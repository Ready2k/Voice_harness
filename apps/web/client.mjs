import {ApprovalScheduler} from "./scheduler.mjs";
import {ClockSync} from "./clock-sync.mjs";

// Call after a user gesture resumes a 24kHz AudioContext. It is a privileged
// client sink: no provider SDK or unapproved audio is accepted here.
export async function connectPlayback({audioContext, runId, operatorToken, baseUrl = location.origin}) {
  if (audioContext.sampleRate !== 24000) throw new Error("UNSUPPORTED_AUDIO_CONTEXT_RATE");
  await audioContext.audioWorklet.addModule(new URL("./playback-worklet.js", import.meta.url));
  const node = new AudioWorkletNode(audioContext, "gated-playback", {numberOfInputs:0,numberOfOutputs:1,outputChannelCount:[1]});
  node.connect(audioContext.destination);
  const response = await fetch(baseUrl + "/api/v1/runs/" + runId + "/ws-ticket", {method:"POST",headers:{Authorization:"Bearer " + operatorToken}});
  if (!response.ok) throw new Error("WEBSOCKET_AUTH_FAILED");
  const {ticket} = await response.json();
  const url = new URL("/api/v1/runs/" + runId + "/ws", baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(url, ["harness.v1", "ticket." + ticket]);
  let start, scheduler, sequence = 0, chain = Promise.resolve();
  let clock;
  const send = (type, payload) => socket.send(JSON.stringify({schema_version:"0.3",message_id:crypto.randomUUID(),session_id:start.session_id,connection_epoch:start.connection_epoch,client_sequence:++sequence,type,payload}));
  node.port.onmessage = ({data}) => {
    if (data.type === "playback.progress" && scheduler) scheduler.progress(data.unitId, data.endSample);
    if (data.type === "playback.ack") send("playback.ack", {unit_id:data.unitId,end_sample:data.endSample});
    if (data.type === "playback.stopped" && start) send("playback.stopped", {ranges:data.ranges});
    if (data.type === "error") { send("stop", {reason:data.code}); node.disconnect(); }
  };
  socket.onmessage = ({data}) => {
    chain = chain.then(async () => {
      const message = JSON.parse(data);
      if (message.type === "start") {
        start = message;
        scheduler = new ApprovalScheduler({sessionId:start.session_id,publicKey:start.approval_public_key,generationEpoch:start.generation_epoch,lookaheadMs:start.limits?.client_schedule_ahead_ms ?? 100,render:{
          enqueue({unitId, epoch, pcm, base = 0, final = true}) {
            const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
            const samples = Float32Array.from({length:pcm.length / 2}, (_, i) => view.getInt16(i * 2, true) / 32768);
            node.port.postMessage({type:"approved",unitId,epoch,samples,base,final}, [samples.buffer]);
          },
          flush:epoch=>node.port.postMessage({type:"flush",epoch}),
          revoke:unitId=>node.port.postMessage({type:"revoke",unitId})
        }});
        scheduler.flush(start.generation_epoch);
        // Eight four-timestamp exchanges now and every 30 s; a new socket starts a new window.
        clock = new ClockSync({send});
        clock.start();
      } else if (message.type === "clock.pong") {
        clock?.onPong(message);
      } else if (message.type === "audio.approved") {
        await scheduler.accept(message);
      } else if (message.type === "playback.stopped") {
        scheduler.flush(message.generation_epoch ?? scheduler.generationEpoch + 1);
      } else if (message.type === "playback.revoke") {
        scheduler.revoke(message.unit_id, message.assessment_epoch);
      } else if (message.type === "ping") {
        send("pong", {});
      }
    }).catch(() => { node.disconnect(); socket.close(); });
  };
  socket.onclose = () => { clock?.stop(); node.disconnect(); };
  return {interrupt() { scheduler.flush(scheduler.generationEpoch + 1); send("interrupt", {}); },close() { clock?.stop(); socket.close();node.disconnect(); }};
}
