import test from 'node:test';
import assert from 'node:assert/strict';
import {comparisonCheck,batchPlan,metricMatrix,costDescription} from '../../apps/web/compare.mjs';
const config={scenario_id:'task',scenario_version:1,fixture_seed:42,policy_profile:'strict-v1',input_mode:'fixed_audio',client_mode:'software_fixture',retain_audio:false,provider_config_id:'a'};
const run=(id,change={})=>({run_id:id,config:{...config,...change}});
test('comparison requires distinct runs of exactly the same task and version',()=>{
 assert.throws(()=>comparisonCheck([run('x'),run('x')]),/distinct/);
 assert.throws(()=>comparisonCheck([run('x'),run('y',{scenario_id:'other'})]),/same scenario/);
 assert.throws(()=>comparisonCheck([run('x'),run('y',{scenario_version:2})]),/same scenario/);
 assert.deepEqual(comparisonCheck([run('x'),run('y',{provider_config_id:'b'})]).differences,[]);
});
test('configuration differences and absent evidence are disclosed',()=>{
 const check=comparisonCheck([run('x'),run('y',{fixture_seed:43,client_mode:undefined})]);
 assert.ok(check.differences.includes('fixture_seed'));assert.ok(check.missing.includes('client_mode'));
});
test('batch pairs seeds across providers and preserves distinct idempotency keys',()=>{
 const jobs=batchPlan({id:'task',version:1,live:true},['a','b'],42,3,false,'batch');
 assert.deepEqual(jobs.map(j=>j.body.fixture_seed),[42,43,44,42,43,44]);
 assert.equal(new Set(jobs.map(j=>j.key)).size,6);
 assert.ok(jobs.every(j=>j.body.repetitions===1&&j.body.policy_profile==='strict-v1'));
 assert.throws(()=>batchPlan({id:'task',live:true},['a','b'],42,5,false,'b'),/2–8/);
 assert.throws(()=>batchPlan({id:'task',live:false},['a','b'],42,1,false,'b'),/mock/);
});
test('missing metric is unavailable rather than zero',()=>{
 const result=metricMatrix([{metrics:{observations:[{metric:'latency',value:null,exclusion_reason:'CLOCK_UNSYNCHRONIZED'}]}},{metrics:{observations:[]}}]);
 assert.match(result[0].values[0],/CLOCK_UNSYNCHRONIZED/);assert.equal(result[0].values[1],'Unavailable');
});
test('partial costs show subtotal, provenance and unknown components without claiming total',()=>{
 const text=costDescription({completeness:'partial',unknown_line_count:1,unknown_components:['voice'],summaries:[{currency:'GBP',known_subtotal:'0.12',total_cost:null,by_provenance:{estimated:'0.12'}}]});
 assert.match(text,/known subtotal/);assert.match(text,/estimated 0.12/);assert.match(text,/voice/);assert.doesNotMatch(text,/\(total\)/);
});

test('numeric differences require compatible measured units and never fill missing values',async()=>{
 const {metricDeltas}=await import('../../apps/web/compare.mjs');
 const entry=(value,unit='ms',exclusion_reason=null)=>({metrics:{observations:[{metric:'latency',value,unit,exclusion_reason}]}});
 assert.equal(metricDeltas(entry(12),entry(15))[0].delta,3);
 assert.equal(metricDeltas(entry(null),entry(15))[0].delta,null);
 assert.equal(metricDeltas(entry(12,'s'),entry(15))[0].delta,null);
 assert.equal(metricDeltas(entry(12,'ms','UNSYNCHRONIZED'),entry(15))[0].delta,null);
});
