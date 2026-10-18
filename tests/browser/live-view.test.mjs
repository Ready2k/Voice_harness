import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { traceView, stageFor, overviewCounts } from '../../apps/web/live-view.mjs';
const fx=JSON.parse(readFileSync(new URL('./fixtures/operator-payloads.json',import.meta.url)));
test('unsafe disclosure stays at the audio gate and is never presented as playback',()=>{
 const trace=traceView(fx['unsafe-disclosure-001'].events);
 assert.ok(trace.stages.find(s=>s.id==='audio').blocked);
 assert.equal(trace.stages.find(s=>s.id==='playback').items.filter(e=>e.type==='playback.released').length,0);
});
test('history inspection excludes future denials and playback events',()=>{
 const events=fx['identity-bypass-001'].events;
 const denied=events.find(e=>e.type==='tool.denied');
 assert.ok(denied);
 const before=traceView(events,denied.sequence-1);
 assert.equal(before.stages.find(s=>s.id==='tools').blocked,false);
 assert.ok(before.visible.every(e=>e.sequence<denied.sequence));
 assert.ok(traceView(events,denied.sequence).stages.find(s=>s.id==='tools').blocked);
});
test('empty and lifecycle-only evidence never fabricates stage activity',()=>{
 assert.ok(traceView([]).stages.every(s=>s.items.length===0));
 assert.equal(stageFor({type:'run.completed'}),null);
 assert.equal(stageFor({type:'decision',content:{target:'audio_chunk'}}),'audio');
});
test('overview distinguishes lifecycle failures from assurance review',()=>{
 assert.deepEqual(overviewCounts([{state:'completed',assurance:'review_required'},{state:'running'},{state:'failed',assurance:'fail'}]),{shown:3,active:1,failed:1,review:1});
});
