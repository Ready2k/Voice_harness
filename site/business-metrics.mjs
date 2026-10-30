// Raw observation counts. These intentionally do not recreate weighted report scores.
export const recorded = x => typeof x === 'number' && Number.isFinite(x);
export function taskCompleted(o) {
  if (!o.completed || !o.expected_tools?.length || o.task_completion_excluded) return null;
  const m = o.metrics;
  if (m && 'task_completed' in m) return Boolean(m.task_completed);
  if (m && Object.keys(m).length) return Boolean(m.served) && !(m.arguments || []).some(a => a.status === 'wrong');
  return typeof o.expected_tools_observed === 'boolean' ? o.expected_tools_observed : null;
}
export function uniqueRuns(rows) {
  const seen = new Set();
  return rows.filter(o => { const key = o.run_id || `${o.study}:${o.provider}:${o.scenario_id}:${o.repetition}`; if (seen.has(key)) return false; seen.add(key); return true; });
}
export function quantile(values, q) {
  const a = values.filter(recorded).sort((a,b)=>a-b);
  if (!a.length) return null;
  const x = (a.length-1)*q, i = Math.floor(x);
  return a[i] + (a[Math.ceil(x)]-a[i])*(x-i);
}
const rate = (k,n) => ({k,n,value:n ? k/n : null});
export function summarize(rows) {
  const done = rows.filter(o=>o.completed === true), task = rows.map(taskCompleted).filter(x=>x !== null);
  const facts = done.filter(o=>o.criteria?.['FACT-01'] && !o.criterion_reasons?.['FACT-01']?.includes('NO_AMOUNT_CLAIM'));
  const speech = rows.filter(o=>recorded(o.replies_safe) && recorded(o.replies_assessed));
  const protection = rows.filter(o=>recorded(o.prohibited_executed));
  const turns = rows.flatMap(o=>o.turns || []), times = turns.filter(t=>!t.overlapped && recorded(t.first_audio_ms));
  const priced = rows.filter(o=>o.cost?.priced && Number.isFinite(Number(o.cost.known_usd)));
  const metrics = rows.filter(o=>o.metrics), tools = metrics.filter(o=>o.metrics.tools);
  const sum = (a,fn) => a.reduce((s,o)=>s+(fn(o) || 0),0);
  const judged = rows.filter(o=>o.assessors), attempts = rows.filter(o=>recorded(o.attempts));
  return {runs:rows.length, completed:rate(done.length,rows.length), assurance:rate(done.filter(o=>o.outcome==='pass').length,done.length),
    task:rate(task.filter(Boolean).length,task.length), taskExcluded:rows.filter(o=>o.task_completion_excluded).length,
    facts:rate(facts.filter(o=>o.criteria['FACT-01']==='pass').length,facts.length),
    speech:rate(sum(speech,o=>o.replies_safe),sum(speech,o=>o.replies_assessed)), speechRuns:speech.length,
    unsafe:sum(rows,o=>o.replies_unsafe), untestable:sum(rows,o=>o.replies_untestable),
    prohibited:protection.length ? sum(protection,o=>o.prohibited_executed) : null, protectionRuns:protection.length,
    firstTry:rate(attempts.filter(o=>o.completed && o.attempts===1).length,attempts.length), retries:sum(attempts,o=>Math.max(0,o.attempts-1)),
    median:quantile(times.map(t=>t.first_audio_ms),.5), p90:quantile(times.map(t=>t.first_audio_ms),.9), timed:times.length,
    estimated:times.filter(t=>t.estimated).length, overlaps:turns.filter(t=>t.overlapped).length,
    cost:quantile(priced.map(o=>Number(o.cost.known_usd)),.5), priced:priced.length,
    knownCost:sum(rows,o=>Number(o.cost?.known_usd))+sum(rows,o=>sum(o.earlier_costs||[],c=>Number(c.known_usd))),
    costCoverage:rows.filter(o=>o.cost).length, metrics:metrics.length,
    tools:Object.fromEntries(['executed','missing','extra','unexpected','redundant','failed','denied'].map(k=>[k,sum(tools,o=>o.metrics.tools[k])])), toolRuns:tools.length,
    judged:rate(judged.filter(o=>o.assessors.judges?.some(j=>j.status==='ok')).length,judged.length),
    crossFamily:rate(judged.filter(o=>o.assessors.cross_family_judge_present===true).length,judged.length),
    outcomes:Object.fromEntries([...new Set(rows.map(o=>o.outcome || 'not_recorded'))].map(k=>[k,rows.filter(o=>(o.outcome||'not_recorded')===k).length]))};
}
export function csv(rows) {
  const columns=['experiment','run_id','provider','scenario_id','scenario','group','audio_profile','outcome','completed','attempts','task_completed','task_completion_excluded','prohibited_executed','failure'];
  const escape=v=>'"'+String(v??'').replace(/^[=+@-]/,"'$&").replaceAll('"','""')+'"';
  return [columns, ...rows.map(o=>columns.map(k=>k==='task_completed'?taskCompleted(o):o[k]))].map(r=>r.map(escape).join(',')).join('\r\n');
}
