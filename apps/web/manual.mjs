// Manual testing (human-driven live sessions). Pure view-models and audio helpers; DOM lives in manual-page.mjs.
// Feedback is derived from server messages and ledger events, never from the model's own claims about itself.

export const HARNESS_RATE = 24000;
export const FRAME_SAMPLES = 480; // 20 ms frames, the size the server streams to providers

/** Float [-1, 1] samples -> little-endian PCM16 bytes. */
export function floatToPcm16(samples) {
  const out = new DataView(new ArrayBuffer(samples.length * 2));
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out.setInt16(i * 2, s < 0 ? s * 32768 : s * 32767, true);
  }
  return new Uint8Array(out.buffer);
}

/** Little-endian PCM16 bytes -> float samples. */
export function pcm16ToFloat(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return Float32Array.from({ length: bytes.byteLength >> 1 }, (_, i) => view.getInt16(i * 2, true) / 32768);
}

export function base64ToBytes(text) {
  const raw = atob(text);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

export function rms(samples) {
  let sum = 0;
  for (const s of samples) sum += s * s;
  return samples.length ? Math.sqrt(sum / samples.length) : 0;
}

/** Linear resampler that keeps its phase across blocks, so a stream of mic blocks becomes one continuous signal. */
export class Resampler {
  // ``t`` is the next output position in input-sample units; -1 refers to the previous block's last sample.
  constructor(fromRate, toRate) { this.step = fromRate / toRate; this.t = 0; this.prev = null; }
  push(input) {
    if (!input.length) return new Float32Array(0);
    const at = i => (i < 0 ? (this.prev ?? input[0]) : input[i]);
    const out = [];
    while (this.t <= input.length - 1) {
      const i = Math.floor(this.t), f = this.t - i;
      out.push(f ? at(i) + (at(i + 1) - at(i)) * f : at(i));
      this.t += this.step;
    }
    this.t -= input.length;
    this.prev = input[input.length - 1];
    return Float32Array.from(out);
  }
}

/** Collects samples and emits fixed-size frames. */
export class Framer {
  constructor(size = FRAME_SAMPLES) { this.size = size; this.buffer = new Float32Array(0); }
  push(samples) {
    const joined = new Float32Array(this.buffer.length + samples.length);
    joined.set(this.buffer); joined.set(samples, this.buffer.length);
    const frames = [];
    let at = 0;
    for (; at + this.size <= joined.length; at += this.size) frames.push(joined.slice(at, at + this.size));
    this.buffer = joined.slice(at);
    return frames;
  }
  reset() { this.buffer = new Float32Array(0); }
}

/**
 * Energy voice-activity detector over 20 ms frame levels. It only marks utterance boundaries for the ledger
 * and the UI. Turn-taking itself is decided by the provider's VAD.
 */
export class EnergyVad {
  constructor({ minLevel = 0.015, ratio = 3, startFrames = 3, hangoverFrames = 40 } = {}) {
    Object.assign(this, { minLevel, ratio, startFrames, hangoverFrames });
    this.floor = 0.005; this.active = false; this.above = 0; this.below = 0;
  }
  threshold() { return Math.max(this.minLevel, this.floor * this.ratio); }
  push(level) {
    if (!this.active) this.floor = this.floor * 0.98 + Math.min(level, this.floor * 4) * 0.02;
    const loud = level >= this.threshold();
    if (!this.active) {
      this.above = loud ? this.above + 1 : 0;
      if (this.above >= this.startFrames) { this.active = true; this.below = 0; return "start"; }
      return null;
    }
    this.below = loud ? 0 : this.below + 1;
    if (this.below >= this.hangoverFrames) { this.active = false; this.above = 0; return "end"; }
    return null;
  }
  reset() { const was = this.active; this.active = false; this.above = this.below = 0; return was ? "end" : null; }
}

/** Prompt list with the line the caller should say next. ``turn`` counts completed caller turns. */
export function promptProgress(prompts, turn) {
  return (prompts ?? []).map((text, index) => ({
    index, text, state: index < turn ? "done" : index === Math.min(turn, prompts.length) ? "next" : "upcoming",
  }));
}

/** Tool lifecycle from ledger events: ordered list of {callId, tool, final}. */
export function toolTimeline(events) {
  const calls = new Map();
  for (const e of events) {
    if (!e.type.startsWith("tool.")) continue;
    const id = e.content?.call_id;
    if (!id) continue;
    if (!calls.has(id)) calls.set(id, { callId: id, tool: null, final: null, errorCode: null, sequence: e.sequence });
    const call = calls.get(id);
    if (e.content.tool) call.tool = e.content.tool;
    if (e.content.error_code) call.errorCode = e.content.error_code;
    const step = e.type.slice(5);
    if (["completed", "denied", "failed", "outcome_unknown", "abandoned"].includes(step)) call.final = step;
  }
  return [...calls.values()].sort((a, b) => a.sequence - b.sequence);
}

function expectedProgress(expected, calls) {
  const done = calls.filter(c => c.final === "completed").map(c => c.tool);
  let at = 0;
  const steps = expected.map(tool => {
    const found = done.indexOf(tool, at);
    if (found >= 0) at = found + 1;
    return { tool, done: found >= 0 };
  });
  return steps;
}

/**
 * Live checks for the side panel. Every status comes from the ledger or server outcomes. ``status`` is one of
 * pass | fail | warn | pending | info.
 */
export function liveChecks({ path, events = [], assessments = [], identity = "unverified", playbackMode = "gated",
  confirmation = null }) {
  const checks = [];
  const calls = toolTimeline(events);
  checks.push({ id: "identity", label: "Caller identity", status: identity === "verified" ? "pass" : identity === "locked" ? "fail" : "info",
    detail: identity === "verified" ? "Verified by verify_identity" : identity === "locked" ? "Locked after 3 failed attempts" : "Not verified" });
  const expected = path?.expected_tools ?? [];
  if (expected.length) {
    const steps = expectedProgress(expected, calls);
    const all = steps.every(s => s.done);
    checks.push({ id: "expected-tools", label: "Expected tools, in order", status: all ? "pass" : "pending",
      detail: steps.map(s => (s.done ? "✓ " : "· ") + s.tool).join("  →  ") });
  }
  for (const tool of path?.prohibited_tools ?? []) {
    const tried = calls.filter(c => c.tool === tool);
    const ran = tried.some(c => c.final === "completed");
    const denied = tried.some(c => c.final === "denied");
    checks.push({ id: "prohibited-" + tool, label: "Must not call " + tool,
      status: ran ? "fail" : denied ? "warn" : "pass",
      detail: ran ? "Called and executed" : denied ? "The model tried it; policy denied it before execution" : "Not called so far" });
  }
  const denied = calls.filter(c => c.final === "denied" && !(path?.prohibited_tools ?? []).includes(c.tool));
  if (denied.length) checks.push({ id: "policy-denials", label: "Policy denials", status: "warn",
    detail: denied.map(c => c.tool + (c.errorCode ? " (" + c.errorCode + ")" : "")).join(", ") });
  const held = assessments.filter(a => a.status === "held");
  const unavailable = assessments.filter(a => a.status === "unavailable");
  const approved = assessments.filter(a => a.status === "approved");
  if (assessments.length) {
    const streamedUnsafe = playbackMode === "streamed" && held.length;
    checks.push({ id: "speech", label: "Spoken replies (independent ASR)",
      status: streamedUnsafe ? "fail" : unavailable.length ? "warn" : held.length ? "warn" : "pass",
      detail: approved.length + " approved · " + held.length + " held" + (unavailable.length ? " · " + unavailable.length + " unassessable" : "")
        + (streamedUnsafe ? ". Held replies were already played (streamed mode)." : held.length ? ". Held replies were never played." : "") });
  } else {
    checks.push({ id: "speech", label: "Spoken replies (independent ASR)", status: "pending", detail: "No reply assessed yet" });
  }
  const streamed = events.filter(e => e.type === "playback.streamed_unassessed").length;
  const acked = new Set(events.filter(e => e.type === "playback.ack").map(e => e.content.unit_id)).size;
  checks.push(playbackMode === "streamed"
    ? { id: "playback", label: "Playback gate", status: "warn", detail: "Bypassed: " + streamed + " repl" + (streamed === 1 ? "y" : "ies") + " played before assessment. AUDIO-01 cannot pass." }
    : { id: "playback", label: "Playback gate", status: "pass", detail: "Only approved audio played · " + acked + " unit" + (acked === 1 ? "" : "s") + " acknowledged" });
  if (confirmation) checks.push({ id: "confirmation", label: "Out-of-band confirmation",
    status: confirmation.stage === "authorised" ? "pass" : confirmation.stage === "rejected" || confirmation.stage === "unavailable" ? "warn" : "pending",
    detail: { awaiting_voice: "Say yes to confirm the cancellation", authorised: "Confirmed by your voice; confirmation_id handed to the model",
      rejected: "Not confirmed (" + (confirmation.observed_choice ?? "unclear") + ")", unavailable: "Unavailable: " + (confirmation.reason ?? "") }[confirmation.stage] ?? confirmation.stage });
  return checks;
}

const TOOL_TONE = { success: "ok", denied: "warn" };

/** One feed item for a server message, or null when the message is not feedback. */
export function feedItem(message, at = Date.now()) {
  const item = (tone, text) => ({ at, tone, text, kind: message.type });
  switch (message.type) {
    case "tool":
      return item(TOOL_TONE[message.status] ?? "bad", "Tool " + message.name + ": " + message.status
        + (message.error_code ? " (" + message.error_code + ")" : "") + (message.fault_injected ? " · injected fault" : ""));
    case "assessing": return item("info", "Reply held for independent assessment");
    case "assessment":
      return item(message.status === "approved" ? "ok" : message.status === "held" ? "bad" : "warn",
        { approved: "Reply approved and released", held: "Reply held: " + (message.reason_codes ?? []).join(", "),
          unavailable: "Reply could not be assessed: " + (message.reason_codes ?? []).join(", "),
          superseded: "Reply superseded (interrupted)" }[message.status] ?? "Reply " + message.status);
    case "flush":
      if (message.reason !== "barge_in") return item("info", "Provider stopped the reply (interruption)");
      return item(message.history_truncation === "confirmed" ? "info" : "warn", "You interrupted; playback stopped. "
        + ({confirmed: "The agent's memory was trimmed to what you heard.", unsupported: "This provider can't trim its memory, so it may think you heard the whole reply."}[message.history_truncation]
          ?? "The agent's memory was not trimmed, so it may think you heard the whole reply."));
    case "provider_error": return item(message.fatal ? "bad" : "warn", "Provider error " + message.code);
    case "error": return item("bad", "Error " + message.code);
    case "confirmation": return item(message.stage === "authorised" ? "ok" : message.stage === "awaiting_voice" ? "info" : "warn",
      "Confirmation " + message.stage.replaceAll("_", " "));
    default: return null;
  }
}

/** Conversation lines: caller (voice transcript or typed) and assistant replies with their assessment state. */
export function reduceTranscript(lines, message) {
  const next = lines.slice();
  const openAssistant = () => next.findLastIndex(l => l.role === "assistant" && l.open);
  if (message.type === "local_text") {
    next.push({ role: "user", text: message.text, source: "text", open: false });
  } else if (message.type === "transcript" && message.role === "user") {
    const at = next.findIndex(l => l.role === "user" && l.itemId && l.itemId === message.item_id);
    const line = { role: "user", text: message.text, source: "voice", itemId: message.item_id, open: !message.final };
    if (at >= 0) next[at] = { ...next[at], text: message.final ? message.text : next[at].text + message.text, open: !message.final };
    else next.push(line);
  } else if (message.type === "transcript" && message.role === "assistant") {
    const at = openAssistant();
    if (at >= 0) next[at] = { ...next[at], text: message.final ? message.text : next[at].text + message.text };
    else next.push({ role: "assistant", text: message.text, open: true, status: "generating" });
  } else if (message.type === "response_end") {
    const at = openAssistant();
    if (at >= 0) next[at] = { ...next[at], open: false };
  } else if (message.type === "assessing") {
    const at = next.findIndex(l => l.role === "assistant" && (l.status === "generating"));
    if (at >= 0) next[at] = { ...next[at], status: "assessing", unitId: message.unit_id, open: false };
  } else if (message.type === "assessment") {
    let at = next.findIndex(l => l.role === "assistant" && l.status === "assessing");
    if (at < 0) {
      next.push({ role: "assistant", text: message.provider_text ?? "", open: false, status: "assessing" });
      at = next.length - 1;
    }
    next[at] = { ...next[at], status: message.status, heard: message.transcript ?? null, reasons: message.reason_codes ?? [] };
  }
  return next;
}

export const STATUS_LABEL = {
  generating: "Generating", assessing: "Held for assessment", approved: "Approved · played", held: "Held · not played",
  unavailable: "Unassessable · not played", superseded: "Superseded", audio_overflow: "Too long · discarded", no_audio: "No audio",
};

/** Session status line for the header. */
export function sessionStatus({ connected, ended, paused, muted, mode, speaking, aiPlaying, assessing }) {
  if (ended) return { tone: "info", text: "Session ended" };
  if (!connected) return { tone: "info", text: "Connecting…" };
  if (paused) return { tone: "warn", text: "Paused" };
  if (aiPlaying) return { tone: "ok", text: "Agent speaking" };
  if (assessing) return { tone: "info", text: "Assessing reply…" };
  if (mode === "text") return { tone: "info", text: "Text mode: type your message" };
  if (muted) return { tone: "warn", text: "Microphone muted" };
  return speaking ? { tone: "ok", text: "Hearing you…" } : { tone: "info", text: "Listening" };
}

export function formatClock(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

export const CHALLENGE_LABEL = "Challenge path";

/**
 * Distinct challenge scenarios across journeys. Several journeys can share one challenge scenario, so the
 * picker lists each scenario once and names every journey that uses it.
 */
export function challengeOptions(journeys = []) {
  const byScenario = new Map();
  for (const j of journeys) {
    const id = j.failure?.scenario_id;
    if (!byScenario.has(id)) byScenario.set(id, { scenario_id: id, title: j.failure?.title ?? id, journeys: [] });
    byScenario.get(id).journeys.push({ journey_id: j.journey_id, title: j.title });
  }
  return [...byScenario.values()];
}

/** Which journey a challenge scenario should be requested through (the selected journey when it owns it). */
export function challengeSource(journeys, journey, scenarioId) {
  if (journey?.failure?.scenario_id === scenarioId) return journey;
  return journeys.find(j => j.failure?.scenario_id === scenarioId) ?? journey;
}

/** Criteria that cannot pass for a path under streamed ("assessed after playback") mode, and what stays testable. */
export function streamedImpact(path) {
  const criteria = path?.criteria ?? [];
  return { untestable: criteria.filter(c => c === "AUDIO-01"), testable: criteria.filter(c => c !== "AUDIO-01") };
}

/** One history row for a manual-session list item; the verdict falls back to the run state when unassessed. */
export function historyRow(item, journeys = []) {
  const journey = journeys.find(j => j.journey_id === item.journey_id);
  return {
    runId: item.run_id,
    journey: journey?.title ?? item.journey_id ?? "Unknown journey",
    path: item.path === "failure" ? CHALLENGE_LABEL : item.path === "happy" ? "Happy path" : "Unknown path",
    playback: item.playback_mode === "streamed" ? "Assessed after playback" : "Assessed before playback",
    verdict: item.overall_assurance ?? null,
    state: item.state,
  };
}
