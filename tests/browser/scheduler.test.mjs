import test from "node:test";
import assert from "node:assert/strict";
import {generateKeyPairSync, sign, createHash} from "node:crypto";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import {ApprovalScheduler, canonical} from "../../apps/web/scheduler.mjs";

const pair = generateKeyPairSync("ed25519");
const publicKey = pair.publicKey.export({type:"spki",format:"der"}).subarray(-32).toString("base64");
function fixture() {
  const audio = Buffer.alloc(960);
  const token = {token_id:"t1",unit_id:"u1",session_id:"s1",generation_epoch:1,assessment_epoch:1,expires_at:2000,codec:"pcm_s16le",rate:24000,channels:1,start_sample:0,end_sample:480,audio_sha256:createHash("sha256").update(audio).digest("hex")};
  return {token:{...token,signature:sign(null,Buffer.from(canonical(token)),pair.privateKey).toString("base64")},audio:audio.toString("base64")};
}

test("only signed exact audio reaches the renderer; replay and stale epoch fail", async () => {
  const rendered=[];
  const scheduler=new ApprovalScheduler({sessionId:"s1",publicKey,generationEpoch:1,clock:()=>1000,render:{enqueue:x=>rendered.push(x),flush(){},revoke(){}}});
  const frame=fixture();
  await assert.rejects(scheduler.accept({...frame,token:{...frame.token,audio_sha256:"forged"}}));
  await assert.rejects(scheduler.accept({...frame,audio:Buffer.alloc(960,1).toString("base64")}));
  assert.equal(rendered.length,0);
  await scheduler.accept(frame);
  assert.equal(rendered.length,1);
  await assert.rejects(scheduler.accept(frame));
  scheduler.flush(2);
  await assert.rejects(scheduler.accept(fixture()));
  assert.equal(rendered.length,1);
});

test("worklet flush and revoke emit zero old-epoch samples", () => {
  let Processor;
  const context={AudioWorkletProcessor:class {constructor(){this.port={postMessage(){}};}},registerProcessor:(name,value)=>{Processor=value;}};
  vm.runInNewContext(readFileSync(new URL("../../apps/web/playback-worklet.js",import.meta.url),"utf8"),context);
  const processor=new Processor();
  processor.port.onmessage({data:{type:"approved",unitId:"u",epoch:1,samples:new Float32Array(480).fill(.5)}});
  processor.port.onmessage({data:{type:"flush",epoch:2}});
  const output=new Float32Array(128);
  processor.process([],[[output]]);
  assert.ok(output.every(x=>x===0));
  processor.port.onmessage({data:{type:"approved",unitId:"u2",epoch:2,samples:new Float32Array(480).fill(.5)}});
  processor.port.onmessage({data:{type:"revoke",unitId:"u2"}});
  processor.process([],[[output]]);
  assert.ok(output.every(x=>x===0));
});

test("Python-issued approval verifies with browser WebCrypto", async () => {
  const fixture=JSON.parse(readFileSync(new URL("../../contracts/approval-fixture.json",import.meta.url),"utf8"));
  const output=[];
  const scheduler=new ApprovalScheduler({sessionId:fixture.token.session_id,publicKey:fixture.public_key,generationEpoch:1,clock:()=>1000,render:{enqueue:x=>output.push(x),flush(){},revoke(){}}});
  await scheduler.accept(fixture);
  assert.equal(output[0].pcm.length,960);
});
