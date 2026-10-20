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

test('different prompt profiles are disclosed',()=>{
 assert.ok(comparisonCheck([run('x',{prompt_profile:'legacy-v1'}),run('y',{prompt_profile:'standardised-v2'})]).differences.includes('prompt_profile'));
});

test('tasks with 2+ runs come first and carry run counts',async()=>{
 const {taskGroups,taskLabel,defaultTask}=await import('../../apps/web/compare.mjs');
 const r=(id,sc,at)=>({run_id:id,scenario_id:sc,scenario_version:1,started_at:at});
 const runs=[r('1','solo','2026-10-20T10:00:00Z'),r('2','pair','2026-10-19T10:00:00Z'),r('3','pair','2026-10-18T10:00:00Z'),r('4','many','2026-10-01T10:00:00Z'),r('5','many','2026-10-02T10:00:00Z'),r('6','many','2026-10-03T10:00:00Z')];
 assert.deepEqual(taskGroups(runs).map(g=>g.key),['many@1','pair@1','solo@1']);
 assert.equal(taskLabel(taskGroups(runs)[0]),'many@1 · 3 runs');assert.equal(taskLabel(taskGroups(runs)[2]),'solo@1 · 1 run');
 assert.equal(defaultTask(runs),'many@1');assert.equal(defaultTask([]),null);
});

test('quick picks take the newest run per provider, or newest repeats of one provider',async()=>{
 const {latestPerProvider,repeatsOfProvider}=await import('../../apps/web/compare.mjs');
 const r=(id,p,at)=>({run_id:id,provider_config_id:p,started_at:at});
 const runs=[r('a1','a','2026-10-01T00:00:00Z'),r('a2','a','2026-10-03T00:00:00Z'),r('b1','b','2026-10-02T00:00:00Z'),r('a3','a','2026-10-02T00:00:00Z')];
 assert.deepEqual(latestPerProvider(runs),['a2','b1']);
 assert.deepEqual(repeatsOfProvider(runs,'a'),['a2','a3','a1']);
 assert.deepEqual(repeatsOfProvider(runs,'a',2),['a2','a3']);
});

test('live controls apply only to unfinished runs',async()=>{
 const {isLive}=await import('../../apps/web/compare.mjs');
 assert.equal(isLive({state:'running'}),true);assert.equal(isLive({state:'queued'}),true);
 for(const s of ['completed','failed','cancelled'])assert.equal(isLive({state:s}),false);
});

test('metric table hides unmeasured metrics, keeps one-sided values and never invents a delta',async()=>{
 const {metricTable}=await import('../../apps/web/compare.mjs');
 const e=obs=>({metrics:{observations:obs}});
 const o=(metric,value,unit='ms',exclusion_reason=null)=>({metric,value,unit,exclusion_reason});
 const {rows,hidden}=metricTable(
  e([o('turns',3,'count'),o('first_audio_latency',120),o('only_x',5),o('gone',null,'ms','NO_DATA')]),
  e([o('turns',5,'count'),o('first_audio_latency',100),o('gone',null,'ms','NO_DATA')]),n=>n.toUpperCase());
 assert.deepEqual(rows.map(r=>r.name),['first_audio_latency','turns','only_x']);
 assert.equal(rows[0].latency,true);assert.equal(rows[0].delta,-20);assert.equal(rows[0].direction,'down');
 assert.equal(rows[1].delta,2);assert.equal(rows[1].direction,'up');
 assert.equal(rows[2].y,null);assert.equal(rows[2].delta,null);
 assert.deepEqual(hidden.map(h=>[h.name,h.label,h.reason]),[['gone','GONE','NO_DATA']]);
});

test('conversations align turn by turn; earlier versions only appear with revisions',async()=>{
 const {alignTurns,hasRevisions}=await import('../../apps/web/compare.mjs');
 const c=t=>({speaker:'customer',text:t}),a=(t,state='played')=>({speaker:'assistant',text:t,state});
 const x=[c('hi'),a('draft','superseded'),a('final'),c('bye'),a('ok')];
 const y=[c('hi'),a('hello')];
 assert.equal(hasRevisions(x),true);assert.equal(hasRevisions(y),false);
 const final=alignTurns(x,y);
 assert.equal(final.length,2);assert.deepEqual(final[0].x.assistant.map(l=>l.text),['final']);
 assert.equal(final[1].y,null);assert.equal(final[1].x.customer.text,'bye');
 assert.deepEqual(alignTurns(x,y,true)[0].x.assistant.map(l=>l.text),['draft','final']);
 assert.equal(alignTurns([a('greeting')],[]).length,1);
});

test('real revised-transcript events yield an earlier version that only shows with revisions',async()=>{
 const {transcript}=await import('../../apps/web/runs.mjs');
 const {alignTurns,hasRevisions}=await import('../../apps/web/compare.mjs');
 const ev=(sequence,type,content)=>({sequence,type,content});
 const events=[ev(1,'simulator.utterance_generated',{output_text:'balance please'}),
  ev(2,'audio.output',{unit_id:'u1',transcript:'draft answer'}),
  ev(3,'audio.output',{unit_id:'u2',transcript:'final answer'}),
  ev(4,'audio.approval_replaced',{old_unit_id:'u1',new_unit_id:'u2'}),
  ev(5,'playback.released',{unit_id:'u2'}),ev(6,'playback.ack',{unit_id:'u2'})];
 const lines=transcript(events);
 assert.equal(hasRevisions(lines),true);
 const hidden=alignTurns(lines,lines,false)[0].x.assistant.map(l=>l.state);
 assert.ok(!hidden.includes('superseded'));
 const shown=alignTurns(lines,lines,true)[0].x.assistant;
 assert.ok(shown.some(l=>l.state==='superseded'));
 assert.ok(shown.length>hidden.length);
});

test('summary-object latency expands to numeric rows with deltas only from recorded values',async()=>{
 const {metricTable}=await import('../../apps/web/compare.mjs');
 const e=obs=>({metrics:{observations:obs}});
 const o=(value,unit='ms',exclusion_reason=null)=>({metric:'first_audio_latency',value,unit,exclusion_reason});
 const {rows}=metricTable(e([o({count:3,p50:100,p95:200,max:250})]),e([o({count:3,p50:90,p95:210})]));
 const by=Object.fromEntries(rows.map(r=>[r.name,r]));
 assert.equal(by['first_audio_latency.p50'].delta,-10);assert.equal(by['first_audio_latency.p95'].delta,10);
 assert.equal(by['first_audio_latency.p50'].x,'100 ms');assert.equal(by['first_audio_latency.count'].delta,0);
 assert.equal(by['first_audio_latency.max'].y,null);assert.equal(by['first_audio_latency.max'].delta,null);
 assert.ok(rows.every(r=>r.latency));
 const ex=metricTable(e([o({p50:1},'ms','X')]),e([o({p50:2})])).rows[0];assert.equal(ex.delta,null);
});
