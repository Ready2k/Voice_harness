// Client half of the four-timestamp clock exchange (spec section 11). All times
// are ms on the browser performance clock; the server uses its own monotonic
// domain, so only the offset interval derived from these samples is comparable.
export const SAMPLES_PER_ROUND = 8;
export const REFRESH_MS = 30000;

export class ClockSync {
  // send(type, payload) emits a control message; now() is the client monotonic clock.
  constructor({send, now = () => performance.now(), setTimer = setInterval, clearTimer = clearInterval}) {
    Object.assign(this, {send, now, setTimer, clearTimer});
    this.round = 0;
    this.outstanding = new Map();
    this.remaining = 0;
    this.timer = null;
    this.counter = 0;
    this.estimates = [];
  }

  // Eight sequential exchanges: each pong triggers the next ping.
  startRound() {
    this.round += 1;
    this.remaining = SAMPLES_PER_ROUND;
    this.outstanding.clear();
    this.estimates = [];
    this.ping();
  }

  ping() {
    if (this.remaining <= 0) return;
    this.remaining -= 1;
    const sampleId = "cs_" + this.round + "_" + (++this.counter);
    const clientSend = this.now();
    this.outstanding.set(sampleId, clientSend);
    this.send("clock.ping", {sample_id: sampleId, round: this.round, client_send: clientSend});
  }

  onPong(message) {
    const clientSend = this.outstanding.get(message.sample_id);
    if (clientSend === undefined) return null;
    this.outstanding.delete(message.sample_id);
    const clientReceive = this.now();
    this.send("clock.sample", {sample_id: message.sample_id, client_receive: clientReceive});
    const estimate = estimate4({clientSend, serverReceive: message.server_receive, serverSend: message.server_send, clientReceive});
    if (estimate) this.estimates.push(estimate);
    this.ping();
    return estimate;
  }

  // Connect-time round, then one every 30 s until stop().
  start() {
    this.startRound();
    this.timer = this.setTimer(() => this.startRound(), REFRESH_MS);
  }

  // Reconnect, sleep/resume or a discontinuity invalidates the window client-side too.
  invalidate() {
    this.outstanding.clear();
    this.remaining = 0;
    this.estimates = [];
  }

  stop() {
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    this.invalidate();
  }

  // Minimum-delay estimate of the current round, or null if incomplete.
  best() {
    if (this.estimates.length < SAMPLES_PER_ROUND) return null;
    return this.estimates.reduce((a, b) => (b.uncertainty < a.uncertainty ? b : a));
  }
}

// Network delay excludes server processing; negative delay or reversed stamps are a discontinuity.
export function estimate4({clientSend, serverReceive, serverSend, clientReceive}) {
  const delay = (clientReceive - clientSend) - (serverSend - serverReceive);
  if (delay < 0 || clientReceive < clientSend || serverSend < serverReceive) return null;
  return {
    offset: ((serverReceive - clientSend) + (serverSend - clientReceive)) / 2,
    uncertainty: delay / 2,
    delay,
  };
}
