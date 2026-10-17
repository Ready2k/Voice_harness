// The operator UI may use this module; provider audio has no direct renderer route.
export function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  return "{" + Object.keys(value).sort().map(k => JSON.stringify(k) + ":" + canonical(value[k])).join(",") + "}";
}

function bytes(base64) {
  return Uint8Array.from(atob(base64), char => char.charCodeAt(0));
}

export class ApprovalScheduler {
  // lookaheadMs bounds how much approved audio is handed to the renderer ahead of
  // the playhead (spec section 6: 100 ms), so a revoke or flush leaves at most that
  // much residual audio. The rest waits here until playback progress frees room.
  constructor({sessionId, publicKey, generationEpoch, render, clock = () => Date.now() / 1000, lookaheadMs = 100, rate = 24000}) {
    Object.assign(this, {sessionId, publicKey, generationEpoch, render, clock, lookaheadMs, rate});
    this.used = new Set();
    this.assessmentEpochs = new Map();
    this.lastSequence = 0;
    this.units = new Map();
    this.order = [];
  }

  get lookaheadSamples() {
    return Math.floor(this.rate * this.lookaheadMs / 1000);
  }

  flush(generationEpoch) {
    this.generationEpoch = Math.max(this.generationEpoch, generationEpoch);
    this.units.clear();
    this.order = [];
    this.render.flush(this.generationEpoch);
  }

  revoke(unitId, assessmentEpoch) {
    this.assessmentEpochs.set(unitId, assessmentEpoch);
    this.units.delete(unitId);
    this.order = this.order.filter(id => id !== unitId);
    this.render.revoke(unitId);
    this.pump();
  }

  // Samples handed to the renderer but not yet reported played.
  inFlight() {
    let total = 0;
    for (const unit of this.units.values()) total += unit.sent - unit.played;
    return total;
  }

  progress(unitId, endSample) {
    const unit = this.units.get(unitId);
    if (!unit) return;
    unit.played = Math.max(unit.played, Math.min(endSample, unit.total));
    if (unit.played >= unit.total) this.units.delete(unitId);
    this.pump();
  }

  pump() {
    let flight = this.inFlight();
    while (this.order.length) {
      const unit = this.units.get(this.order[0]);
      if (!unit) { this.order.shift(); continue; }
      const room = this.lookaheadSamples - flight;
      if (room <= 0) break;
      const count = Math.min(room, unit.total - unit.sent);
      this.render.enqueue({unitId: unit.unitId, epoch: unit.epoch, pcm: unit.pcm.subarray(unit.sent * 2, (unit.sent + count) * 2), base: unit.sent, final: unit.sent + count === unit.total});
      unit.sent += count;
      flight += count;
      if (unit.sent === unit.total) this.order.shift();
    }
  }

  async accept({token, audio}) {
    if (this.used.size >= 10000) throw new Error("PLAYBACK_LIMIT_EXCEEDED");
    const {signature, ...payload} = token;
    const key = await crypto.subtle.importKey("raw", bytes(this.publicKey), {name: "Ed25519"}, false, ["verify"]);
    if (!await crypto.subtle.verify("Ed25519", key, bytes(signature), new TextEncoder().encode(canonical(payload)))) throw new Error("INVALID_APPROVAL_SIGNATURE");
    const pcm = bytes(audio);
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", pcm)), b => b.toString(16).padStart(2, "0")).join("");
    // Repeat epoch/freshness checks after async crypto to close an interrupt race.
    if (token.session_id !== this.sessionId || token.generation_epoch !== this.generationEpoch || token.expires_at <= this.clock() || this.used.has(token.token_id)) throw new Error("STALE_APPROVAL");
    if ((this.assessmentEpochs.get(token.unit_id) ?? token.assessment_epoch) !== token.assessment_epoch) throw new Error("STALE_ASSESSMENT");
    // Release order is session-monotonic; gaps (revoked/blocked units) are fine, reuse or reordering is not.
    if (!Number.isInteger(token.sequence) || token.sequence <= this.lastSequence) throw new Error("OUT_OF_ORDER_TOKEN");
    if (token.codec !== "pcm_s16le" || token.rate !== 24000 || token.channels !== 1 || hash !== token.audio_sha256 || pcm.length !== (token.end_sample - token.start_sample) * 2) throw new Error("INVALID_APPROVED_AUDIO");
    this.used.add(token.token_id);
    this.lastSequence = token.sequence;
    this.units.set(token.unit_id, {unitId: token.unit_id, epoch: token.generation_epoch, pcm, total: pcm.length / 2, sent: 0, played: 0});
    this.order.push(token.unit_id);
    this.pump();
  }
}
