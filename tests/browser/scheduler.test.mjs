import test from "node:test";
import assert from "node:assert/strict";
import {generateKeyPairSync, sign, createHash} from "node:crypto";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import {ApprovalScheduler, canonical} from "../../apps/web/scheduler.mjs";

const pair = generateKeyPairSync("ed25519");
const publicKey = pair.publicKey.export({type:"spki",format:"der"}).subarray(-32).toString("base64");
function fixture({id = "1", sequence = 1, samples = 480} = {}) {
  const audio = Buffer.alloc(samples * 2);
  const token = {token_id:"t" + id,unit_id:"u" + id,sequence,session_id:"s1",generation_epoch:1,assessment_epoch:1,expires_at:2000,codec:"pcm_s16le",rate:24000,channels:1,start_sample:0,end_sample:samples,audio_sha256:createHash("sha256").update(audio).digest("hex")};
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
  await assert.rejects(scheduler.accept(fixture({id:"2",sequence:2})));
  assert.equal(rendered.length,1);
});

function make(rendered, options = {}) {
  return new ApprovalScheduler({sessionId:"s1",publicKey,generationEpoch:1,clock:()=>1000,render:{enqueue:x=>rendered.push(x),flush(){},revoke(){}},...options});
}

test("tokens must carry strictly increasing sequence: reused, reordered and missing are rejected", async () => {
  const rendered=[];
  const s=make(rendered);
  await s.accept(fixture({id:"a",sequence:5}));
  await assert.rejects(s.accept(fixture({id:"b",sequence:4})), /OUT_OF_ORDER_TOKEN/);
  await assert.rejects(s.accept(fixture({id:"c",sequence:5})), /OUT_OF_ORDER_TOKEN/);
  const missing=fixture({id:"d",sequence:9});
  delete missing.token.sequence;
  await assert.rejects(s.accept(missing));
  // A gap (a revoked or blocked unit in between) is legal.
  await s.accept(fixture({id:"e",sequence:9}));
  assert.deepEqual(rendered.map(x=>x.unitId),["ua","ue"]);
});

test("a rejected out-of-order token does not advance the sequence or reach the renderer", async () => {
  const rendered=[];
  const s=make(rendered);
  await s.accept(fixture({id:"a",sequence:3}));
  await assert.rejects(s.accept(fixture({id:"b",sequence:2})));
  await s.accept(fixture({id:"c",sequence:4}));
  assert.equal(s.lastSequence,4);
  assert.equal(rendered.length,2);
});

test("renderer never holds more than 100 ms of approved audio ahead of the playhead", async () => {
  const rendered=[];
  const s=make(rendered);
  const samples=24000; // 1 s
  await s.accept(fixture({id:"long",samples}));
  const lookahead=2400; // 100 ms at 24 kHz
  assert.equal(s.lookaheadSamples,lookahead);
  assert.equal(rendered.length,1);
  assert.equal(rendered[0].pcm.length/2,lookahead);
  assert.equal(rendered[0].final,false);
  let played=0, max=s.inFlight();
  while (played<samples) {
    played=Math.min(samples,played+1200);
    s.progress("ulong",played);
    max=Math.max(max,s.inFlight());
  }
  assert.ok(max<=lookahead);
  assert.equal(rendered.reduce((n,x)=>n+x.pcm.length/2,0),samples);
  assert.equal(rendered.at(-1).final,true);
  assert.deepEqual(rendered.map(x=>x.base),rendered.map((_,i)=>rendered.slice(0,i).reduce((n,x)=>n+x.pcm.length/2,0)));
});

test("revoke and flush drop audio still held back by the lookahead", async () => {
  const rendered=[], revoked=[];
  const s=make(rendered,{render:{enqueue:x=>rendered.push(x),flush(){},revoke:id=>revoked.push(id)}});
  await s.accept(fixture({id:"one",sequence:1,samples:24000}));
  await s.accept(fixture({id:"two",sequence:2,samples:24000}));
  assert.equal(s.inFlight(),2400);
  s.revoke("uone",2);
  assert.deepEqual(revoked,["uone"]);
  s.progress("uone",2400);
  assert.ok(rendered.slice(1).every(x=>x.unitId==="utwo"));
  s.flush(2);
  const before=rendered.length;
  s.progress("utwo",2400);
  assert.equal(rendered.length,before);
});

test("lookahead is configurable from the start limits", async () => {
  const rendered=[];
  const s=make(rendered,{lookaheadMs:20});
  await s.accept(fixture({id:"x",samples:24000}));
  assert.equal(rendered[0].pcm.length/2,480);
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
