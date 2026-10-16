// Phase 0 live probe for the OpenAI Realtime API (spec section 3 procedure).
// Usage: node --env-file=../../.env openai_probe.mjs [--model gpt-realtime-2.1]
// Writes a redacted raw event log and observations JSON to artifacts/phase0/ (audio = hash + length only).
import WebSocket from "ws";
import { createHash } from "node:crypto";
import { writeFileSync, mkdirSync, appendFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const modelId = arg("model", "gpt-realtime-2.1");
const key = process.env.OPENAI_API_KEY;
if (!key) { console.error("OPENAI_API_KEY not set"); process.exit(2); }
const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const outDir = join(root, "artifacts", "phase0");
mkdirSync(outDir, { recursive: true });
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const logPath = join(outDir, `openai-probe-${stamp}.jsonl`);
const obsPath = join(outDir, `openai-observations-${stamp}.json`);

let seq = 0;
const evidence = {}, notes = {};
const mark = (row, id, n) => { (evidence[row] ??= []).push(id); if (n) (notes[row] ??= []).push(n); };
const note = (row, n) => (notes[row] ??= []).push(n);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sha = (s) => createHash("sha256").update(s).digest("hex").slice(0, 16);
const log = (session, dir, type, detail) => {
  const id = `oai_ev_${++seq}`;
  appendFileSync(logPath, JSON.stringify({ id, t: new Date().toISOString(), session, dir, type, detail }) + "\n");
  return id;
};
const redact = (ev) => {
  const e = JSON.parse(JSON.stringify(ev));
  for (const k of ["audio", "delta"]) if (typeof e[k] === "string" && /audio/.test(e.type ?? "") && !/transcript/.test(e.type)) { e[k] = { b64_len: e[k].length, sha: sha(e[k]) }; }
  return e;
};

const TOOL = { type: "function", name: "get_account_summary", description: "Return a synthetic account summary for an account id.",
  parameters: { type: "object", properties: { account_id: { type: "string" } }, required: ["account_id"], additionalProperties: false } };

function connect(label) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const ws = new WebSocket(`wss://api.openai.com/v1/realtime?model=${modelId}`, { headers: { Authorization: `Bearer ${key}` } });
    const s = { ws, label, events: [], t0, audioDeltas: 0, types: new Set(), errors: [], usage: null, resp: [] };
    ws.on("message", (m) => {
      const ev = JSON.parse(m.toString());
      const id = log(label, "in", ev.type, redact(ev));
      s.events.push({ id, ev }); s.types.add(ev.type);
      if (ev.type === "error") s.errors.push({ id, ev });
      if (ev.type === "response.output_audio.delta") { s.audioDeltas++; if (s.audioDeltas === 1) { mark("audio_out", id); s.firstAudioMs = Date.now() - s.sentAt; } }
    });
    ws.on("error", (e) => { log(label, "in", "ws_error", { message: e.message }); reject(e); });
    ws.on("unexpected-response", (_r, res) => { const id = log(label, "in", "http_error", { status: res.statusCode }); note("auth_reachability", `HTTP ${res.statusCode}`); reject(new Error(`HTTP ${res.statusCode} ${id}`)); });
    ws.on("open", () => { const id = log(label, "in", "ws_open", { ms: Date.now() - t0 }); mark("auth_reachability", id, "websocket open"); resolve(s); });
  });
}
const send = (s, ev) => { const id = log(s.label, "out", ev.type, redact(ev)); s.ws.send(JSON.stringify(ev)); return id; };
const waitFor = async (s, pred, ms = 30000) => { const dl = Date.now() + ms; while (Date.now() < dl) { const f = s.events.find((e) => pred(e.ev)); if (f) return f; await sleep(50); } return null; };

async function textTurn(s, text) {
  s.sentAt = Date.now();
  send(s, { type: "conversation.item.create", item: { type: "message", role: "user", content: [{ type: "input_text", text }] } });
  return send(s, { type: "response.create" });
}

(async () => {
  const meta = { modelId, startedAt: new Date().toISOString(), node: process.version };
  let flags = { toolCalled: false };
  // --- s1: session config, text turn, tool round trip, audio in, cancel/truncate, error
  const s1 = await connect("s1");
  const created = await waitFor(s1, (e) => e.type === "session.created", 10000);
  if (created) { meta.sessionCreated = { model: created.ev.session?.model, id: created.ev.session?.id }; mark("model_discovery", created.id, `session.created model=${created.ev.session?.model}`); }
  send(s1, { type: "session.update", session: { type: "realtime", model: modelId, output_modalities: ["audio"],
    instructions: "You are a concise banking test assistant. Use tools when asked about accounts. Reply in one short sentence.",
    audio: { input: { format: { type: "audio/pcm", rate: 24000 }, turn_detection: null, transcription: { model: "gpt-4o-transcribe" } },
             output: { format: { type: "audio/pcm", rate: 24000 }, voice: "marin" } },
    tools: [TOOL], tool_choice: "auto" } });
  const upd = await waitFor(s1, (e) => e.type === "session.updated", 10000);
  if (upd) { mark("input_codecs_rates", upd.id, JSON.stringify(upd.ev.session?.audio?.input?.format)); mark("output_codecs_rates", upd.id, JSON.stringify(upd.ev.session?.audio?.output?.format)); mark("turn_signalling", upd.id, "session.updated accepted turn_detection=null"); }

  await textTurn(s1, "What is the balance for account ACC-001?");
  const fc = await waitFor(s1, (e) => e.type === "response.done" && e.response?.output?.some((o) => o.type === "function_call"));
  if (fc) {
    const call = fc.ev.response.output.find((o) => o.type === "function_call");
    flags.toolCalled = true; mark("tool_arguments_and_results", fc.id, `function_call ${call.name} ${call.arguments}`);
    send(s1, { type: "conversation.item.create", item: { type: "function_call_output", call_id: call.call_id, output: JSON.stringify({ account_id: "ACC-001", balance: "1234.56", currency: "GBP" }) } });
    mark("tool_arguments_and_results", send(s1, { type: "response.create" }), "function_call_output delivered then response.create");
  } else note("tool_arguments_and_results", "no function_call within 30s");
  const done2 = await waitFor(s1, (e) => e.type === "response.done" && e.response?.output?.some((o) => o.type === "message") , 30000);
  if (done2) {
    mark("turn_signalling", done2.id, `response.done status=${done2.ev.response.status}`);
    if (done2.ev.response.usage) { mark("usage_granularity", done2.id, `response.done usage keys: ${Object.keys(done2.ev.response.usage).join(",")}; ${JSON.stringify(done2.ev.response.usage.input_token_details ?? {})}`); }
    else note("usage_granularity", "response.done carried no usage");
  }
  const tr = s1.events.find((e) => e.ev.type === "response.output_audio_transcript.done");
  if (tr) mark("transcript_finality", tr.id, "response.output_audio_transcript.delta then .done");
  note("audio_alignment", "no word/audio timestamps in observed events");

  // audio in: 200 ms of 24 kHz PCM16 silence appended and committed (manual turn)
  const silence = Buffer.alloc(24000 * 2 * 0.2).toString("base64");
  const a1 = send(s1, { type: "input_audio_buffer.append", audio: silence });
  send(s1, { type: "input_audio_buffer.commit" });
  const com = await waitFor(s1, (e) => e.type === "input_audio_buffer.committed", 10000);
  if (com) mark("audio_in", a1, "append + commit accepted (input_audio_buffer.committed); recognition not exercised");
  else note("audio_in", `commit not acknowledged: ${s1.errors.map((e) => e.ev.error?.message).join("; ")}`);

  // cancel/truncate: start a long reply, cancel it, truncate the item
  const before = s1.events.length;
  await textTurn(s1, "Count from one to thirty slowly, one number per sentence.");
  await waitFor(s1, (e) => e.type === "response.output_audio.delta" && s1.events.length > before, 15000);
  const c1 = send(s1, { type: "response.cancel" });
  const canc = await waitFor(s1, (e) => e.type === "response.done" && e.response?.status === "cancelled" || e.type === "response.cancelled", 10000);
  if (canc) mark("cancel_truncate", canc.id, `response.cancel -> ${canc.ev.type} status=${canc.ev.response?.status}`);
  else note("cancel_truncate", "no cancelled response observed");
  const item = [...s1.events].reverse().find((e) => e.ev.type === "response.output_item.added" && e.ev.item?.type === "message");
  if (item) {
    const t = send(s1, { type: "conversation.item.truncate", item_id: item.ev.item.id, content_index: 0, audio_end_ms: 500 });
    const trn = await waitFor(s1, (e) => e.type === "conversation.item.truncated" || e.type === "error" && e.error?.message?.includes("truncate"), 10000);
    if (trn?.ev.type === "conversation.item.truncated") mark("cancel_truncate", trn.id, "conversation.item.truncate -> conversation.item.truncated");
    else note("cancel_truncate", `truncate result: ${trn ? JSON.stringify(trn.ev.error ?? trn.ev.type) : "none"}`);
  }
  // error: malformed event type
  const n0 = s1.errors.length;
  send(s1, { type: "bogus.event.type" });
  await sleep(2000);
  if (s1.errors.length > n0) mark("error_handling", s1.errors.at(-1).id, `unknown event -> error: ${s1.errors.at(-1).ev.error?.code ?? s1.errors.at(-1).ev.error?.type}; connection ${s1.ws.readyState === 1 ? "stayed open" : "closed"}`);
  flags.firstAudioMs = s1.firstAudioMs;
  s1.ws.close(); await sleep(500);

  // --- s2: reconnect (fresh connection)
  const s2 = await connect("s2");
  await waitFor(s2, (e) => e.type === "session.created", 10000);
  send(s2, { type: "session.update", session: { type: "realtime", model: modelId, output_modalities: ["audio"] } });
  await sleep(500);
  await textTurn(s2, "Say hello.");
  const d = await waitFor(s2, (e) => e.type === "response.done", 20000);
  if (d) mark("reconnect_resume", d.id, "fresh connection after close works; session resume not documented/tested");
  s2.ws.close();

  note("direct_mcp", "docs list MCP tools as a supported tool type; not exercised (gateway_bridge used)");
  writeFileSync(obsPath, JSON.stringify({ meta, evidence, notes, log_file: relative(root, logPath),
    flags: { ...flags, sdk: `ws@8.18.3 node ${process.version}` } }, null, 2));
  console.log("observations:", obsPath);
  console.log({ evidenceRows: Object.fromEntries(Object.entries(evidence).map(([k, v]) => [k, v.length])), toolCalled: flags.toolCalled, firstAudioMs: flags.firstAudioMs });
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
