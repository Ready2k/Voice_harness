// Operator runs page (FN-03). Pure view-models over existing API payloads + small DOM helpers.
// No ranking or scoring; every state shown is derived from ledger events, never from model text.

export const TERMINAL = new Set(["completed", "cancelled", "failed"]);

export const UNIT_LABEL = {
  pending: "Pending (held)",
  approved: "Approved, not yet released",
  released: "Released to playback gate",
  played: "Played (client acknowledged)",
  stopped: "Stopped by interruption",
  blocked: "Blocked",
  expired: "Expired (decision timeout)",
  discarded: "Discarded",
  superseded: "Superseded by revision",
};

const BLOCKING_ACTIONS = new Set(["deny", "redact", "replace", "escalate"]);

/** Pure: list payload -> rows (newest first as served). */
export function runRows(payload) {
  return payload.items.map((r) => ({
    runId: r.run_id,
    state: r.state,
    terminal: TERMINAL.has(r.state),
    scenario: r.scenario_id ? r.scenario_id + "@" + r.scenario_version : "unknown",
    policy: r.policy_profile ?? "unknown",
    startedAt: r.started_at ?? null,
  }));
}

/** Pure: scenarios payload -> launchable options (only runnable definitions). */
export function launchOptions(payload) {
  return payload.items
    .filter((s) => s.runnable)
    .map((s) => ({ id: s.scenario_id, version: s.version, label: s.scenario_id + " v" + s.version, summary: s.definition?.goal ?? "" }));
}

/** Pure: body of POST /api/v1/runs for a scenario choice. */
export function launchBody(option, policyProfile = "strict-v1") {
  return { scenario_id: option.id, scenario_version: option.version, policy_profile: policyProfile };
}

/** Pure: append a page of events without duplicates, ordered by sequence. */
export function mergeEvents(existing, incoming) {
  const bySequence = new Map(existing.map((e) => [e.sequence, e]));
  for (const e of incoming) bySequence.set(e.sequence, e);
  return [...bySequence.values()].sort((a, b) => a.sequence - b.sequence);
}

const TOOL_STEPS = {
  "tool.assembling": "assembling",
  "tool.requested": "requested",
  "tool.authorized": "authorized",
  "tool.denied": "denied",
  "tool.executing": "executing",
  "tool.completed": "completed",
  "tool.failed": "failed",
  "tool.abandoned": "abandoned",
  "tool.cancelled_before_execution": "cancelled before execution",
  "tool.outcome_unknown": "outcome unknown",
  "tool.reconciled": "reconciled",
  "tool.result_sent": "result sent to provider",
  "tool.result_delivery_failed": "result delivery failed",
};

/** Pure: every unit's state, derived only from ledger events. */
export function unitStates(events) {
  const units = new Map();
  const ensure = (id) => {
    if (!units.has(id)) units.set(id, { unitId: id, state: "pending", transcript: "", reasons: [], safeText: null });
    return units.get(id);
  };
  for (const e of events) {
    const c = e.content;
    switch (e.type) {
      case "audio.output":
        Object.assign(ensure(c.unit_id), { transcript: c.transcript, sequence: e.sequence, responseId: c.response_id });
        break;
      case "decision":
        if (c.target !== "audio_chunk" || !c.unit_id) break;
        {
          const u = ensure(c.unit_id);
          if (BLOCKING_ACTIONS.has(c.action)) {
            u.state = "blocked";
            u.reasons = c.reason_codes ?? [];
            u.safeText = c.safe_text ?? null;
            u.action = c.action;
          } else if (c.action === "allow" && u.state === "pending") u.state = "approved";
        }
        break;
      case "playback.released":
        ensure(c.unit_id).state = "released";
        break;
      case "playback.ack":
        if (ensure(c.unit_id).state === "released") units.get(c.unit_id).state = "played";
        break;
      case "playback.stopped":
        ensure(c.unit_id).state = "stopped";
        break;
      case "audio.decision_timeout":
        ensure(c.unit_id).state = "expired";
        for (const id of c.discarded_unit_ids ?? []) if (id !== c.unit_id) ensure(id).state = "discarded";
        break;
      case "audio.backpressure":
        for (const id of c.discarded_unit_ids ?? []) ensure(id).state = "discarded";
        break;
      case "audio.approval_replaced":
        ensure(c.old_unit_id).state = "superseded";
        break;
      case "transcript.revised":
        if (units.has(c.unit_id)) units.get(c.unit_id).revised = c.new;
        break;
      default:
    }
  }
  return [...units.values()].map((u) => ({ ...u, label: UNIT_LABEL[u.state] ?? u.state }));
}

/** Pure: transcript lines (customer from the simulator, assistant from generated audio units). */
export function transcript(events) {
  const states = new Map(unitStates(events).map((u) => [u.unitId, u]));
  const lines = [];
  for (const e of events) {
    if (e.type === "simulator.utterance_generated") {
      lines.push({ sequence: e.sequence, speaker: "customer", text: e.content.output_text, state: null });
    } else if (e.type === "audio.output") {
      const u = states.get(e.content.unit_id);
      const withheld = u.state === "blocked" || u.state === "expired" || u.state === "discarded";
      lines.push({
        sequence: e.sequence,
        speaker: "assistant",
        text: e.content.transcript,
        state: u.state,
        stateLabel: u.label,
        generatedButNotReleased: withheld,
        note: u.state === "blocked" ? (u.reasons.join(", ") + (u.safeText ? "; safe text: " + u.safeText : "")) : null,
      });
    }
  }
  return lines;
}

/** Pure: tool lifecycle grouped by call id, steps in ledger order. */
export function toolCalls(events) {
  const calls = new Map();
  for (const e of events) {
    const step = TOOL_STEPS[e.type];
    if (!step) continue;
    const id = e.content.call_id;
    if (!calls.has(id)) calls.set(id, { callId: id, tool: null, steps: [], final: null, errorCode: null });
    const call = calls.get(id);
    if (e.content.tool) call.tool = e.content.tool;
    call.steps.push({ sequence: e.sequence, step });
    if (e.content.error_code) call.errorCode = e.content.error_code;
    if (["completed", "denied", "failed", "abandoned", "outcome unknown", "cancelled before execution"].includes(step)) call.final = step;
  }
  return [...calls.values()];
}

/** Pure: confirmation events grouped by request, in ledger order. */
export function confirmations(events) {
  const groups = new Map();
  for (const e of events) {
    if (!e.type.startsWith("confirmation.") && e.type !== "customer.confirmation_observed") continue;
    const id = e.content.request_id ?? e.content.confirmation_id ?? "unknown";
    if (!groups.has(id)) groups.set(id, { requestId: id, steps: [], outcome: "pending" });
    const g = groups.get(id);
    g.steps.push({ sequence: e.sequence, step: e.type.replace(/^(confirmation|customer)\./, "") });
    if (e.type === "confirmation.consumed") g.outcome = "consumed";
    else if (e.type === "confirmation.rejected") g.outcome = "rejected: " + (e.content.reason_code ?? "unspecified");
    else if (e.type === "confirmation.revoked") g.outcome = "revoked";
  }
  return [...groups.values()];
}

/** Pure: decisions (tool, audio, ...) for the guardrail table. */
export function decisions(events) {
  return events
    .filter((e) => e.type === "decision")
    .map((e) => ({
      sequence: e.sequence,
      target: e.content.target,
      action: e.content.action,
      blocking: BLOCKING_ACTIONS.has(e.content.action) || e.content.action === "hold",
      policy: e.content.policy_id + "@" + e.content.policy_version,
      reasons: e.content.reason_codes ?? [],
      latencyMs: e.content.latency_ms,
    }));
}

/** Pure: metrics observations -> rows; null values keep their stated reason. */
export function metricRows(payload) {
  return (payload?.observations ?? []).map((o) => ({
    name: o.metric ?? o.name,
    unit: o.unit ?? "",
    value: o.value !== null && typeof o.value === "object"
      ? Object.entries(o.value).map(([k, v]) => k + " " + v).join(", ")
      : (o.value ?? null),
    reason: o.value == null ? (o.exclusion_reason ?? "not measured") : null,
  }));
}

/** Pure: holds for one run. */
export function runHolds(payload, now = Date.now() / 1000) {
  return payload.items.map((h) => ({
    holdId: h.hold_id,
    owner: h.owner_id,
    scope: h.scope_type,
    findings: h.finding_ids,
    state: h.released_at != null ? "released" : h.expires_at <= now ? "expired" : "active",
    expiresAt: h.expires_at,
  }));
}

/** Pure: one-screen summary, including the pending/blocked/released tally. */
export function runView(run, events, metrics, holds) {
  const units = unitStates(events);
  const tally = { pending: 0, blocked: 0, released: 0 };
  for (const u of units) {
    if (u.state === "blocked") tally.blocked += 1;
    else if (["released", "played"].includes(u.state)) tally.released += 1;
    else if (["pending", "approved"].includes(u.state)) tally.pending += 1;
  }
  return {
    runId: run.run_id,
    state: run.state,
    terminal: TERMINAL.has(run.state),
    scenario: (run.scenario?.scenario_id ?? "unknown") + "@" + (run.scenario?.version ?? "?"),
    termination: run.termination_reason ?? null,
    eventCount: events.length,
    tally,
    units,
    transcript: transcript(events),
    toolCalls: toolCalls(events),
    confirmations: confirmations(events),
    decisions: decisions(events),
    metrics: metricRows(metrics),
    holds: holds ? runHolds(holds) : [],
  };
}

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text; // textContent only: API data is never parsed as HTML
  return node;
}

function table(headers, rows) {
  const t = el("table");
  const head = el("tr");
  for (const h of headers) head.append(el("th", "", h));
  t.append(head);
  for (const row of rows) {
    const tr = el("tr");
    for (const cell of row) tr.append(el("td", "", cell === null || cell === undefined ? "" : String(cell)));
    t.append(tr);
  }
  return t;
}

function section(title, ...children) {
  const s = el("section", "panel");
  s.append(el("h2", "", title), ...children);
  return s;
}

export function renderRunList(root, rows, onOpen) {
  root.replaceChildren();
  if (!rows.length) return root.append(el("p", "muted", "No runs yet."));
  const t = el("table");
  const head = el("tr");
  for (const h of ["Run", "State", "Scenario", "Policy", "Started"]) head.append(el("th", "", h));
  t.append(head);
  for (const r of rows) {
    const tr = el("tr");
    const link = el("button", "link", r.runId);
    link.type = "button";
    link.addEventListener("click", () => onOpen(r.runId));
    const first = el("td");
    first.append(link);
    tr.append(first, el("td", "state state-" + r.state, r.state), el("td", "", r.scenario), el("td", "", r.policy), el("td", "", r.startedAt ?? ""));
    t.append(tr);
  }
  root.append(t);
}

export function renderRun(root, view) {
  root.replaceChildren();
  const head = el("div", "run-head");
  head.append(el("h2", "", "Run " + view.runId), el("span", "badge state-" + view.state, view.state));
  if (view.termination) head.append(el("span", "muted", "ended: " + view.termination));
  root.append(head);
  const tally = el("div", "tally");
  for (const [name, value] of Object.entries(view.tally)) {
    const box = el("div", "tile tile-" + name);
    box.append(el("strong", "", String(value)), el("span", "", name));
    tally.append(box);
  }
  tally.append(el("div", "tile", ""));
  tally.lastChild.append(el("strong", "", String(view.eventCount)), el("span", "", "events"));
  root.append(tally);

  const lines = el("div", "transcript");
  for (const l of view.transcript) {
    const row = el("div", "line line-" + l.speaker + (l.generatedButNotReleased ? " withheld" : ""));
    row.append(el("span", "speaker", l.speaker));
    row.append(el("span", "text", l.text));
    if (l.stateLabel) row.append(el("span", "chip chip-" + l.state, l.stateLabel));
    if (l.generatedButNotReleased) row.append(el("span", "muted", "generated, never played"));
    if (l.note) row.append(el("span", "muted", l.note));
    lines.append(row);
  }
  root.append(section("Transcript", view.transcript.length ? lines : el("p", "muted", "No transcript yet.")));
  root.append(section("Tool lifecycle", table(["Call", "Tool", "Steps", "Final", "Error"], view.toolCalls.map((c) => [c.callId, c.tool ?? "", c.steps.map((s) => s.step).join(" > "), c.final ?? "in progress", c.errorCode ?? ""]))));
  root.append(section("Guardrail decisions", table(["#", "Target", "Action", "Policy", "Reasons", "ms"], view.decisions.map((d) => [d.sequence, d.target, d.action, d.policy, d.reasons.join(", "), d.latencyMs]))));
  root.append(section("Confirmations", view.confirmations.length ? table(["Request", "Steps", "Outcome"], view.confirmations.map((c) => [c.requestId, c.steps.map((s) => s.step).join(" > "), c.outcome])) : el("p", "muted", "None.")));
  root.append(section("Preservation holds", view.holds.length ? table(["Hold", "Owner", "Scope", "State"], view.holds.map((h) => [h.holdId, h.owner, h.scope + (h.findings.length ? " (" + h.findings.length + " findings)" : ""), h.state])) : el("p", "muted", "None.")));
  root.append(section("Metrics", table(["Metric", "Value", "Unit", "If not measured"], view.metrics.map((m) => [m.name, m.value, m.unit, m.reason ?? ""]))));
}

export async function api(token, path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Authorization: "Bearer " + token, ...(options.headers ?? {}) },
  });
  if (!response.ok) {
    let code = "HTTP " + response.status;
    try { code = (await response.json()).error.code; } catch { /* keep status */ }
    throw new Error(code);
  }
  return response.json();
}

/** All events for a run, following next_cursor. */
export async function fetchEvents(token, runId, after = 0) {
  const items = [];
  let cursor = after;
  for (let page = 0; page < 50; page += 1) {
    const body = await api(token, "/api/v1/runs/" + encodeURIComponent(runId) + "/events?limit=1000&cursor=" + cursor);
    items.push(...body.items);
    if (body.next_cursor == null) break;
    cursor = body.next_cursor;
  }
  return items;
}
