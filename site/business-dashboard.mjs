import {summarize, uniqueRuns, taskCompleted, quantile, csv, recorded} from './business-metrics.mjs';
const $=id=>document.getElementById(id), esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=x=>x==null?'Not recorded':Number(x).toLocaleString('en-GB',{maximumFractionDigits:2});
const usd=x=>x==null?'Not recorded':`$${Number(x).toFixed(4)}`;
const ms=x=>x==null?'Not recorded':`${(x/1000).toFixed(2)} s`;
const pct=x=>x==null?'Not available':`${(x*100).toFixed(1)}%`;
const ratio=r=>r.n?`${pct(r.value)}<small>${r.k} / ${r.n}</small><span class="bar" aria-hidden="true"><i style="width:${Math.min(100,r.value*100)}%"></i></span>`:'—<small>No eligible evidence</small>';
const table=(title,headers,rows)=>`<div class="table-wrap"><table><caption>${esc(title)}</caption><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(r=>`<tr>${r.map((v,i)=>`<td${i?' class="metric"':''}>${v}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${headers.length}">No matching evidence.</td></tr>`}</tbody></table></div>`;
const panel=(title,values,note='')=>`<article class="detail-panel"><h3>${esc(title)}</h3><dl>${values.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>${note?`<p>${esc(note)}</p>`:''}</article>`;
let manifest, loaded=[], rows=[], filtered=[], page=0, generation=0;
const cache=new Map();
function options(id,values,label='All') { const old=$(id).value; $(id).innerHTML=`<option value="">${label}</option>`+[...new Set(values)].filter(Boolean).sort().map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join(''); if([...$(id).options].some(o=>o.value===old)) $(id).value=old; }
async function fetchData(version) {
  if(!cache.has(version.data)) cache.set(version.data,(async()=>{
    const response=await fetch(version.data); if(!response.ok) throw Error(`Could not load ${version.data}`);
    if(version.data.endsWith('.json')) return response.json();
    const doc=new DOMParser().parseFromString(await response.text(),'text/html');
    const embedded=doc.getElementById('report-data'); if(!embedded) throw Error('Missing assessment data');
    return JSON.parse(embedded.textContent);
  })().catch(e=>{cache.delete(version.data);throw e;}));
  return cache.get(version.data);
}
function setVersions() {
 const study=manifest.studies.find(s=>s.id===$('experiment').value);
 $('version').innerHTML=study?study.versions.map((v,i)=>`<option value="${i}">${esc(v.label)}${i===0?' · preferred':''}</option>`).join(''):'<option value="0">Preferred version per experiment</option>';
 $('version').disabled=!study;
}
async function load() {
 const token=++generation; $('status').textContent='Loading assessment evidence…'; $('content').hidden=true;
 try {
  const selected=manifest.studies.filter(s=>$('experiment').value==='all'||s.id===$('experiment').value);
  const result=await Promise.all(selected.map(async study=>{const version=study.versions[$('experiment').value==='all'?0:Number($('version').value)];return {study,version,data:await fetchData(version)};}));
  if(token!==generation)return;
  loaded=result;
  rows=uniqueRuns(result.flatMap(({study,version,data})=>data.observations.map(o=>{
   const config=data.report.limits?.provider_disclosures?.providers?.find(c=>c.provider===o.provider)||{};
   return {...o,study:study.id,experiment:data.title,reportPath:version.path,providerName:data.names[o.provider]||o.provider,
    model:config.model_id||'Not recorded',prompt:o.parity?.prompt_profile||data.report.limits?.provider_disclosures?.prompt_profile||'Not recorded',voice:o.parity?.voice||config.voice||'Not recorded'};
  })));
  options('provider',rows.map(o=>o.providerName),'All services');options('group',rows.map(o=>o.group),'All groups');options('audio',rows.map(o=>o.audio_profile),'All audio conditions');options('outcome',rows.map(o=>o.outcome),'All outcomes');
  $('selection-note').textContent=$('experiment').value==='all'?'Preferred versions only. Duplicate run IDs are counted once; results remain separated by experiment. No pooled quality or speed ranking.':`${result[0].version.state} · ${result[0].version.runs} of ${result[0].version.planned} planned cells observed · ${result[0].version.label}. ${result[0].version.runs===0?'Preflight records are excluded from scored outcomes.':''}`;
  $('content').hidden=false; render();
 }catch(e){if(token===generation){$('status').textContent=`${e.message}. Reload or choose another experiment; partial data is not displayed.`;}}
}
function groupsByStudy(items){return loaded.map(x=>({...x,items:items.filter(o=>o.study===x.study.id)}));}
function render(){
 const query=$('search').value.toLowerCase().trim();
 filtered=rows.filter(o=>(!$('provider').value||o.providerName===$('provider').value)&&(!$('group').value||o.group===$('group').value)&&(!$('audio').value||o.audio_profile===$('audio').value)&&(!$('outcome').value||o.outcome===$('outcome').value)&&(!query||[o.scenario,o.scenario_id,o.run_id,o.providerName,o.model,o.failure,o.prompt].join(' ').toLowerCase().includes(query)));
 const all=$('experiment').value==='all', s=summarize(filtered);
 $('status').textContent=`${num(filtered.length)} matching runs of ${num(rows.length)} unique runs in this selection.`;
 $('scope').textContent='Overview and breakdowns follow your run filters';
 const kpi=(label,value,note)=>`<article class="kpi"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`;
 $('kpis').innerHTML=all?[
  kpi('Unique recorded runs',num(s.runs),'Repeated assessment versions counted once'),kpi('Experiments represented',new Set(filtered.map(o=>o.study)).size,'Different setups stay separate below'),kpi('Configured services',new Set(filtered.map(o=>o.provider)).size,'Historical configurations differ'),kpi('Customer situations',new Set(filtered.map(o=>o.scenario_id)).size,'Distinct recorded scenario IDs')
 ].join(''):[
  kpi('Recorded runs',num(s.runs),`${s.completed.k} completed · ${s.retries} retry attempts recorded`),
  kpi('Task completion',s.task.n?`${s.task.k} / ${s.task.n}`:'Not available','Eligible completed runs with expected tools'),
  kpi('Prohibited actions',num(s.prohibited),`Recorded in ${s.protectionRuns} / ${s.runs} runs`),
  kpi('Factual checks',s.facts.n?`${s.facts.k} / ${s.facts.n}`:'Not available','Passes / applicable checks; no-claim passes excluded'),
  kpi('Assurance passes',`${s.assurance.k} / ${s.assurance.n}`,'Completed runs; unresolved outcomes are not passes'),
  kpi('Explicitly safe replies',s.speech.n?`${s.speech.k} / ${s.speech.n}`:'Not recorded',`Explicit safe counts present in ${s.speechRuns} runs`),
  kpi('First audio · median',ms(s.median),`${s.timed} non-overlapping measured turns · descriptive only`),
  kpi('Voice cost · median',usd(s.cost),`${s.priced} fully priced final runs; retry costs excluded from median`)
 ].join('');
 const verdict=loaded[0]?.data.report.verdict;
 $('finding').innerHTML=all?'<strong>Explore the differences; keep the experiments distinct.</strong><p>Only archive counts are combined here. The tables below retain each experiment’s context, denominators and missing evidence.</p>':`<strong>${loaded[0].version.runs===0?'No scored runs yet.':verdict?.no_clear_winner===true?'The published assessment finds no clear quality winner.':'Read the published verdict in context.'}</strong><p>This statement describes the whole selected assessment, not the filtered subset. <a href="${esc(loaded[0].version.path)}">Open the source report ↗</a></p>`;
 renderComparison();renderDetails();renderPublished();page=0;renderRuns();
}
function renderComparison(){
 const key=$('breakdown').value;
 $('comparison').innerHTML=groupsByStudy(filtered).map(({data,items})=>{
  const groups=new Map();for(const o of items){const value=key==='provider'?o.providerName:String(o[key]??'Not recorded');if(!groups.has(value))groups.set(value,[]);groups.get(value).push(o);}
  return table(data.title,[$('breakdown').selectedOptions[0].textContent,'Runs','Task completion','Assurance','Factual checks','Safe replies','Prohibited actions','First audio · median / p90','Median voice cost'],[...groups].sort(([a],[b])=>a.localeCompare(b)).map(([label,a])=>{const s=summarize(a);return [esc(label),num(s.runs),ratio(s.task),ratio(s.assurance),ratio(s.facts),ratio(s.speech),`${num(s.prohibited)}<small>${s.protectionRuns} runs recorded</small>`,`${ms(s.median)} / ${ms(s.p90)}<small>${s.timed} turns · ${s.estimated} estimated</small>`,`${usd(s.cost)}<small>${s.priced} / ${s.runs} final runs fully priced</small>`];}));
 }).join('');
}
function renderDetails(){
 $('details').innerHTML=groupsByStudy(filtered).map(({data,items})=>{
  const s=summarize(items), mturns=items.flatMap(o=>o.metrics?.turns||[]), known=key=>mturns.filter(t=>recorded(t[key]));
  const codes=[...new Set(items.flatMap(o=>Object.keys(o.criteria||{})))].sort();
  const checks=codes.map(code=>{const a=items.filter(o=>code in (o.criteria||{})), counts={};a.forEach(o=>counts[o.criteria[code]]=(counts[o.criteria[code]]||0)+1);return [esc(code),num(a.length),...['pass','fail','review_required','untestable'].map(k=>num(counts[k]||0)),esc(Object.entries(counts).filter(([k])=>!['pass','fail','review_required','untestable'].includes(k)).map(([k,v])=>`${k}: ${v}`).join(', ')||'—')];});
  const repeated=new Map();
  for(const o of items){const key=`${o.provider}:${o.scenario_id}`;if(!repeated.has(key))repeated.set(key,[]);repeated.get(key).push(o);}
  const repeatGroups=[...repeated.values()].filter(a=>new Set(a.map(o=>o.repetition)).size>1);
  const varied=repeatGroups.filter(a=>new Set(a.map(o=>o.outcome)).size>1).length;
  const argumentsRecorded=items.flatMap(o=>o.metrics?.arguments||[]);
  const noisy=items.filter(o=>o.completed && o.audio_profile && o.audio_profile!=='clean_studio');
  const unsafeRuns=items.filter(o=>recorded(o.replies_unsafe)), untestableRuns=items.filter(o=>recorded(o.replies_untestable));
  const configs=[...new Set(items.map(o=>`${o.model} · ${o.prompt} · ${o.voice} · ${o.turn_commit||'turn control not recorded'}`))];
  return `<details ${loaded.length===1?'open':''}><summary>${esc(data.title)} · ${items.length} filtered runs</summary><div class="detail-grid">`+
   panel('Availability & assessment', [['Completed runs',`${s.completed.k} / ${s.completed.n}`],['First-attempt completion',`${s.firstTry.k} / ${s.firstTry.n} with attempt count`],['Retry attempts',s.retries],['Task exclusions',s.taskExcluded],...Object.entries(s.outcomes).map(([k,v])=>[`Outcome · ${k}`,v])])+
   panel('Tools & customer requests', [['Runs with tool metrics',`${s.toolRuns} / ${s.runs}`],...Object.entries(s.tools).map(([k,v])=>[`${k} tool calls`,s.toolRuns?v:'Not recorded']),['Legitimate requests unserved',`${items.filter(o=>o.metrics?.legitimate_request && o.metrics.served===false).length} / ${items.filter(o=>o.metrics?.legitimate_request && typeof o.metrics.served==='boolean').length}`]],'Unexpected or extra calls do not automatically mean a prohibited action. Counts describe recorded tool metrics.')+
   panel('Conversation & timing',[['Measured first-audio turns',s.timed],['First audio · median',ms(s.median)],['First audio · p90',ms(s.p90)],['Estimated timing samples',s.estimated],['Overlapping turns excluded',s.overlaps],['Reply duration · median',ms(quantile(known('reply_ms').map(t=>t.reply_ms),.5))],['Long gaps recorded',known('long_gaps').length?known('long_gaps').reduce((n,t)=>n+t.long_gaps,0):'Not recorded'],['Reply words · median',num(quantile(known('reply_words').map(t=>t.reply_words),.5))]],'Different timing anchors and turn controls prevent a fair speed ranking. Reply metrics are present only in newer exports.')+
   panel('Protection, consistency & difficult conditions',[
    ['Unsafe replies',unsafeRuns.length?`${unsafeRuns.reduce((n,o)=>n+o.replies_unsafe,0)} in ${unsafeRuns.length} reporting runs`:'Not recorded'],
    ['Untestable replies',untestableRuns.length?`${untestableRuns.reduce((n,o)=>n+o.replies_untestable,0)} in ${untestableRuns.length} reporting runs`:'Not recorded'],
    ['Noisy / telephone assurance passes',`${noisy.filter(o=>o.outcome==='pass').length} / ${noisy.length}`],
    ['Repeated service / scenario groups',repeatGroups.length],['Groups with varying outcomes',repeatGroups.length?`${varied} / ${repeatGroups.length}`:'No repeated groups'],
    ['Wrong declared tool arguments',argumentsRecorded.length?`${argumentsRecorded.filter(a=>a.status==='wrong').length} / ${argumentsRecorded.length} argument records`:'Not recorded']],
    'Consistency describes the repetitions in the current filter. It does not imply statistical significance or robustness in production.')+
   panel('Cost & evidence coverage',[['Final runs fully priced',`${s.priced} / ${s.runs}`],['Priced final-run median',usd(s.cost)],['Known voice spend incl. retries',s.costCoverage?usd(s.knownCost):'Not recorded'],['Runs with cost records',`${s.costCoverage} / ${s.runs}`],['Runs with successful judge',`${s.judged.k} / ${s.judged.n} with assessor records`],['Cross-family judge recorded',`${s.crossFamily.k} / ${s.crossFamily.n}`],['Runs with explicit safe-reply counts',`${s.speechRuns} / ${s.runs}`]],'Known spend is a lower bound where coverage is incomplete. It is not cost per resolved task. Judge presence is not proof of calibration.')+
  `</div>${table('Every recorded assurance criterion (includes no-claim checks)', ['Criterion','Recorded','Pass','Fail','Review required','Untestable','Other outcomes'],checks)}<details><summary>Recorded configurations &amp; assessment limitations</summary><ul>${configs.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><pre>${esc(JSON.stringify(data.report.limits||{},null,2))}</pre></details></details>`;
 }).join('');
}
function renderPublished(){
 $('published').innerHTML=loaded.map(({data,version})=>table(data.title,['Service','Quality excluding latency','Scenario-cluster interval','Task completion','Factual accuracy','Published voice cost median'],(data.report.scores||[]).map(o=>{
  const cost=data.report.cost?.find(c=>c.provider===o.provider);
  return [esc(o.name||data.names[o.provider]),pct(o.composite_excluding_latency),o.composite_excluding_latency_lo==null?'Not recorded':`${pct(o.composite_excluding_latency_lo)} – ${pct(o.composite_excluding_latency_hi)}`,o.task_completion?ratio(o.task_completion):'Not recorded',o.factual?ratio(o.factual):'Not recorded',`${usd(cost?.median_usd)}<small>${esc(cost?.status||'Not recorded')} · may include retries / separate cost runs</small>`];
 }))+`<p class="note"><a href="${esc(version.path)}">Source assessment ↗</a> · ${esc(version.label)} · ${esc(data.generated_at)}. Published rates may be group-balanced; their percentages need not equal the displayed raw counts.</p>`).join('');
}
function renderRuns(){
 const total=Math.ceil(filtered.length/30), start=page*30;
 $('runs').innerHTML=table('Matching runs · 30 per page',['Run / scenario','Experiment','Service / model','Outcome','Task','Attempts','Evidence'],filtered.slice(start,start+30).map((o,i)=>[
  `<button class="run-link" data-run="${start+i}">${esc(o.scenario||o.scenario_id)}</button><small>${esc(o.run_id)}</small>`,esc(o.experiment),`${esc(o.providerName)}<small>${esc(o.model)}</small>`,`<span class="pill ${esc(o.outcome)}">${esc(o.outcome||'Not recorded')}</span>`,taskCompleted(o)===null?`Not scored<small>${esc(o.task_completion_excluded||'No eligible task evidence')}</small>`:taskCompleted(o)?'Completed':'Not completed',num(o.attempts),`<a href="${esc(o.reportPath)}">Report ↗</a>`]));
 $('page-count').textContent=total?`Page ${page+1} of ${total} · ${filtered.length} runs`:'No matching runs';$('previous').disabled=page===0;$('next').disabled=page+1>=total;$('export').disabled=!filtered.length;
}
function showRun(index){
 const o=filtered[index]; if(!o)return;
 $('run-title').textContent=o.scenario||o.scenario_id;
 const sections=[['Assurance checks',{criteria:o.criteria,reasons:o.criterion_reasons,task_exclusion:o.task_completion_excluded,failure:o.failure}],['Tools & customer metrics',o.metrics],['Turn timing',o.turns],['Costs & retries',{cost:o.cost,earlier_costs:o.earlier_costs,attempts:o.attempts,earlier_failures:o.earlier_failures}],['Assessor evidence',o.assessors],['Configuration',o.parity],['Full retained observation',Object.fromEntries(Object.entries(o).filter(([k])=>!['study','experiment','reportPath','providerName','model','prompt','voice'].includes(k)))]];
 $('run-detail').innerHTML=`<p>${esc(o.providerName)} · ${esc(o.model)} · <span class="pill">${esc(o.outcome)}</span></p><p><code>${esc(o.run_id)}</code></p><p>${esc(o.experiment)} · <a href="${esc(o.reportPath)}">Open source assessment ↗</a></p>`+sections.map(([label,value],i)=>`<details ${i===0?'open':''}><summary>${esc(label)}</summary><pre>${esc(value==null?'Not recorded in this export.':JSON.stringify(value,null,2))}</pre></details>`).join('');
 $('run-dialog').showModal();
}
$('experiment').addEventListener('change',()=>{setVersions();load();});$('version').addEventListener('change',load);
for(const id of ['provider','group','audio','outcome','search']) $(id).addEventListener(id==='search'?'input':'change',render);
$('breakdown').addEventListener('change',renderComparison);
$('reset').addEventListener('click',()=>{for(const id of ['provider','group','audio','outcome','search'])$(id).value='';$('breakdown').value='provider';render();});
$('previous').addEventListener('click',()=>{if(page>0){page--;renderRuns();}});$('next').addEventListener('click',()=>{if((page+1)*30<filtered.length){page++;renderRuns();}});
$('runs').addEventListener('click',e=>{const b=e.target.closest('[data-run]');if(b)showRun(Number(b.dataset.run));});
$('close-dialog').addEventListener('click',()=>$('run-dialog').close());
$('export').addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([csv(filtered)],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='voice-harness-filtered-runs.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
try {
 const response=await fetch('site/business-dashboard.json');if(!response.ok)throw Error('Dashboard catalogue unavailable');manifest=await response.json();
 $('experiment').innerHTML=manifest.studies.map((s,i)=>`<option value="${s.id}">${i===0?'LATEST · ':''}${esc(s.versions[0].title)}${s.versions[0].runs===0?' · no scored runs':''}</option>`).join('')+'<option value="all">All experiments · deduplicated overview</option>';
 $('experiment').disabled=false;setVersions();$('coverage-note').textContent=manifest.scope;
 $('archive-stats').innerHTML=`<span><strong>${manifest.studies.length}</strong> retained experiments</span><span><strong>${manifest.studies.reduce((n,s)=>n+s.versions.length,0)}</strong> assessment versions</span><span>Re-scores linked · individual runs inspectable</span>`;
 $('sources').innerHTML=manifest.studies.map(s=>s.versions.map((v,i)=>`<div class="source"><a href="${esc(v.path)}">${esc(v.title)} ↗</a><small>${esc(v.label)}${i===0?' · preferred':''} · ${esc(v.state)} · ${v.runs} / ${v.planned} cells</small><small>Source: ${esc(s.source)} · <a href="${esc(v.data)}">Retained data</a></small><small>SHA-256: ${v.sha256}</small></div>`).join('')).join('');
 await load();
}catch(e){$('status').textContent=`${e.message}. Please reload or use the research archive.`;}
