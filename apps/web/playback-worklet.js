class GatedPlayback extends AudioWorkletProcessor {
  constructor() {
    super();
    this.epoch = 1;
    this.queue = [];
    this.port.onmessage = ({data}) => {
      if (data.type === "flush") {
        this.port.postMessage({type:"playback.stopped",ranges:this.queue.map(item=>({unitId:item.unitId,endSample:item.offset,epoch:item.epoch}))});
        this.epoch = Math.max(this.epoch, data.epoch);
        this.queue = [];
      } else if (data.type === "revoke") {
        this.queue = this.queue.filter(item => item.unitId !== data.unitId);
      } else if (data.type === "approved" && data.epoch === this.epoch) {
        const buffered = this.queue.reduce((sum,item)=>sum + item.samples.length - item.offset,0);
        if (this.queue.length >= 100 || buffered + data.samples.length > 48000) {
          this.port.postMessage({type:"error",code:"AUDIO_BACKPRESSURE"});
        } else {
          this.queue.push({...data, offset: 0});
        }
      }
    };
  }
  process(inputs, outputs) {
    const output = outputs[0][0];
    output.fill(0);
    let cursor = 0;
    while (cursor < output.length && this.queue.length) {
      const item = this.queue[0];
      if (item.epoch !== this.epoch) { this.queue.shift(); continue; }
      const count = Math.min(output.length - cursor, item.samples.length - item.offset);
      output.set(item.samples.subarray(item.offset, item.offset + count), cursor);
      item.offset += count;
      cursor += count;
      this.port.postMessage({type: "playback.progress", unitId: item.unitId, endSample: item.offset, epoch: item.epoch});
      if (item.offset === item.samples.length) {
        this.port.postMessage({type: "playback.ack", unitId: item.unitId, endSample: item.offset, epoch: item.epoch});
        this.queue.shift();
      }
    }
    return true;
  }
}
registerProcessor("gated-playback", GatedPlayback);
