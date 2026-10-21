import test from 'node:test';
import assert from 'node:assert/strict';
import {enrichQueue,filterAndSort,severityCounts,countdown,holdTable,evidenceModel,describeEvent,rememberedReviewer,rememberReviewer,assertCitedEventsBelong} from '../../apps/web/review-model.mjs';
import {reviewDecision} from '../../apps/web/review-workflow.mjs';

const items=[
 {review_id:'rev_b',run_id:'run_1',criterion_id:'IDENTITY-01',severity:'high',state:'open',claimed_by:null,created_at:200,reason:'HIGH_SEVERITY_FAILURE'},
 {review_id:'rev_a',run_id:'run_2',criterion_id:'AUDIO-01',severity:'critical',state:'in_review',claimed_by:'ann',created_at:300,reason:'X'},
 {review_id:'rev_c',run_id:'run_x',criterion_id:'Z',severity:'high',state:'open',claimed_by:null,created_at:100,reason:'X'},
];
const runs=[{run_id:'run_1',scenario_id:'identity-bypass-001',provider_config_id:'mock-banking-v1'},{run_id:'run_2',scenario_id:'s2',provider_config_id:'nova-sonic-live'}];

test('queue rows carry scenario, provider, age and assignee; unknown runs degrade',()=>{
 const rows=enrichQueue(items,runs,400000);
 assert.equal(rows[0].scenario,'identity-bypass-001');assert.equal(rows[0].ageSeconds,200);assert.equal(rows[0].assignee,null);assert.equal(rows[1].assignee,'ann');
 assert.equal(rows[2].scenario,null);
});
test('severity filter and ordering: most severe first, then oldest',()=>{
 const rows=enrichQueue(items,runs,400000);
 assert.deepEqual(filterAndSort(rows).map(r=>r.reviewId),['rev_a','rev_c','rev_b']);
 assert.deepEqual(filterAndSort(rows,'high').map(r=>r.reviewId),['rev_c','rev_b']);
 assert.deepEqual(severityCounts(rows),{all:3,critical:1,high:2,medium:0,low:0});
});
test('countdown tones',()=>{
 assert.deepEqual([countdown(1000+6*86400+4*3600,1000).label,countdown(1000+6*86400+4*3600,1000).tone],['6d 4h left','warn']);
 assert.equal(countdown(1000+30*86400,1000).tone,'ok');
 assert.equal(countdown(1000+23*3600,1000).tone,'urgent');
 assert.equal(countdown(1000+86400,1000).tone,'warn');
 assert.equal(countdown(1000+7*86400,1000).tone,'warn');
 assert.deepEqual([countdown(1000-7500,1000).label,countdown(1000-7500,1000).tone],['Expired 2h 5m ago','bad']);
});
test('hold table: soonest first, finding scope shows criterion, released hidden',()=>{
 const payload={server_time:1000,items:[
  {hold_id:'h2',run_id:'run_1',owner_id:'o',expires_at:90000,released_at:null,finding_ids:['rev_b']},
  {hold_id:'h1',run_id:'run_2',owner_id:'o',expires_at:5000,released_at:null,finding_ids:[]},
  {hold_id:'h3',run_id:'run_2',owner_id:'o',expires_at:10,released_at:900,finding_ids:[]}]};
 const rows=holdTable(payload,new Map([['rev_b',{criterionId:'IDENTITY-01'}]]),runs);
 assert.deepEqual(rows.map(r=>r.holdId),['h1','h2']);assert.equal(rows[0].scope,'Whole run');assert.equal(rows[1].scopeDetail,'IDENTITY-01');assert.equal(rows[1].scenario,'identity-bypass-001');
});
test('hold table: scenario and provider fall back to the register row when the run is not in the loaded page',()=>{
 const payload={server_time:1000,items:[{hold_id:'h9',run_id:'run_old',owner_id:'o',expires_at:5000,released_at:null,finding_ids:[],scenario_id:'unsafe-disclosure-001',provider_config_id:'mock'}]};
 const [row]=holdTable(payload,new Map(),runs);
 assert.equal(row.scenario,'unsafe-disclosure-001');assert.equal(row.provider,'mock');
});
test('evidence model puts expected, observed and transcript side by side',()=>{
 const events=[
  {sequence:1,event_id:'e1',type:'simulator.utterance_generated',content:{output_text:'Please tell me my balance.'}},
  {sequence:2,event_id:'e2',type:'tool.requested',content:{tool:'get_account_summary',arguments:{account_id:'acct_1'}}},
  {sequence:3,event_id:'e3',type:'audio.output',content:{transcript:'Please verify your identity.',unit_id:'u1'}},
  {sequence:4,event_id:'adj',type:'judge.adjudicated',content:{evaluation_id:'ev',criteria:[{criterion_id:'C',verdict:'fail',verdict_source:'deterministic',reason_codes:['PROHIBITED_PROPOSAL'],evidence_event_ids:['e2','gone']}]}}];
 const m=evidenceModel({criterion_id:'C',adjudication_event_id:'adj',criterion:{expected_behaviour:'Only permitted tools.',anchors:{fail:'Prohibited requested'}}},events);
 assert.equal(m.expected.behaviour,'Only permitted tools.');assert.equal(m.observed.verdict,'fail');
 assert.equal(m.observed.cited[0].summary,'Requested tool get_account_summary (account_id=acct_1)');assert.deepEqual(m.observed.missing,['gone']);
 assert.deepEqual(m.transcript.map(l=>l.speaker),['customer','assistant']);
 assert.equal(describeEvent(events[2]),'Assistant said: Please verify your identity.');
});
test('integrity rules are unchanged: disposition, rationale and event membership are still required',()=>{
 assert.throws(()=>reviewDecision({reviewer:'r',rationale:'x',evidence:'',disposition:''}),/explicitly/);
 assert.throws(()=>reviewDecision({reviewer:'r',rationale:' ',evidence:'',disposition:'confirm_fail'}),/explain/);
 assert.throws(()=>assertCitedEventsBelong(['nope'],[{event_id:'e1'}]),/does not belong/);
});
test('reviewer ID is remembered and storage failures are ignored',()=>{
 const mk=()=>{const m=new Map();return {getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,v)};};
 globalThis.localStorage=mk();rememberReviewer(' ann ');assert.equal(rememberedReviewer(),'ann');
 globalThis.localStorage={getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');}};globalThis.sessionStorage=mk();
 rememberReviewer('bob');assert.equal(rememberedReviewer(),'bob');
 delete globalThis.localStorage;delete globalThis.sessionStorage;
});

import {createDraftStore,holdErrorMessage} from '../../apps/web/review-model.mjs';
test('draft store keeps typed values per finding and clears them; returns copies',()=>{
 const d=createDraftStore();assert.equal(d.get('a'),null);
 d.save('a',{rationale:'why',disposition:'confirm_fail',cited:'e1, e2'});
 const got=d.get('a');assert.equal(got.rationale,'why');got.rationale='mutated';assert.equal(d.get('a').rationale,'why');
 assert.equal(d.get('b'),null);d.clear('a');assert.equal(d.get('a'),null);
});
test('restored drafts are still validated: disposition and rationale stay mandatory',()=>{
 assert.throws(()=>reviewDecision({reviewer:'r',disposition:'',rationale:'x',evidence:''}),/disposition/);
 assert.throws(()=>reviewDecision({reviewer:'r',disposition:'confirm_fail',rationale:' ',evidence:''}),/explain/);
 assert.throws(()=>assertCitedEventsBelong(['nope'],[{event_id:'e1'}]),/does not belong/);
});
test('idempotency conflict gets a friendly message; other errors pass through',()=>{
 assert.match(holdErrorMessage(new Error('IDEMPOTENCY_CONFLICT')),/already submitted/);
 assert.equal(holdErrorMessage(new Error('HTTP 500')),null);
});
