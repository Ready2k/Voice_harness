import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import {ClockSync, estimate4, SAMPLES_PER_ROUND} from "../../apps/web/clock-sync.mjs";

// A scripted server: fixed one-way delays and a fixed clock offset (server = client + 500).
function harness({upMs = 5, downMs = 5, processMs = 1} = {}) {
  let t = 1000;
  const sent = [];
  const sync = new ClockSync({now: () => t, send: (type, payload) => sent.push({type, payload}), setTimer: () => 7, clearTimer: () => {}});
  const roundTrip = () => {
    const ping = sent.filter(m => m.type === "clock.ping").at(-1).payload;
    const serverReceive = ping.client_send + 500 + upMs;
    const serverSend = serverReceive + processMs;
    t = ping.client_send + upMs + processMs + downMs;
    return sync.onPong({sample_id: ping.sample_id, server_receive: serverReceive, server_send: serverSend});
  };
  return {sync, sent, roundTrip, advance: ms => { t += ms; }};
}

test("a round is eight sequential four-timestamp exchanges and recovers the offset", () => {
  const {sync, sent, roundTrip} = harness();
  sync.startRound();
  for (let i = 0; i < SAMPLES_PER_ROUND; i++) {
    assert.equal(sent.filter(m => m.type === "clock.ping").length, i + 1);
    roundTrip();
  }
  assert.equal(sent.filter(m => m.type === "clock.ping").length, 8);
  assert.equal(sent.filter(m => m.type === "clock.sample").length, 8);
  const best = sync.best();
  assert.equal(best.offset, 500);
  // Network delay excludes server processing; half of it bounds the uncertainty.
  assert.equal(best.delay, 10);
  assert.equal(best.uncertainty, 5);
});

test("a pong for an unknown sample is ignored and sends nothing", () => {
  const {sync, sent} = harness();
  sync.startRound();
  const before = sent.length;
  assert.equal(sync.onPong({sample_id: "forged", server_receive: 1, server_send: 2}), null);
  assert.equal(sent.length, before);
});

test("reversed stamps are a discontinuity, not a sample", () => {
  assert.equal(estimate4({clientSend: 10, serverReceive: 5, serverSend: 4, clientReceive: 20}), null);
  assert.equal(estimate4({clientSend: 10, serverReceive: 5, serverSend: 6, clientReceive: 9}), null);
});

test("start schedules a refresh timer and stop invalidates the window", () => {
  const {sync, sent, roundTrip} = harness();
  const timers = [];
  sync.setTimer = (fn, ms) => { timers.push({fn, ms}); return 1; };
  sync.start();
  assert.equal(timers[0].ms, 30000);
  roundTrip();
  timers[0].fn();
  assert.equal(sync.round, 2);
  sync.stop();
  assert.equal(sync.best(), null);
  assert.equal(sync.outstanding.size, 0);
  assert.ok(sent.length > 0);
});

test("worklet chunks of one unit ack once, at the unit end", () => {
  let Processor;
  const posted = [];
  const context = {AudioWorkletProcessor: class { constructor() { this.port = {postMessage: m => posted.push(m)}; } }, registerProcessor: (name, value) => { Processor = value; }};
  vm.runInNewContext(readFileSync(new URL("../../apps/web/playback-worklet.js", import.meta.url), "utf8"), context);
  const processor = new Processor();
  processor.port.onmessage({data: {type: "approved", unitId: "u", epoch: 1, samples: new Float32Array(128).fill(.5), base: 0, final: false}});
  processor.port.onmessage({data: {type: "approved", unitId: "u", epoch: 1, samples: new Float32Array(128).fill(.5), base: 128, final: true}});
  const output = new Float32Array(128);
  processor.process([], [[output]]);
  assert.equal(posted.filter(m => m.type === "playback.ack").length, 0);
  processor.process([], [[output]]);
  const acks = posted.filter(m => m.type === "playback.ack");
  assert.equal(acks.length, 1);
  assert.equal(acks[0].endSample, 256);
});
