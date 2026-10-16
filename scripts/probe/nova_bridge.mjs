// JSON-lines bridge between the Python Nova Sonic adapter and Bedrock
// InvokeModelWithBidirectionalStream. Uses the SDK proven in nova_probe.mjs.
// stdin : one JSON object per line = a Nova event ({"event":{...}}); EOF closes the input stream.
//         First line may be {"_open":{"region":..,"modelId":..}}.
// stdout: one JSON per line = a Nova output event, or {"_open_ok":{requestId,httpStatus}},
//         {"_error":{name,message}}, {"_closed":true}.
import { BedrockRuntimeClient, InvokeModelWithBidirectionalStreamCommand } from "@aws-sdk/client-bedrock-runtime";
import { createInterface } from "node:readline";

class Queue {
  constructor() { this.items = []; this.waiters = []; this.closed = false; }
  push(x) { const w = this.waiters.shift(); w ? w({ value: x, done: false }) : this.items.push(x); }
  close() { this.closed = true; this.waiters.splice(0).forEach((w) => w({ value: undefined, done: true })); }
  [Symbol.asyncIterator]() { return { next: () => this.items.length ? Promise.resolve({ value: this.items.shift(), done: false }) : this.closed ? Promise.resolve({ done: true }) : new Promise((r) => this.waiters.push(r)) }; }
}
const out = (o) => process.stdout.write(JSON.stringify(o) + "\n");
const q = new Queue();
let started = false;

async function start(cfg) {
  const client = new BedrockRuntimeClient({ region: cfg.region ?? "us-east-1" });
  try {
    const response = await client.send(new InvokeModelWithBidirectionalStreamCommand({ modelId: cfg.modelId ?? "amazon.nova-2-5-sonic", body: q }));
    out({ _open_ok: { requestId: response.$metadata?.requestId, httpStatus: response.$metadata?.httpStatusCode } });
    for await (const part of response.body) {
      if (part.chunk?.bytes) out(JSON.parse(Buffer.from(part.chunk.bytes).toString()));
      else out({ _error: { name: "NonChunkEvent", message: Object.keys(part).join(",") } });
    }
  } catch (e) {
    out({ _error: { name: e.name, message: e.message } });
  }
  out({ _closed: true });
  process.exit(0);
}

const rl = createInterface({ input: process.stdin });
rl.on("line", (line) => {
  if (!line.trim()) return;
  const msg = JSON.parse(line);
  if (msg._open) { if (!started) { started = true; start(msg._open); } return; }
  q.push({ chunk: { bytes: Buffer.from(JSON.stringify(msg)) } });
});
rl.on("close", () => q.close());
