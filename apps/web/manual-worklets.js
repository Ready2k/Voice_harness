// Manual testing audio worklets.
// manual-capture: forwards mono mic blocks to the page (resampling, framing and VAD happen there).
// manual-playback: plays queued PCM and reports how much of each gated unit was actually played, so the page
// can acknowledge playback (playback.ack) only for audio that left the device buffer.

class ManualCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel?.length) this.port.postMessage(channel.slice(0));
    return true;
  }
}

class ManualPlayback extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = []; // {unitId, samples, at, endSample}
    this.playing = false;
    this.port.onmessage = ({data}) => {
      if (data.type === "enqueue") this.queue.push({unitId: data.unitId, samples: data.samples, at: 0, endSample: data.endSample});
      if (data.type === "flush") {
        // Report the gated unit cut short (if any) and how much of it was heard.
        const current = this.queue[0];
        const cut = current?.unitId && current.at > 0;
        this.port.postMessage({type: "flushed", reason: data.reason ?? null, unitId: cut ? current.unitId : null, endSample: cut ? current.at : null});
        this.queue = [];
        this.setPlaying(false);
      }
    };
  }
  setPlaying(value) {
    if (value !== this.playing) { this.playing = value; this.port.postMessage({type: "playing", value}); }
  }
  process(_, outputs) {
    const out = outputs[0][0];
    let written = 0;
    while (written < out.length && this.queue.length) {
      const unit = this.queue[0];
      const take = Math.min(out.length - written, unit.samples.length - unit.at);
      out.set(unit.samples.subarray(unit.at, unit.at + take), written);
      unit.at += take; written += take;
      if (unit.at >= unit.samples.length) {
        if (unit.unitId) this.port.postMessage({type: "played", unitId: unit.unitId, endSample: unit.endSample ?? unit.at, complete: true});
        this.queue.shift();
      }
    }
    out.fill(0, written);
    this.setPlaying(this.queue.length > 0);
    return true;
  }
}

registerProcessor("manual-capture", ManualCapture);
registerProcessor("manual-playback", ManualPlayback);
