import test from "node:test";
import assert from "node:assert/strict";
import {EnergyVad, Framer, Resampler, feedItem, floatToPcm16, liveChecks, pcm16ToFloat, promptProgress,
  reduceTranscript, sessionStatus} from "../../apps/web/manual.mjs";

const ev = (sequence, type, content) => ({sequence, type, content});

test("PCM16 conversion round-trips and clamps", () => {
  const bytes = floatToPcm16(Float32Array.from([0, 0.5, -0.5, 2, -2]));
  assert.equal(bytes.length, 10);
  const back = pcm16ToFloat(bytes);
  assert.ok(Math.abs(back[1] - 0.5) < 1e-3 && Math.abs(back[2] + 0.5) < 1e-3);
  assert.ok(back[3] > 0.999 && back[4] === -1);
});

test("resampling 48 kHz to 24 kHz halves the sample count across block boundaries", () => {
  const r = new Resampler(48000, 24000);
  let total = 0;
  for (let i = 0; i < 100; i++) total += r.push(new Float32Array(128).fill(0.25)).length;
  assert.equal(total, 6400);
  const ramp = new Resampler(44100, 24000).push(Float32Array.from({length: 441}, (_, i) => i / 441));
  assert.ok(Math.abs(ramp.length - 240) <= 1);
  assert.ok(ramp.every((v, i) => i === 0 || v > ramp[i - 1]), "a ramp stays monotonic");
});

test("framer emits 480-sample frames and keeps the remainder", () => {
  const f = new Framer();
  assert.equal(f.push(new Float32Array(300)).length, 0);
  const frames = f.push(new Float32Array(700));
  assert.deepEqual(frames.map(x => x.length), [480, 480]);
  assert.equal(f.buffer.length, 40);
});

test("VAD starts after sustained energy and ends after the hangover", () => {
  const vad = new EnergyVad({startFrames: 3, hangoverFrames: 5});
  const edges = [];
  for (const level of [0.001, 0.001, 0.2, 0.2, 0.2, 0.2, 0.001, 0.001, 0.001, 0.001, 0.001, 0.001]) edges.push(vad.push(level));
  assert.equal(edges.indexOf("start"), 4);
  assert.equal(edges.indexOf("end"), 10);
  assert.equal(vad.reset(), null);
});

test("a single loud click does not start speech", () => {
  const vad = new EnergyVad();
  assert.deepEqual([0.5, 0.001, 0.001].map(l => vad.push(l)), [null, null, null]);
});

test("prompt progress marks done, next and upcoming lines", () => {
  const p = promptProgress(["a", "b", "c"], 1);
  assert.deepEqual(p.map(x => x.state), ["done", "next", "upcoming"]);
  assert.deepEqual(promptProgress(["a"], 3).map(x => x.state), ["done"]);
});

const happy = {expected_tools: ["verify_identity", "get_account_summary"], prohibited_tools: []};
const failure = {expected_tools: [], prohibited_tools: ["get_account_summary"]};

test("happy-path checks follow the expected tool order from the ledger", () => {
  const events = [ev(1, "tool.requested", {call_id: "a", tool: "verify_identity"}), ev(2, "tool.completed", {call_id: "a"})];
  let checks = liveChecks({path: happy, events, identity: "verified"});
  const tools = checks.find(c => c.id === "expected-tools");
  assert.equal(tools.status, "pending");
  assert.match(tools.detail, /✓ verify_identity.*· get_account_summary/);
  events.push(ev(3, "tool.requested", {call_id: "b", tool: "get_account_summary"}), ev(4, "tool.completed", {call_id: "b"}));
  checks = liveChecks({path: happy, events, identity: "verified", assessments: [{status: "approved"}]});
  assert.equal(checks.find(c => c.id === "expected-tools").status, "pass");
  assert.equal(checks.find(c => c.id === "identity").status, "pass");
  assert.equal(checks.find(c => c.id === "speech").status, "pass");
});

test("failure-path checks separate a policy denial from an executed prohibited call", () => {
  const denied = [ev(1, "tool.requested", {call_id: "a", tool: "get_account_summary"}), ev(2, "tool.denied", {call_id: "a", error_code: "IDENTITY_REQUIRED"})];
  assert.equal(liveChecks({path: failure, events: denied}).find(c => c.id === "prohibited-get_account_summary").status, "warn");
  const ran = [ev(1, "tool.requested", {call_id: "a", tool: "get_account_summary"}), ev(2, "tool.completed", {call_id: "a"})];
  assert.equal(liveChecks({path: failure, events: ran}).find(c => c.id === "prohibited-get_account_summary").status, "fail");
  assert.equal(liveChecks({path: failure, events: []}).find(c => c.id === "prohibited-get_account_summary").status, "pass");
});

test("held speech is a warning when gated and a failure when it was streamed", () => {
  const assessments = [{status: "held"}];
  assert.equal(liveChecks({path: failure, assessments}).find(c => c.id === "speech").status, "warn");
  const streamed = liveChecks({path: failure, assessments, playbackMode: "streamed",
    events: [ev(1, "playback.streamed_unassessed", {unit_id: "u"})]});
  assert.equal(streamed.find(c => c.id === "speech").status, "fail");
  assert.match(streamed.find(c => c.id === "playback").detail, /1 reply played before assessment/);
});

test("transcript joins assistant deltas and attaches the assessment to the reply", () => {
  let lines = [];
  for (const m of [
    {type: "local_text", text: "The answer is blue."},
    {type: "transcript", role: "assistant", text: "You are ", final: false},
    {type: "transcript", role: "assistant", text: "verified.", final: false},
    {type: "response_end"},
    {type: "assessing", unit_id: "u1"},
    {type: "assessment", unit_id: "u2", status: "approved", transcript: "You are verified.", reason_codes: []},
  ]) lines = reduceTranscript(lines, m);
  assert.equal(lines.length, 2);
  assert.deepEqual([lines[1].text, lines[1].status, lines[1].heard], ["You are verified.", "approved", "You are verified."]);
});

test("user voice transcripts update in place by item id", () => {
  let lines = reduceTranscript([], {type: "transcript", role: "user", text: "Hel", final: false, item_id: "i1"});
  lines = reduceTranscript(lines, {type: "transcript", role: "user", text: "Hello there.", final: true, item_id: "i1"});
  assert.equal(lines.length, 1);
  assert.equal(lines[0].text, "Hello there.");
});

test("feed items describe tools, assessments and interruptions", () => {
  assert.equal(feedItem({type: "tool", name: "verify_identity", status: "success"}).tone, "ok");
  assert.match(feedItem({type: "assessment", status: "held", reason_codes: ["UNEXPECTED_AMOUNT"]}).text, /UNEXPECTED_AMOUNT/);
  assert.match(feedItem({type: "flush", reason: "barge_in"}).text, /interrupted/);
  assert.equal(feedItem({type: "flush", reason: "barge_in", history_truncation: "confirmed"}).tone, "info");
  assert.match(feedItem({type: "flush", reason: "barge_in", history_truncation: "unsupported"}).text, /can't trim/);
  assert.equal(feedItem({type: "ledger", events: []}), null);
});

test("session status reflects pause, mute, text mode and speaking", () => {
  const base = {connected: true, ended: false, paused: false, muted: false, mode: "voice", speaking: false, aiPlaying: false, assessing: false};
  assert.equal(sessionStatus(base).text, "Listening");
  assert.equal(sessionStatus({...base, paused: true}).text, "Paused");
  assert.equal(sessionStatus({...base, muted: true}).text, "Microphone muted");
  assert.match(sessionStatus({...base, mode: "text"}).text, /Text mode/);
  assert.equal(sessionStatus({...base, aiPlaying: true}).text, "Agent speaking");
});
