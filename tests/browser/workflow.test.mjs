import test from 'node:test';
import assert from 'node:assert/strict';
import {holdBody,cloneRunConfig,testedIdentity} from '../../apps/web/workflow.mjs';
import {reviewDecision} from '../../apps/web/review-workflow.mjs';
import {transcript} from '../../apps/web/runs.mjs';

test('hold creation uses exactly one scope and validates expiry',()=>{
 const input={owner:'reviewer',reason:'investigation',expiry:'',key:'key'};
 assert.deepEqual(holdBody(input),{owner_id:'reviewer',reason:'investigation',expires_at:null,finding_ids:null,run_wide:true,idempotency_key:'key'});
 assert.deepEqual(holdBody({...input,findings:'f1, f2'}).finding_ids,['f1','f2']);
 assert.equal(holdBody({...input,findings:'f1'}).run_wide,false);
 assert.throws(()=>holdBody({...input,expiry:'2020-01-01T12:00'}),/future/);
});
test('rerun copies execution settings, forces one repetition, and does not forward unrelated state',()=>{
 assert.deepEqual(cloneRunConfig({scenario_id:'task',fixture_seed:7,policy_profile:'strict-v1',repetitions:20,secret:'never forward'}),{scenario_id:'task',fixture_seed:7,policy_profile:'strict-v1',repetitions:1});
});
test('unknown live model identity is not fabricated for judge independence',()=>{
 assert.equal(testedIdentity({config:{provider_config_id:'unknown'}},[]),null);
 const identity=testedIdentity({config:{provider_config_id:'openai-realtime-live'}},[{type:'run.live_binding',content:{provider:{requested_model_id:'m',family:'openai',adapter:'openai_realtime'}}}]);assert.equal(identity.model_id,'m');
});
test('review rationale is required and supporting evidence remains explicit',()=>{
 assert.throws(()=>reviewDecision({reviewer:'r',rationale:' ',evidence:'',disposition:'confirm_fail'}),/explain/);
 assert.deepEqual(reviewDecision({reviewer:' r ',rationale:'Evidence confirms failure',evidence:'e1, e2',disposition:'confirm_fail'}).evidence_event_ids,['e1','e2']);
});
test('revision does not erase prior release evidence or imply that played speech was never released',()=>{
 const events=[{sequence:1,type:'audio.output',content:{unit_id:'u',transcript:'original'}},{sequence:2,type:'playback.released',content:{unit_id:'u'}},{sequence:3,type:'playback.ack',content:{unit_id:'u'}},{sequence:4,type:'audio.approval_replaced',content:{old_unit_id:'u',new_unit_id:'u2'}}];
 const line=transcript(events)[0];assert.equal(line.state,'superseded');assert.equal(line.playbackLabel,'Playback acknowledged');assert.equal(line.generatedButNotReleased,false);
});

test('stored report pairs retain the backend provider ordering without confusing means for names',async()=>{
 const {pairedLabels}=await import('../../apps/web/reports.mjs');
 assert.deepEqual(pairedLabels(['a','b','c'],[{},{},{}]),['a vs b','a vs c','b vs c']);
 assert.deepEqual(pairedLabels(['a','b','c'],[{}]),['Pair 1 (see source report)']);
});
