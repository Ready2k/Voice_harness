import {providerName,providerEl} from './providers.mjs?v=ux-final-20261023';
import {scrollRegion,shortId,idCell,runLink,relativeTime,timeEl,humanise,rawToggle} from './format.mjs?v=ux-final-20261023';
import {revealResult} from './nav.mjs?v=ux-final-20261023';
import {mountReports} from "./reports.mjs?v=ux-final-20261023";
import {setConnected} from "./chrome.mjs?v=ux-final-20261023";
import {api,el,fetchEvents,launchOptions,TERMINAL,transcript,runResult,durationLabel} from './runs.mjs?v=ux-final-20261023';
import {createWorkspace} from './live-view.mjs?v=ux-final-20261023';
import {comparisonCheck,batchPlan,metricMatrix,costDescription,metricDeltas,taskGroups,taskLabel,defaultTask,latestPerProvider,repeatsOfProvider,isLive,metricTable,alignTurns,hasRevisions} from './compare.mjs?v=ux-final-20261023';
import {friendlyError} from './onboarding.mjs?v=ux-final-20261023';
const $=id=>document.getElementById(id);
const pairWorkspaces={};
let showRevisions=false,token='',runs=[],nextCursor=null,scenarios=[],providers=[],chosen=new Set(),entries=[],pending=null,timer=null,generation=0,loading=false;
function report(e){$('error').textContent=friendlyError(e);}
function option(label,value){return Object.assign(el('option','',label),{value});}
function table(root,heading,headers,rows){root.replaceChildren(el('h3','',heading));const t=el('table');const head=el('tr');headers.forEach(h=>head.append(el('th','',h)));t.append(head);rows.forEach(row=>{const tr=el('tr');row.forEach(v=>{const td=el('td','');if(v&&typeof v==='object'&&v.nodeType)td.append(v);else td.textContent=String(v??'Unavailable');tr.append(td);});t.append(tr);});root.append(scrollRegion(Object.assign(el('div','table-scroll'),{}),heading+' table'));root.lastChild.append(t);}
async function loadRuns(more=false){
 const response=await api(token,'/api/v1/runs?limit=200'+(more&&nextCursor!=null?'&cursor='+encodeURIComponent(nextCursor):''));
 runs=more?[...runs,...response.items]:response.items;nextCursor=response.next_cursor;
 $('more').hidden=nextCursor==null;
 const old=$('task').value;const groups=taskGroups(runs);
 $('task').replaceChildren(...groups.map(g=>option(taskLabel(g),g.key)));
 $('task').value=groups.some(g=>g.key===old)?old:(defaultTask(runs)??'');
 candidates();
}
function select(ids){chosen=new Set(ids.slice(0,8));candidates();}
function candidates(){
 const task=$('task').value;const list=runs.filter(r=>r.scenario_id+'@'+r.scenario_version===task).sort((a,b)=>Date.parse(b.started_at??'')-Date.parse(a.started_at??''));
 const providersHere=[...new Set(list.map(r=>r.provider_config_id??'unknown'))];
 const keep=$('pick-provider').value;
 $('pick-provider').replaceChildren(...providersHere.map(p=>option(providerName(p)+' ('+list.filter(r=>(r.provider_config_id??'unknown')===p).length+')',p)));
 if(providersHere.includes(keep))$('pick-provider').value=keep;
 const t=el('table','run-picker');const head=el('tr');
 for(const h of ['','Provider','Run','Verdict','Started','Duration'])head.append(el('th','',h));
 const thead=el('thead');thead.append(head);t.append(thead);const body=el('tbody');
 for(const run of list){
  const tr=el('tr',chosen.has(run.run_id)?'selected':'');
  const input=el('input');input.type='checkbox';input.value=run.run_id;input.checked=chosen.has(run.run_id);input.setAttribute('aria-label','Select run '+shortId(run.run_id));
  input.addEventListener('change',()=>{if(input.checked&&chosen.size>=8){input.checked=false;report(new Error('Select at most 8 runs.'));return;}input.checked?chosen.add(run.run_id):chosen.delete(run.run_id);tr.classList.toggle('selected',input.checked);$('selection').textContent=selectionText();});
  const cb=el('td');cb.append(input);
  const prov=el('td');prov.append(providerEl(run.provider_config_id));
  const id=el('td');id.append(idCell(run.run_id));
  const res=runResult(run);const verdict=el('td');verdict.append(el('span','verdict verdict-'+res.key,res.label));
  const when=el('td');when.append(timeEl(run.started_at));
  tr.append(cb,prov,id,verdict,when,el('td','',durationLabel(run.started_at,run.last_event_at)||'—'));
  tr.addEventListener('click',e=>{if(e.target===input||e.target.closest('a,button'))return;input.click();});
  body.append(tr);
 }
 t.append(body);
 $('candidates').replaceChildren(list.length?t:el('p','muted','No runs recorded for this task yet.'));
 $('selection').textContent=selectionText();
}
function selectionText(){return chosen.size+' selected'+(chosen.size<2?' · choose at least 2':'');}
function batchChoices(){
 const selected=scenarios[Number($('batch-scenario').value)];$('batch-providers').replaceChildren();
 for(const p of selected?.live?providers:[{provider_config_id:'mock-banking-v1',available:true}]){
  const label=el('label','check');const input=el('input');input.type='checkbox';input.value=p.provider_config_id;input.disabled=!p.available;input.checked=p.available;
  label.append(input,el('span','',providerName(p.provider_config_id)+(p.available?'':' · Unavailable: '+(p.reason??'Not configured'))));$('batch-providers').append(label);
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
  await loadRuns();mountReports($('stored-reports'),token);setConnected(true);$('controls').hidden=false;$('status').textContent='Connected · Choose at least two runs of one task.';
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
 if(left&&right){renderDeltas(left,right);renderConversation(left,right);}
 for(const [side,id] of [['left',x],['right',y]]){
  const entry=entries.find(e=>e.run.run_id===id);if(!entry)continue;
  if(pairWorkspaces[side]?.id!==id)pairWorkspaces[side]={id,view:createWorkspace($(side+'-trace'),null)};
  pairWorkspaces[side].view.update(entry.run,entry.events,entry.metrics,entry.holds,entry.assurance,scenarios.find(s=>s.id===entry.run.config.scenario_id)?.title);
  // Comparison is inspection-only. Cancellation belongs to the run workspace.
  $(side+'-trace').querySelectorAll('button').forEach(b=>{if(b.textContent==='Cancel evaluation')b.hidden=true;});
  $(side+'-link').href=runLink(id);$(side+'-link').textContent='Open '+(side==='left'?'X':'Y')+' ('+shortId(id)+') in Runs';
 }
}
function fmtDelta(r){if(r.delta==null)return '—';return (r.delta>0?'+':r.delta<0?'−':'')+Number(Math.abs(r.delta).toPrecision(5))+(r.unit&&r.unit!=='count'?' '+r.unit:'');}
function renderDeltas(left,right){
 const {rows,hidden}=metricTable(left,right,humanise);const root=$('deltas');
 const side=(e)=>providerName(e.run.config.provider_config_id)+' · '+shortId(e.run.run_id);
 root.replaceChildren(el('h3','','Metrics · Y − X'));
 if(!rows.length){root.append(el('p','muted','No metric was recorded for both runs.'));}
 else{
  const t=el('table','delta-table');const h=el('tr');
  for(const [text,title] of [['Metric',''],['X',side(left)],['Y',side(right)],['Δ (Y − X)','Observed difference, not statistical significance']]){const th=el('th',text==='Metric'?'':'num',text);if(title)th.title=title;h.append(th);}
  const th=el('thead');th.append(h);t.append(th);const tb=el('tbody');
  for(const r of rows){
   const tr=el('tr',r.latency?'latency':'');
   const name=el('td','',r.label);if(r.latency)name.append(' ',el('span','tag','latency'));name.title=r.name;
   tr.append(name,el('td','num',r.x??'—'),el('td','num',r.y??'—'),el('td','num delta delta-'+(r.direction??'none'),fmtDelta(r)));tb.append(tr);
  }
  t.append(tb);root.append(t);
 }
 if(hidden.length){const d=el('details','unavailable');d.append(el('summary','',hidden.length+' metric'+(hidden.length===1?'':'s')+' not measured for either run'));const ul=el('ul');for(const m of hidden)ul.append(el('li','',m.label+' · '+humanise(String(m.reason).toLowerCase())));d.append(ul);root.append(d);}
}
function turnCell(turn,revisions){
 const c=el('div','turn-cell');
 if(!turn){c.append(el('p','muted','No matching turn'));return c;}
 if(turn.customer)c.append(line(turn.customer));
 for(const l of turn.assistant)c.append(line(l,revisions));
 return c;
}
function line(l,revisions){
 const d=el('div','conv-line conv-'+l.speaker+(l.state==='superseded'?' earlier':''));
 d.append(el('span','cv-who',l.speaker==='customer'?'Caller':'Assistant'),el('span','cv-text',l.text));
 if(l.state==='superseded')d.append(el('span','chip chip-superseded','Earlier version'));
 else if(l.stateLabel&&!['played','released'].includes(l.state))d.append(el('span','chip chip-'+l.state,l.stateLabel));
 return d;
}
function renderConversation(left,right){
 const xl=transcript(left.events),yl=transcript(right.events),root=$('conversation');
 const head=el('div','conv-head');head.append(el('h3','','Conversation, turn by turn'));
 if(hasRevisions(xl)||hasRevisions(yl)){const lab=el('label','check');const cb=el('input');cb.type='checkbox';cb.checked=showRevisions;cb.addEventListener('change',()=>{showRevisions=cb.checked;renderConversation(left,right);});lab.append(cb,el('span','','Show revisions'));head.append(lab);}
 root.replaceChildren(head);
 const turns=alignTurns(xl,yl,showRevisions);
 if(!turns.length){root.append(el('p','muted','No transcript recorded yet.'));return;}
 const g=el('div','conv-grid');
 g.append(el('div','conv-col-head','Turn'),el('div','conv-col-head','X · '+providerName(left.run.config.provider_config_id)),el('div','conv-col-head','Y · '+providerName(right.run.config.provider_config_id)));
 for(const t of turns)g.append(el('div','conv-turn',String(t.turn)),turnCell(t.x,showRevisions),turnCell(t.y,showRevisions));
 root.append(g);
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
  const firstShow=$('results').hidden;$('results').hidden=false;
  const notes=[];
  if(match.differences.length)notes.push('Same task, different settings: '+match.differences.map(humanise).join(', ')+'. This is a configuration comparison, not an isolated provider effect.');
  if(match.missing.length)notes.push('Missing configuration evidence: '+match.missing.map(humanise).join(', ')+'.');
  if(match.adaptiveCaller)notes.push('Live AI callers generate different dialogues even with matching settings; use repeated runs.');
  $('comparability').textContent=notes.join(' ');$('comparability').hidden=!notes.length;
  const counts=new Map();entries.forEach(e=>{const k=e.run.config.provider_config_id;counts.set(k,(counts.get(k)??0)+1);});
  $('status').textContent='Selected samples: '+[...counts].map(([p,n])=>providerName(p)+' n='+n).join(' · ');
  const names=entries.map((e,i)=>(i+1)+': '+e.run.run_id);
  table($('summary'),'Outcomes & configuration',['Measure',...names],[
   ['Provider configuration',...entries.map(e=>providerEl(e.run.config.provider_config_id))],
   ['Agent prompt',...entries.map(e=>e.run.config.prompt_profile??'Original / not recorded')],
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
  for(const side of ['left','right'])$(side).replaceChildren(...entries.map((e,i)=>option(providerName(e.run.config.provider_config_id)+' · '+shortId(e.run.run_id)+' · '+(relativeTime(e.run.started_at??e.events[0]?.recorded_at)||e.run.state),e.run.run_id)));
  $('left').value=ids.includes(previous[0])?previous[0]:ids[0];$('right').value=ids.includes(previous[1])?previous[1]:ids[1];pair();
  const url=new URL(location.href);url.searchParams.set('runs',ids.join(','));history.replaceState(null,'',url);
  if(!refresh&&firstShow)revealResult($('results'));
  if(entries.some(e=>!TERMINAL.has(e.run.state)))timer=setTimeout(()=>compareIds(ids,true),5000);
 }catch(e){if(mine!==generation)return;report(e);$('status').textContent='Comparison could not be loaded. Previously loaded results, if present, are unchanged.';}
}
function savePlan(){try{sessionStorage.setItem('harness-comparison-batch',JSON.stringify(pending));}catch{}}
async function launchPending(){
 if(loading||!pending)return;loading=true;$('batch-launch').disabled=true;$('batch-retry').hidden=true;$('error').textContent='';
 try{
  for(const job of pending){
   if(job.run_ids)continue;
   $('batch-progress').textContent='Launching '+providerName(job.body.provider_config_id)+' · seed '+job.body.fixture_seed+'. Completed launches: '+pending.filter(j=>j.run_ids).length+' / '+pending.length;
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
$('pick-latest').addEventListener('click',()=>select(latestPerProvider(runs.filter(r=>r.scenario_id+'@'+r.scenario_version===$('task').value))));
$('pick-repeats').addEventListener('click',()=>select(repeatsOfProvider(runs.filter(r=>r.scenario_id+'@'+r.scenario_version===$('task').value),$('pick-provider').value)));
$('pick-clear').addEventListener('click',()=>select([]));
$('refresh').addEventListener('click',()=>loadRuns().catch(report));$('more').addEventListener('click',()=>loadRuns(true).catch(report));
$('compare').addEventListener('click',()=>compareIds([...chosen]));$('open-ids').addEventListener('click',()=>compareIds($('ids').value.split(',')));
$('left').addEventListener('change',pair);$('right').addEventListener('change',pair);
$('update').addEventListener('click',()=>compareIds(entries.map(e=>e.run.run_id),true));
$('batch-scenario').addEventListener('change',batchChoices);
$('batch').addEventListener('submit',e=>{e.preventDefault();if(loading)return;if(pending){report(new Error('Finish the pending batch with Retry unfinished launches before starting another.'));return;}try{pending=batchPlan(scenarios[Number($('batch-scenario').value)],[...$('batch-providers').querySelectorAll('input:checked')].map(e=>e.value),Number($('batch-seed').value),Number($('batch-count').value),$('batch-retain').checked,'compare-'+crypto.randomUUID());savePlan();launchPending();}catch(e){pending=null;report(e);}});
$('batch-retry').addEventListener('click',launchPending);
$('export').addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify({export_type:'descriptive_run_comparison_snapshot',captured_at:new Date().toISOString(),statement:'Recorded run evidence; no ranking or statistical inference. This snapshot is not a signed evidence bundle.',comparability:comparisonCheck(entries.map(e=>e.run)),entries},null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='run-comparison.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});
try{pending=JSON.parse(sessionStorage.getItem('harness-comparison-batch')??'null');if(pending){$('batch-retry').hidden=false;$('batch-progress').textContent='An unfinished comparison batch is saved in this tab. Connect, then retry its unfinished launches.';}const saved=sessionStorage.getItem('harness-operator-token');if(saved){$('token').value=saved;connect();}}catch{}
