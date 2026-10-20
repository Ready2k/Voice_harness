import test from 'node:test';
import assert from 'node:assert/strict';
import {runExplanation,causationLinks,viewChanged} from '../../apps/web/ux-insights.mjs';
import {reviewDecision} from '../../apps/web/review-workflow.mjs';
test('run explanation separates verdicts and links only recorded supporting events',()=>{
 const e={event_id:'denied',sequence:2,type:'tool.denied',content:{}};
 const result=runExplanation({assurance:{model_behaviour:'fail',platform_enforcement:'pass'}},[e]);
 assert.match(result.text,/Model behaviour: fail/);assert.match(result.text,/Platform enforcement: pass/);
 assert.deepEqual(result.evidence,[{id:'denied',sequence:2,type:'tool.denied'}]);
 assert.doesNotMatch(result.text,/zero unauthorized|safe|never heard/i);
});
test('newer assessment takes priority; an unassessed run is not passed',()=>{
 assert.match(runExplanation({assurance:{model_behaviour:'pass'}},[],{status:'assessed',model_behaviour:'untestable'}).text,/untestable/);
 assert.match(runExplanation({},[]).text,/No model or platform assessment/);
});
test('only recorded, available causal IDs become inspectable links',()=>{
 const e={event_id:'e1',sequence:1};assert.deepEqual(causationLinks({causation_event_ids:['e1','missing']},[e]),[e]);
 assert.deepEqual(causationLinks({},[e]),[]);
});
test('unchanged views retain their DOM; changes are detected per root',()=>{
 const root={};assert.equal(viewChanged(root,{a:1}),true);assert.equal(viewChanged(root,{a:1}),false);assert.equal(viewChanged(root,{a:2}),true);assert.equal(viewChanged({}, {a:2}),true);
});
test('review decision requires an explicit disposition',()=>{
 assert.throws(()=>reviewDecision({reviewer:'r',rationale:'checked',disposition:'',evidence:''}),/explicitly/);
});
