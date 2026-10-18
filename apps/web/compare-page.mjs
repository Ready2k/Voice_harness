import {mountReports} from "./reports.mjs?v=ux-audit-20261018";
import {setConnected} from "./chrome.mjs?v=ux-audit-20261018";
import {api,el,fetchEvents,launchOptions,TERMINAL} from './runs.mjs?v=ux-audit-20261018';
import {createWorkspace} from './live-view.mjs?v=ux-audit-20261018';
import {comparisonCheck,batchPlan,metricMatrix,costDescription,metricDeltas} from './compare.mjs?v=compare2';
import {friendlyError} from './onboarding.mjs?v=ux-audit-20261018';
const $=id=>document.getElementById(id);
const pairWorkspaces={};
let token='',runs=[],nextCursor=null,scenarios=[],providers=[],chosen=new Set(),entries=[],pending=null,timer=null,generation=0,loading=false;
function report(e){$('error').textContent=friendlyError(e);}
function option(label,value){return Object.assign(el('option','',label),{value});}
function table(root,heading,headers,rows){root.replaceChildren(el('h3','',heading));const t=el('table');const head=el('tr');headers.forEach(h=>head.append(el('th','',h)));t.append(head);rows.forEach(row=>{const tr=el('tr');row.forEach(v=>tr.append(el('td','',String(v??'Unavailable'))));t.append(tr);});root.append(t);}
async function loadRuns(more=false){
 const response=await api(token,'/api/v1/runs?limit=200'+(more&&nextCursor!=null?'&cursor='+encodeURIComponent(nextCursor):''));
 runs=more?[...runs,...response.items]:response.items;nextCursor=response.next_cursor;
 $('more').hidden=nextCursor==null;
 const old=$('task').value;const tasks=[...new Set(runs.map(r=>r.scenario_id+'@'+r.scenario_version))];
 $('task').replaceChildren(...tasks.map(t=>option(t,t)));if(tasks.includes(old))$('task').value=old;
 candidates();
}
function candidates(){
 const task=$('task').value;$('candidates').replaceChildren();
 for(const run of runs.filter(r=>r.scenario_id+'@'+r.scenario_version===task)){
  const label=el('label','compare-candidate');const input=el('input');input.type='checkbox';input.value=run.run_id;input.checked=chosen.has(run.run_id);
  input.addEventListener('change',()=>{if(input.checked&&chosen.size>=8){input.checked=false;report(new Error('Select at most 8 runs.'));return;}input.checked?chosen.add(run.run_id):chosen.delete(run.run_id);$('selection').textContent=chosen.size+' selected';});
  const text=el('span');text.append(el('strong','',run.provider_config_id??'Unknown provider'),el('span','muted',run.state+' · '+run.run_id),el('span','muted',run.started_at??'Start time unavailable'));
  label.append(input,text);$('candidates').append(label);
 }
 $('selection').textContent=chosen.size+' selected';
}
function batchChoices(){
 const selected=scenarios[Number($('batch-scenario').value)];$('batch-providers').replaceChildren();
 for(const p of selected?.live?providers:[{provider_config_id:'mock-banking-v1',available:true}]){
  const label=el('label','check');const input=el('input');input.type='checkbox';input.value=p.provider_config_id;input.disabled=!p.available;input.checked=p.available;
  label.append(input,el('span','',p.provider_config_id+(p.available?'':' · Unavailable: '+(p.reason??'Not configured'))));$('batch-providers').append(label);
 }
 if(!selected?.live&&Number($('batch-count').value)<2)$('batch-count').value='2';
 $('batch-launch').disabled=loading||!$('batch-providers').querySelector('input:not(:disabled)');
}
async function connect(){
 ++generation;clearTimeout(timer);$('error').textContent='';token=$('token').value;
 try{
  const [s,p]=await Promise.all([api(token,'/api/v1/scenarios'),api(token,'/api/v1/live-providers')]);
  scenarios=launchOptions(s);providers=p.items;
  $('batch-scenario').replaceChildren(...scenarios.map((s,i)=>option(s.label+(s.live?' · live':' · scripted'),String(i))));batchChoices();
  await loadRuns();mountReports($('stored-reports'),token);setConnected(true);$('controls').hidden=false;$('status').textContent='Connected · Choose a task and at least two runs.';
  try{sessionStorage.setItem('harness-operator-token',token);}catch{}
  const candidate=new URLSearchParams(location.search).get('candidate');
  if(candidate){const r=await api(token,'/api/v1/runs/'+encodeURIComponent(candidate));if(!runs.some(v=>v.run_id===candidate))runs.unshift({...r,...r.config});chosen.add(candidate);const value=r.config.scenario_id+'@'+r.config.scenario_version;if(![...$('task').options].some(o=>o.value===value))$('task').append(option(value,value));$('task').value=value;candidates();}
  const ids=new URLSearchParams(location.search).get('runs');if(ids){$('ids').value=ids;await compareIds(ids.split(','));}
 }catch(e){setConnected(false);$('controls').hidden=true;$('results').hidden=true;report(e);$('status').textContent='Could not connect. Check your token and retry.';}
}
async function fetchEntry(id){
 const base='/api/v1/runs/'+encodeURIComponent(id);
 const run=await api(token,base);
 const [events,metrics,holds,assurance,cost]=await Promise.all([fetchEvents(token,id),api(token,'/api/v1/metrics/'+encodeURIComponent(id)),api(token,'/api/v1/holds-register?state=all&run_id='+encodeURIComponent(id)),api(token,base+'/assurance'),api(token,base+'/cost').catch(e=>({unavailable_reason:e.message}))]);
 return {run,events,metrics,holds,assurance,cost};
}
function pair(){
 const x=$('left').value,y=$('right').value;
 if(x===y){const other=entries.find(e=>e.run.run_id!==x)?.run.run_id;if(!other)return;$('right').value=other;return pair();}
 $('error').textContent='';
 const left=entries.find(e=>e.run.run_id===x),right=entries.find(e=>e.run.run_id===y);
 if(left&&right)table($('deltas'),'Y − X · observed differences, not statistical significance',['Metric','Difference'],metricDeltas(left,right).map(m=>[m.name,m.delta==null?m.reason:(m.delta>0?'+':'')+m.delta+' '+m.unit]));
 for(const [side,id] of [['left',x],['right',y]]){
  const entry=entries.find(e=>e.run.run_id===id);if(!entry)continue;
  if(pairWorkspaces[side]?.id!==id)pairWorkspaces[side]={id,view:createWorkspace($(side+'-trace'),null)};
  pairWorkspaces[side].view.update(entry.run,entry.events,entry.metrics,entry.holds,entry.assurance,scenarios.find(s=>s.id===entry.run.config.scenario_id)?.title);
  // Comparison is inspection-only. Cancellation belongs to the run workspace.
  $(side+'-trace').querySelectorAll('button').forEach(b=>{if(b.textContent==='Cancel evaluation')b.hidden=true;});
  $(side+'-link').href='./runs.html?run='+encodeURIComponent(id);
 }
}
async function compareIds(ids,refresh=false){
 ids=ids.map(x=>x.trim()).filter(Boolean);
 if(ids.length<2||ids.length>8||new Set(ids).size!==ids.length){report(new Error('Choose 2–8 distinct run IDs.'));return;}
 clearTimeout(timer);const mine=++generation;$('status').textContent='Loading recorded comparison evidence…';$('error').textContent='';
 try{
  const loaded=await Promise.all(ids.map(fetchEntry));if(mine!==generation)return;
  const match=comparisonCheck(loaded.map(e=>e.run));entries=loaded;
  if(!refresh){
   chosen=new Set(ids);
   for(const {run} of entries)if(!runs.some(r=>r.run_id===run.run_id))runs.unshift({...run,...run.config});
   const task=entries[0].run.config.scenario_id+'@'+entries[0].run.config.scenario_version;
   if(![...$('task').options].some(o=>o.value===task))$('task').append(option(task,task));
   $('task').value=task;candidates();
  }
  $('results').hidden=false;
  $('comparability').textContent=match.differences.length?'Same task, different settings: '+match.differences.join(', ')+'. Treat this as a configuration comparison, not an isolated provider effect.':'Same scenario, version, seed and recorded execution settings. Provider configurations may differ. Model versions and provider session settings must be checked in the event evidence; matching these fields alone does not establish full equivalence.';
  if(match.missing.length)$('comparability').textContent+=' Missing configuration evidence: '+match.missing.join(', ')+'.';
  $('snapshot').textContent='Evidence fetched '+new Date().toISOString()+'. Descriptive comparison; no winner, statistical significance or population-level claim. Counts reflect selected runs only.';
  const counts=new Map();entries.forEach(e=>{const k=e.run.config.provider_config_id;counts.set(k,(counts.get(k)??0)+1);});
  $('status').textContent='Selected samples: '+[...counts].map(([p,n])=>p+' n='+n).join(' · ');
  const names=entries.map((e,i)=>(i+1)+': '+e.run.run_id);
  table($('summary'),'Outcomes & configuration',['Measure',...names],[
   ['Provider configuration',...entries.map(e=>e.run.config.provider_config_id)],
   ['Requested model',...entries.map(e=>e.events.find(v=>v.type==='run.live_binding')?.content?.provider?.requested_model_id??'Not recorded / fixture')],
   ['Returned model',...entries.map(e=>e.events.find(v=>v.type==='run.live_binding')?.content?.provider?.returned_model_id??'Unresolved / fixture')],
   ['Lifecycle (not task success)',...entries.map(e=>e.run.state)],
   ['Model behaviour',...entries.map(e=>(e.assurance?.status==='assessed'?e.assurance.model_behaviour:e.run.assurance?.model_behaviour)??'Not assessed')],
   ['Platform enforcement',...entries.map(e=>(e.assurance?.status==='assessed'?e.assurance.platform_enforcement:e.run.assurance?.platform_enforcement)??'Not assessed')],
   ['Overall assurance',...entries.map(e=>e.assurance?.overall_assurance??e.run.assurance?.overall_assurance??'Not assessed')],
   ['Seed',...entries.map(e=>e.run.config.fixture_seed)],['Policy',...entries.map(e=>e.run.config.policy_profile)],
   ['Input / client mode',...entries.map(e=>(e.run.config.input_mode??'Unknown')+' / '+(e.run.config.client_mode??'Unknown'))],
   ['Audio retention requested',...entries.map(e=>String(e.run.config.retain_audio??'Unknown'))],
   ['Termination',...entries.map(e=>e.run.termination_reason??'Not terminal')],
  ]);
  table($('metrics'),'Recorded metrics · missing measurements stay unavailable',['Metric',...names],metricMatrix(entries).map(m=>[m.name,...m.values]));
  table($('costs'),'Costs · currencies and provenance remain separate',['Run','Cost statement'],entries.map(e=>[e.run.run_id,e.cost?.unavailable_reason?'Unavailable: '+e.cost.unavailable_reason:costDescription(e.cost)]));
  const previous=[$('left').value,$('right').value];
  for(const side of ['left','right'])$(side).replaceChildren(...entries.map((e,i)=>option((i+1)+': '+e.run.config.provider_config_id+' · '+e.run.run_id,e.run.run_id)));
  $('left').value=ids.includes(previous[0])?previous[0]:ids[0];$('right').value=ids.includes(previous[1])?previous[1]:ids[1];pair();
  const url=new URL(location.href);url.searchParams.set('runs',ids.join(','));history.replaceState(null,'',url);
  if(entries.some(e=>!TERMINAL.has(e.run.state)))timer=setTimeout(()=>compareIds(ids,true),5000);
 }catch(e){if(mine!==generation)return;report(e);$('status').textContent='Comparison could not be loaded. Previously loaded results, if present, are unchanged.';}
}
function savePlan(){try{sessionStorage.setItem('harness-comparison-batch',JSON.stringify(pending));}catch{}}
async function launchPending(){
 if(loading||!pending)return;loading=true;$('batch-launch').disabled=true;$('batch-retry').hidden=true;$('error').textContent='';
 try{
  for(const job of pending){
   if(job.run_ids)continue;
   $('batch-progress').textContent='Launching '+job.body.provider_config_id+' · seed '+job.body.fixture_seed+'. Completed launches: '+pending.filter(j=>j.run_ids).length+' / '+pending.length;
   const result=await api(token,'/api/v1/runs',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':job.key},body:JSON.stringify(job.body)});
   job.run_ids=result.run_ids;savePlan();
  }
  const ids=pending.flatMap(j=>j.run_ids);$('batch-progress').textContent='Created '+ids.length+' runs. Results refresh every 5 seconds while runs remain active.';
  $('ids').value=ids.join(',');await loadRuns();await compareIds(ids);pending=null;try{sessionStorage.removeItem('harness-comparison-batch');}catch{}
 }catch(e){report(e);$('batch-progress').textContent='Batch partially launched: '+pending.filter(j=>j.run_ids).length+' / '+pending.length+'. Retry reuses the original launch keys to avoid duplicate runs.';$('batch-retry').hidden=false;}
 finally{loading=false;$('batch-launch').disabled=false;}
}
$('auth').addEventListener('submit',e=>{e.preventDefault();connect();});
$('task').addEventListener('change',()=>{chosen.clear();candidates();});
$('refresh').addEventListener('click',()=>loadRuns().catch(report));$('more').addEventListener('click',()=>loadRuns(true).catch(report));
$('compare').addEventListener('click',()=>compareIds([...chosen]));$('open-ids').addEventListener('click',()=>compareIds($('ids').value.split(',')));
$('left').addEventListener('change',pair);$('right').addEventListener('change',pair);
$('update').addEventListener('click',()=>compareIds(entries.map(e=>e.run.run_id),true));
$('batch-scenario').addEventListener('change',batchChoices);
$('batch').addEventListener('submit',e=>{e.preventDefault();if(loading)return;if(pending){report(new Error('Finish the pending batch with Retry unfinished launches before starting another.'));return;}try{pending=batchPlan(scenarios[Number($('batch-scenario').value)],[...$('batch-providers').querySelectorAll('input:checked')].map(e=>e.value),Number($('batch-seed').value),Number($('batch-count').value),$('batch-retain').checked,'compare-'+crypto.randomUUID());savePlan();launchPending();}catch(e){pending=null;report(e);}});
$('batch-retry').addEventListener('click',launchPending);
$('export').addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify({export_type:'descriptive_run_comparison_snapshot',captured_at:new Date().toISOString(),statement:'Recorded run evidence; no ranking or statistical inference. This snapshot is not a signed evidence bundle.',comparability:comparisonCheck(entries.map(e=>e.run)),entries},null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='run-comparison.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});
try{pending=JSON.parse(sessionStorage.getItem('harness-comparison-batch')??'null');if(pending){$('batch-retry').hidden=false;$('batch-progress').textContent='An unfinished comparison batch is saved in this tab. Connect, then retry its unfinished launches.';}const saved=sessionStorage.getItem('harness-operator-token');if(saved){$('token').value=saved;connect();}}catch{}
