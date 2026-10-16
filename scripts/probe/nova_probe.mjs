// Phase 0 live probe for Amazon Nova Sonic on Bedrock (spec section 3 procedure).
// Usage: node nova_probe.mjs [--region us-east-1] [--model amazon.nova-2-5-sonic] [--speech-wav f.wav]
// Credentials come from the environment (see scripts/aws-session.sh). Writes a redacted raw
// event log plus observations JSON to artifacts/phase0/; audio payloads are stored as hash+length only.
import { BedrockRuntimeClient, InvokeModelWithBidirectionalStreamCommand } from "@aws-sdk/client-bedrock-runtime";
import { createHash, randomUUID } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync, appendFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const region = arg("region", "us-east-1");
const modelId = arg("model", "amazon.nova-2-5-sonic");
const speechWav = arg("speech-wav", null);
const outDir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "artifacts", "phase0");
mkdirSync(outDir, { recursive: true });
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const logPath = join(outDir, `nova-probe-${stamp}.jsonl`);
const obsPath = join(outDir, `nova-observations-${stamp}.json`);

let seq = 0;
const evidence = {}; // row -> [event ids]
const notes = {};    // row -> [strings]
const mark = (row, id, note) => { (evidence[row] ??= []).push(id); if (note) (notes[row] ??= []).push(note); };
const note = (row, n) => (notes[row] ??= []).push(n);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sha = (b) => createHash("sha256").update(b).digest("hex").slice(0, 16);

function log(session, dir, type, detail) {
  const id = `nova_ev_${++seq}`;
  appendFileSync(logPath, JSON.stringify({ id, t: new Date().toISOString(), session, dir, type, detail }) + "\n");
  return id;
}

class Queue {
  constructor() { this.items = []; this.waiters = []; this.closed = false; }
  push(x) { const w = this.waiters.shift(); w ? w({ value: x, done: false }) : this.items.push(x); }
  close() { this.closed = true; this.waiters.splice(0).forEach((w) => w({ value: undefined, done: true })); }
  [Symbol.asyncIterator]() { return { next: () => this.items.length ? Promise.resolve({ value: this.items.shift(), done: false }) : this.closed ? Promise.resolve({ done: true }) : new Promise((r) => this.waiters.push(r)) }; }
}

const TOOL = { toolSpec: { name: "get_account_summary", description: "Return a synthetic account summary for an account id.",
  inputSchema: { json: JSON.stringify({ type: "object", properties: { account_id: { type: "string" } }, required: ["account_id"], additionalProperties: false }) } } };

async function runSession(label, { textTurn, audioSilence, tool, badToolResult, wav }) {
  const client = new BedrockRuntimeClient({ region });
  const q = new Queue();
  const send = (event) => { const id = log(label, "out", Object.keys(event.event)[0], summarize(event.event)); q.push({ chunk: { bytes: Buffer.from(JSON.stringify(event)) } }); return id; };
  const promptName = randomUUID();
  const st = { toolUse: null, assistantEnded: 0, errors: [], audioOut: 0, interrupted: false, usage: [], ended: false };
  const t0 = Date.now();

  send({ event: { sessionStart: { inferenceConfiguration: { maxTokens: 1024, topP: 0.9, temperature: 0.2 }, turnDetectionConfiguration: { endpointingSensitivity: "MEDIUM" } } } });
  send({ event: { promptStart: { promptName, textOutputConfiguration: { mediaType: "text/plain" },
    audioOutputConfiguration: { mediaType: "audio/lpcm", sampleRateHertz: 24000, sampleSizeBits: 16, channelCount: 1, voiceId: "matthew", encoding: "base64", audioType: "SPEECH" },
    ...(tool ? { toolUseOutputConfiguration: { mediaType: "application/json" }, toolConfiguration: { tools: [TOOL] } } : {}) } } });
  const sys = randomUUID();
  send({ event: { contentStart: { promptName, contentName: sys, type: "TEXT", interactive: false, role: "SYSTEM", textInputConfiguration: { mediaType: "text/plain" } } } });
  send({ event: { textInput: { promptName, contentName: sys, content: "You are a concise banking test assistant. Use tools when asked about accounts. Reply in one short sentence." } } });
  send({ event: { contentEnd: { promptName, contentName: sys } } });

  // Nova requires an open audio input content for the whole prompt (text-only turns are rejected).
  const aud = randomUUID();
  send({ event: { contentStart: { promptName, contentName: aud, type: "AUDIO", interactive: true, role: "USER", audioInputConfiguration: { mediaType: "audio/lpcm", sampleRateHertz: 16000, sampleSizeBits: 16, channelCount: 1, audioType: "SPEECH", encoding: "base64" } } } });
  const silence = Buffer.alloc(640).toString("base64"); // 20 ms at 16 kHz s16le
  st.wavChunks = []; st.pumped = 0;
  const pump = setInterval(() => {
    const c = st.wavChunks.shift() ?? silence;
    const id = log(label, "out", "audioInput", { bytes_b64: c.length, sha: sha(c) });
    q.push({ chunk: { bytes: Buffer.from(JSON.stringify({ event: { audioInput: { promptName, contentName: aud, content: c } } })) } });
    if (++st.pumped === 1) st.firstAudioId = id;
  }, 20);

  let response;
  try {
    response = await client.send(new InvokeModelWithBidirectionalStreamCommand({ modelId, body: q }));
    mark("auth_reachability", log(label, "in", "stream_open", { region, modelId, httpStatus: response.$metadata?.httpStatusCode, requestId: response.$metadata?.requestId }));
    note("auth_reachability", `HTTP ${response.$metadata?.httpStatusCode}`);
  } catch (e) {
    const id = log(label, "in", "open_error", { name: e.name, message: e.message });
    note("auth_reachability", `open failed: ${e.name}: ${e.message}`);
    clearInterval(pump);
    return { st, id, failed: true };
  }

  const reader = (async () => {
    let role = null, stage = null, cur = null;
    try {
      for await (const part of response.body) {
        if (!part.chunk?.bytes) { log(label, "in", "non_chunk", Object.keys(part)); continue; }
        const ev = JSON.parse(Buffer.from(part.chunk.bytes).toString()).event ?? {};
        const type = Object.keys(ev)[0];
        const id = log(label, "in", type, summarize(ev));
        if (ev.contentStart) { role = ev.contentStart.role; cur = ev.contentStart.type; try { stage = JSON.parse(ev.contentStart.additionalModelFields ?? "{}").generationStage ?? null; } catch { stage = null; } }
        if (ev.textOutput) {
          const text = ev.textOutput.content;
          if (text.includes('"interrupted"')) { st.interrupted = true; mark("cancel_truncate", id, "interrupted marker in textOutput"); }
          mark(role === "USER" ? "transcript_finality" : "transcript_finality", id, `${role}/${stage}`);
          st.sawText = true;
        }
        if (ev.audioOutput) { st.audioOut++; mark("audio_out", id); if (st.audioOut === 1) note("audio_out", `first audio ${Date.now() - t0}ms after open`); }
        if (ev.toolUse) { st.toolUse = ev.toolUse; mark("tool_arguments_and_results", id, `toolUse ${ev.toolUse.toolName} ${ev.toolUse.content}`); }
        if (ev.usageEvent) { st.usage.push(ev.usageEvent); mark("usage_granularity", id); }
        if (ev.contentEnd) {
          if (role === "ASSISTANT" && ev.contentEnd.type === "AUDIO") st.assistantEnded++;
          if (ev.contentEnd.stopReason) { mark("turn_signalling", id, `stopReason=${ev.contentEnd.stopReason} role=${role} type=${ev.contentEnd.type}`); st.stopReasons = [...(st.stopReasons ?? []), ev.contentEnd.stopReason]; }
        }
        if (ev.completionEnd) st.ended = true;
        if (/error|exception/i.test(type)) { st.errors.push(ev); mark("error_handling", id, type); }
      }
    } catch (e) {
      const id = log(label, "in", "stream_error", { name: e.name, message: e.message });
      st.errors.push({ name: e.name, message: e.message }); mark("error_handling", id, `${e.name}: ${e.message}`);
    }
    st.readerDone = true;
  })();

  if (textTurn) {
    const c = randomUUID();
    send({ event: { contentStart: { promptName, contentName: c, type: "TEXT", interactive: true, role: "USER", textInputConfiguration: { mediaType: "text/plain" } } } });
    const id = send({ event: { textInput: { promptName, contentName: c, content: textTurn } } });
    mark("text_in", id);
    send({ event: { contentEnd: { promptName, contentName: c } } });
  }
  if (tool) {
    const dl = Date.now() + 30000;
    while (!st.toolUse && Date.now() < dl && !st.readerDone) await sleep(100);
    if (st.toolUse) {
      const toolUseId = badToolResult ? "bogus-" + randomUUID() : st.toolUse.toolUseId;
      const c = randomUUID();
      send({ event: { contentStart: { promptName, contentName: c, type: "TOOL", interactive: false, role: "TOOL", toolResultInputConfiguration: { toolUseId, type: "TEXT", textInputConfiguration: { mediaType: "text/plain" } } } } });
      const id = send({ event: { toolResult: { promptName, contentName: c, content: JSON.stringify({ status: "success", data: { account_id: "ACC-001", balance: "1234.56", currency: "GBP" } }) } } });
      mark("tool_arguments_and_results", id, badToolResult ? "sent toolResult with bogus toolUseId" : "sent toolResult");
      send({ event: { contentEnd: { promptName, contentName: c } } });
    } else note("tool_arguments_and_results", "no toolUse event within 30s");
  }
  // wait for an assistant audio turn to end
  const dl2 = Date.now() + 30000;
  while (st.assistantEnded < 1 && Date.now() < dl2 && !st.readerDone) await sleep(100);

  if (audioSilence && st.pumped >= 50 && !st.errors.length) mark("audio_in", st.firstAudioId, `${st.pumped} x 20 ms chunks of 16 kHz PCM16 silence accepted without error (recognition not exercised)`);
  if (wav) {
    // barge-in: ask for a long reply, then stream real speech while the model is talking
    const c = randomUUID();
    send({ event: { contentStart: { promptName, contentName: c, type: "TEXT", interactive: true, role: "USER", textInputConfiguration: { mediaType: "text/plain" } } } });
    send({ event: { textInput: { promptName, contentName: c, content: "Please count slowly from one to thirty." } } });
    send({ event: { contentEnd: { promptName, contentName: c } } });
    const base = st.audioOut; const dl3 = Date.now() + 20000;
    while (st.audioOut < base + 5 && Date.now() < dl3) await sleep(50);
    const pcm = readFileSync(wav).subarray(44);
    for (let o = 0; o < pcm.length; o += 640) st.wavChunks.push(pcm.subarray(o, o + 640).toString("base64"));
    await sleep(4000);
  }
  await sleep(1500);
  clearInterval(pump);
  send({ event: { contentEnd: { promptName, contentName: aud } } });
  send({ event: { promptEnd: { promptName } } });
  send({ event: { sessionEnd: {} } });
  q.close();
  await Promise.race([reader, sleep(10000)]);
  return { st, failed: false };
}

function summarize(ev) {
  const [type, body] = Object.entries(ev)[0];
  if (type === "audioOutput" || type === "audioInput") return { bytes_b64: body.content.length, sha: sha(body.content) };
  return JSON.parse(JSON.stringify(body));
}

(async () => {
  console.log(`Probe: ${modelId} in ${region}; log ${logPath}`);
  const meta = { modelId, region, startedAt: new Date().toISOString(), node: process.version };
  const s1 = await runSession("s1", { textTurn: "What is the balance for account ACC-001?", audioSilence: true, tool: true, wav: speechWav });
  console.log("s1:", { toolUse: !!s1.st.toolUse, audioOut: s1.st.audioOut, errors: s1.st.errors.length, usage: s1.st.usage.length });
  // reconnect: fresh session after the first ended, text-only
  const s2 = await runSession("s2", { textTurn: "Say hello.", tool: false });
  if (s2.st.audioOut > 0) mark("reconnect_resume", log("s2", "in", "summary", { audioOut: s2.st.audioOut }), "new session after clean close works; no resume token observed (resume NOT tested)");
  // error: tool result for an unknown toolUseId
  const s3 = await runSession("s3", { textTurn: "What is the balance for account ACC-002?", tool: true, badToolResult: true });
  note("error_handling", `bogus toolUseId: ${s3.st.errors.length} error event(s) observed`);
  if (!speechWav) note("cancel_truncate", "barge-in not exercised: no --speech-wav supplied");
  if (s1.st.usage.length) note("usage_granularity", `usageEvent keys: ${Object.keys(s1.st.usage[0]).join(",")}`);
  writeFileSync(obsPath, JSON.stringify({ meta, evidence, notes,
    flags: { toolCalled: !!s1.st.toolUse, interrupted: s1.st.interrupted, sdk: "@aws-sdk/client-bedrock-runtime@3.1136.0" } }, null, 2));
  console.log("observations:", obsPath);
})().catch((e) => { console.error(e); process.exit(1); });
