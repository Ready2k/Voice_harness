import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {summarize,uniqueRuns,taskCompleted,csv} from '../../site/business-metrics.mjs';
test('completed assessment matches published raw numerators and denominators',()=>{
 const d=JSON.parse(fs.readFileSync('artifacts/bakeoff-2026-10-29-standard-5/report.json'));
 for(const row of d.report.scores){
  const s=summarize(d.observations.filter(o=>o.provider===row.provider));
  for(const [field,score] of [['task','task_completion'],['facts','factual'],['assurance','task_success'],['speech','safe_speech']]){
   assert.equal(s[field].k,row[score].k,`${row.provider} ${field} numerator`);
   assert.equal(s[field].n,row[score].n,`${row.provider} ${field} denominator`);
  }
 }
});
test('re-scores replace rather than double-count original observations',()=>{
 const base='artifacts/fair-bakeoff-2026-10-28/';
 const original=JSON.parse(fs.readFileSync(base+'report.json')).observations;
 const revised=JSON.parse(fs.readFileSync(base+'rescored-2026-10-28/report.json')).observations;
 const rows=uniqueRuns([...revised,...original]);assert.equal(rows.length,720);assert.equal(rows[0],revised[0]);
 assert.equal(summarize(rows).task.n,198);
});
test('missing evidence is not converted into safe replies, zero actions or priced cost',()=>{
 const s=summarize([{run_id:'x',completed:true,outcome:'untestable',replies_assessed:3,replies_unsafe:0}]);
 assert.equal(s.speech.n,0);assert.equal(s.prohibited,null);assert.equal(s.cost,null);assert.equal(s.median,null);assert.equal(s.assurance.k,0);
});
test('task eligibility, legacy fallback, and explicit exclusions',()=>{
 const row={completed:true,expected_tools:['cancel'],metrics:{served:true,arguments:[{status:'wrong'}]}};
 assert.equal(taskCompleted(row),false);assert.equal(taskCompleted({...row,metrics:{task_completed:true}}),true);
 assert.equal(taskCompleted({...row,task_completion_excluded:'NO_CLARIFICATION'}),null);
 assert.equal(taskCompleted({...row,expected_tools:[]}),null);assert.equal(taskCompleted({...row,completed:false}),null);
});
test('timing excludes overlaps and cost median excludes retries and partial prices',()=>{
 const s=summarize([{turns:[{first_audio_ms:1000},{first_audio_ms:1,overlapped:true},{first_audio_ms:null}],cost:{priced:true,known_usd:'0.02'},earlier_costs:[{known_usd:'0.01'}]}, {cost:{priced:false,known_usd:'0.03'}}]);
 assert.equal(s.median,1000);assert.equal(s.cost,.02);assert.equal(s.priced,1);assert.ok(Math.abs(s.knownCost-.06)<1e-12);
});
test('CSV escapes commas, quotes and formula-like user-visible content',()=>{
 const output=csv([{scenario:'=SUM(A1)',run_id:'a,"b'}]);assert.ok(output.includes("'=SUM(A1)"));assert.ok(output.includes('a,""b'));
});
