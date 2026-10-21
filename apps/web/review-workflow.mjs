import {api,el,fetchEvents} from './runs.mjs?v=ux-final-20261025';
import {field,action,formAction,post,words,holdBody} from './workflow.mjs?v=ux-final-20261025';
import {friendlyError} from './onboarding.mjs?v=ux-final-20261025';
import {revealResult} from './nav.mjs?v=ux-final-20261025';
import {providerEl,providerName} from './providers.mjs?v=ux-final-20261025';
import {scrollRegion,idCell,timeEl,rawToggle,humanise,shortId,relativeTime,copyButton} from './format.mjs?v=ux-final-20261025';
import {SEVERITIES,enrichQueue,filterAndSort,severityCounts,evidenceModel,assertCitedEventsBelong,createDraftStore,holdErrorMessage,rememberedReviewer,rememberReviewer,holdTable,countdown} from './review-model.mjs?v=ux-final-20261025';

export function reviewDecision({reviewer,disposition,rationale,evidence}){
 if(!['confirm_fail','confirm_pass','confirm_untestable','overturn_fail'].includes(disposition))throw new Error('Choose a disposition explicitly.');
 if(!reviewer.trim()||!rationale.trim())throw new Error('Add your reviewer ID and explain the decision.');
 const ids=evidence.split(/[\s,]+/).filter(Boolean);
 return {reviewer_id:reviewer.trim(),disposition,rationale:rationale.trim(),evidence_event_ids:ids};
}

const loadRuns=async token=>{try{return (await api(token,'/api/v1/runs?limit=200')).items;}catch{return [];}};
const chip=(sev)=>el('span','sev sev-'+sev,sev);
const runLabel=r=>(r.scenario_id??'Unknown scenario')+' · '+providerName(r.provider_config_id)+' · '+(relativeTime(r.started_at)||'unknown time')+' · '+shortId(r.run_id);

function evidencePanel(model,runId){
 const wrap=el('div','evidence-compare');
 const exp=el('section','ev-col');exp.append(el('h4','','Expected'));
 exp.append(el('p','',model.expected.behaviour??'No expected behaviour recorded for this check.'));
 if(model.expected.prohibited)exp.append(el('p','muted','Not allowed: '+model.expected.prohibited));
 if(model.expected.passAnchor)exp.append(el('p','muted','Pass looks like: '+model.expected.passAnchor));
 const obs=el('section','ev-col');obs.append(el('h4','','Observed'));
 const v=model.observed.verdict;
 obs.append(el('p','','Check result: '+(v?words(v):'not recorded')+(model.observed.source?' ('+words(model.observed.source)+')':'')));
 if(model.expected.failAnchor)obs.append(el('p','muted','Fail looks like: '+model.expected.failAnchor));
 if(model.observed.reasonCodes.length)obs.append(el('p','muted','Reason: '+model.observed.reasonCodes.map(words).join(', ')));
 if(model.observed.cited.length){const ul=el('ul','ev-cited');for(const c of model.observed.cited){const li=el('li','');li.append(el('strong','','#'+c.sequence+' '),document.createTextNode(c.summary),document.createTextNode(' '),copyButton(c.eventId,'Copy event ID '+c.eventId));ul.append(li);}obs.append(ul);}
 else obs.append(el('p','muted','The check cited no events.'));
 if(model.observed.missing.length)obs.append(el('p','muted','Cited events not found in the ledger: '+model.observed.missing.join(', ')));
 const tr=el('section','ev-col');tr.append(el('h4','','Transcript around the evidence'));
 if(model.transcript.length){for(const l of model.transcript){const p=el('p','ev-line'+(l.cited?' ev-cited-line':''));p.append(el('span','speaker',l.speaker==='customer'?'Caller':'Assistant'),document.createTextNode(' '+l.text));tr.append(p);}}
 else tr.append(el('p','muted','No transcript lines recorded for this run.'));
 const link=el('a','','Open full run transcript and audio status');link.href='./runs.html?run='+encodeURIComponent(runId);const lp=el('p','muted');lp.append(link);tr.append(lp);
 wrap.append(exp,obs,tr);return wrap;
}

export async function renderReviewQueue(root,token,{onAddHold}={}){
 root.replaceChildren(el('h2','','Findings awaiting review'));
 const filter=field('Review state',{value:'open',options:[['open','Open'],['in_review','Claimed / in review'],['decided','Decided']]});
 const sev=field('Severity',{value:'all',options:[['all','All severities'],...SEVERITIES.map(s=>[s,s.charAt(0).toUpperCase()+s.slice(1)])]});
 const bar=el('div','filter-bar');bar.append(filter.wrapper,sev.wrapper);
 const list=el('div','review-queue');const detail=el('section','panel review-detail');detail.append(el('p','muted','Select a finding to inspect its evidence and record a review.'));const columns=el('div','review-columns');columns.append(list,detail);const status=el('p','muted');status.setAttribute('role','status');const more=action('Load more findings',()=>load(true));
 root.append(bar,status,columns,more);const drafts=createDraftStore();list.setAttribute('role','group');list.setAttribute('aria-label','Findings queue');detail.setAttribute('aria-label','Selected finding');detail.setAttribute('role','region');let cursor=null;let loaded=[];let runs=[];let selected=null;
 function renderList(){
  list.replaceChildren();const rows=filterAndSort(loaded,sev.input.value);
  for(const r of rows){
   const b=action('',()=>open(r.reviewId,{reveal:true}),'queue-item'+(r.reviewId===selected?' selected':''));b.setAttribute('aria-pressed',String(r.reviewId===selected));
   const top=el('span','qi-top');top.append(el('strong','',humanise(r.criterionId)),chip(r.severity));
   const meta=el('span','qi-meta');meta.append(el('span','',r.scenario??'Scenario unknown'),el('span','',' · '));meta.append(r.provider?providerEl(r.provider):el('span','','Provider unknown'));
   const age=el('span','qi-meta');age.append(document.createTextNode('Opened '),r.createdIso?timeEl(r.createdIso):el('span','','unknown'),document.createTextNode(' · '+(r.assignee?'Assigned to '+r.assignee:'Unassigned')+' · '+words(r.reason)));
   b.append(top,meta,age);b.title=r.runId;list.append(b);
  }
  const counts=severityCounts(loaded);for(const o of sev.input.options)o.textContent=(o.value==='all'?'All severities':o.value.charAt(0).toUpperCase()+o.value.slice(1))+' ('+counts[o.value]+')';
  status.textContent=rows.length?rows.length+' of '+loaded.length+' findings shown.':loaded.length?'No '+sev.input.value+' findings loaded; change the severity filter.':'No findings in this state.';
 }
 async function load(append=false){
  more.disabled=true;
  try{runs=await loadRuns(token);const result=await api(token,'/api/v1/reviews?state='+filter.input.value+(append&&cursor?'&cursor='+encodeURIComponent(cursor):''));
   const rows=enrichQueue(result.items,runs);loaded=append?[...loaded,...rows]:rows;cursor=result.next_cursor;more.hidden=!cursor;renderList();}catch(e){status.textContent=e.message;}finally{more.disabled=false;}
 }
 async function open(id,{reveal=false,notice=''}={}){
  status.textContent='Loading finding…';selected=id;renderList();
  try{const r=await api(token,'/api/v1/reviews/'+encodeURIComponent(id));const events=await fetchEvents(token,r.run_id);const url=new URL(location.href);url.searchParams.set('review',id);history.replaceState(null,'',url);
   const run=runs.find(x=>x.run_id===r.run_id);
   const head=el('div','detail-head');head.append(el('h3','',r.criterion?.title??r.criterion_id),chip(r.severity??'medium'));
   const ctx=el('p','muted');ctx.append(document.createTextNode((run?.scenario_id??'Scenario unknown')+' · '),run?providerEl(run.provider_config_id):document.createTextNode('Provider unknown'),document.createTextNode(' · State: '+words(r.state)+(r.claimed_by?' · Claimed by '+r.claimed_by:' · Unassigned')+' · '),idCell(r.run_id));
   detail.replaceChildren(head,el('p','',words(r.reason)),ctx);
   const model=evidenceModel(r,events);detail.append(evidencePanel(model,r.run_id));
   detail.append(rawToggle({review:r,cited_events:events.filter(e=>model.citedIds.includes(e.event_id))},'Raw finding and cited events (JSON)'));
   if(reveal)revealResult(detail);
   if(r.state==='decided'){drafts.clear(id);detail.append(el('p','', 'Decision: '+words(r.decision?.disposition)+' · '+words(r.decision?.effect)+(r.decision?.reviewer_id?' · by '+r.decision.reviewer_id:'')));status.textContent='Decision is recorded as human evidence.';return;}
   const form=el('form','workflow-form');const draft=drafts.get(id);const reviewer=field('Reviewer ID',{value:draft?.reviewer??(r.claimed_by||rememberedReviewer())});reviewer.input.autocomplete='off';
   const rationale=field('Decision rationale',{type:'textarea'});rationale.input.maxLength=4000;
   const disposition=field('Disposition',{value:'',options:[['','Choose a disposition…'],['confirm_fail','Confirm failure'],['confirm_pass','Confirm pass'],['confirm_untestable','Confirm untestable'],['overturn_fail','Overturn failure (requires supporting evidence)']]});
   const cited=field('Supporting event IDs (comma separated)',{required:false});cited.input.value=draft?.cited??model.citedIds.join(', ');
   const correct=field('Correction evidence / check version',{required:false});correct.input.placeholder='Recorded check version, e.g. 1';const correction=el('input');correction.type='checkbox';const correctionLabel=el('label','check');correctionLabel.append(correction,document.createTextNode('Include a deterministic check correction'));
   if(draft){disposition.input.value=draft.disposition;rationale.input.value=draft.rationale;correction.checked=draft.correction;correct.input.value=draft.correct;}
   const isOpen=r.state==='open';
   const saveDraft=()=>drafts.save(id,{reviewer:reviewer.input.value,disposition:disposition.input.value,rationale:rationale.input.value,cited:cited.input.value,correction:correction.checked,correct:correct.input.value});
   const submit=el('button','primary',isOpen?'Claim and record decision':'Record decision');submit.type='submit';
   const formStatus=el('p','muted');formStatus.setAttribute('role','status');
   form.append(reviewer.wrapper,disposition.wrapper,rationale.wrapper,cited.wrapper,el('p','muted',(isOpen?'Submitting claims this review for the reviewer ID above (recorded in the ledger), then records the decision. ':'')+'Cited event IDs are pre-filled from the check; confirm they support your decision. Missing evidence cannot be overridden by assertion. Changing a deterministic failure requires a check correction with the recorded version and supporting evidence. The server determines whether the decision applies.'),correctionLabel,correct.wrapper);
   correct.wrapper.hidden=!correction.checked;correct.input.required=correction.checked;correction.addEventListener('change',()=>{correct.wrapper.hidden=!correction.checked;correct.input.required=correction.checked;});
   const picker=el('details','evidence-picker');picker.append(el('summary','','Select other supporting events from this run'));
   picker.append(el('p','muted','Most recent '+Math.min(events.length,100)+' events. Selected events are added to the IDs above.'));
   for(const event of events.slice(-100)){if(!event.event_id)continue;const label=el('label','check');const input=el('input');input.type='checkbox';input.value=event.event_id;input.checked=(draft?.cited!=null?draft.cited.split(/[\s,]+/).filter(Boolean):model.citedIds).includes(event.event_id);label.append(input,el('span','','#'+event.sequence+' · '+words(event.type)+' · '+event.event_id));
    input.addEventListener('change',()=>{const ids=new Set(cited.input.value.split(/[\s,]+/).filter(Boolean));input.checked?ids.add(event.event_id):ids.delete(event.event_id);cited.input.value=[...ids].join(', ');});picker.append(label);}
   form.append(picker);form.addEventListener('input',saveDraft);form.addEventListener('change',saveDraft);
   const claimOnly=isOpen?action('Claim only',async()=>{try{const who=reviewer.input.value.trim();if(!who)throw new Error('Add your reviewer ID.');rememberReviewer(who);await post(token,'/api/v1/reviews/'+encodeURIComponent(id)+'/claim',{reviewer_id:who});await load();await open(id);status.textContent='Claimed by '+who+'.';}catch(e){formStatus.textContent=e.message;}}):null;
   form.append(submit);if(claimOnly)form.append(claimOnly);form.append(formStatus);detail.append(form);
   const hold=action('Preserve evidence for this finding',()=>onAddHold?.({runId:r.run_id,findingId:r.review_id,owner:reviewer.input.value.trim()}),'link-button');detail.append(hold);
   formAction(form,submit,async()=>{
    const body=reviewDecision({reviewer:reviewer.input.value,disposition:disposition.input.value,rationale:rationale.input.value,evidence:cited.input.value});
    assertCitedEventsBelong(body.evidence_event_ids,events);
    if(correction.checked){if(!body.evidence_event_ids.length)throw new Error('A check correction requires supporting event IDs.');body.check_correction={correction_id:crypto.randomUUID(),corrected_check_id:r.criterion_id,corrected_check_version:correct.input.value.trim(),rationale:body.rationale,evidence_event_ids:body.evidence_event_ids};}
    rememberReviewer(body.reviewer_id);
    if(isOpen)await post(token,'/api/v1/reviews/'+encodeURIComponent(id)+'/claim',{reviewer_id:body.reviewer_id});
    let result;
    try{result=await post(token,'/api/v1/reviews/'+encodeURIComponent(id)+'/decision',body);}
    catch(e){if(isOpen){saveDraft();const msg='Claimed by '+body.reviewer_id+', but the decision was not recorded: '+friendlyError(e)+' Your entries were kept; submit again to retry.';await load();await open(id,{notice:msg});return;}throw e;}
    drafts.clear(id);await load();await open(id);status.textContent='Decision recorded: '+words(result.effect)+' · Effective verdict: '+words(result.effective_verdict);
   },formStatus);
   status.textContent=notice||'Review the evidence before recording a disposition.';if(notice)formStatus.textContent=notice;
  }catch(e){status.textContent=e.message;}
 }
 filter.input.addEventListener('change',()=>load());sev.input.addEventListener('change',renderList);await load();
 const requested=new URLSearchParams(location.search).get('review');if(requested)await open(requested);
}

export async function renderHoldManagement(root,token){
 root.replaceChildren(el('h2','','Preservation holds'));
 const summary=el('p','muted');summary.setAttribute('role','status');const tableBox=scrollRegion(el('div','hold-table-box'),'Preservation holds table');const addBox=el('details','panel add-hold');
 root.append(summary,tableBox,addBox);
 let runs=await loadRuns(token);let findings=new Map();let ticker=null;
 try{for(const r of (await api(token,'/api/v1/reviews?limit=1000')).items)findings.set(r.review_id,{criterionId:r.criterion_id,runId:r.run_id,severity:r.severity});}catch{/* findings are optional context */}
 async function refresh(){
  try{
   const payload=await api(token,'/api/v1/holds-register?state=active');const fetched=Date.now();const rows=holdTable(payload,findings,runs);
   summary.textContent=rows.length+' active hold'+(rows.length===1?'':'s')+'; '+rows.filter(r=>r.countdown.tone!=='ok').length+' expiring within 7 days or expired. Holds protect retained artifacts until expiry; they cannot restore purged evidence.';
   tableBox.replaceChildren(rows.length?holdsTable(rows,payload.server_time,fetched):el('p','muted','No active holds. Add one below, or from a finding.'));
  }catch(e){summary.textContent=e.message;}
 }
 function holdsTable(rows,serverTime,fetched){
  clearInterval(ticker);const t=el('table','holds');const head=el('tr');for(const h of ['Run','Scope','Owner','Expires','Actions']){const th=el('th','',h);th.scope='col';head.append(th);}t.append(head);const stamps=[];
  for(const h of rows){
   const tr=el('tr',h.runId===new URLSearchParams(location.search).get('run')?'hold-focus':'');const run=el('td');run.append(el('div','',h.scenario??'Scenario unknown'),Object.assign(el('div','muted'),{}));run.lastChild.append(h.provider?providerEl(h.provider):document.createTextNode('Provider unknown'),document.createTextNode(' · '),idCell(h.runId));
   const scope=el('td');scope.append(el('strong','',h.scope),el('div','muted',h.scopeDetail));
   const exp=el('td');const c=el('strong','flag-'+h.countdown.tone,h.countdown.label);exp.append(c,el('div','muted',new Date(h.expiresAt*1000).toLocaleString()));stamps.push({c,h});
   const act=el('td');const manage=action('Renew or release',()=>{detailRow.hidden=!detailRow.hidden;manage.setAttribute('aria-expanded',String(!detailRow.hidden));});manage.setAttribute('aria-expanded','false');manage.setAttribute('aria-label','Renew or release hold for '+(h.scenario??'run')+' ('+h.scope+')');act.append(manage);
   tr.append(run,scope,el('td','',h.owner),exp,act);t.append(tr);
   const detailRow=el('tr');detailRow.hidden=true;const cell=el('td');cell.colSpan=5;cell.append(holdActions(h));detailRow.append(cell);t.append(detailRow);
  }
  ticker=setInterval(()=>{if(!t.isConnected){clearInterval(ticker);return;}const now=serverTime+(Date.now()-fetched)/1000;for(const s of stamps){const lbl=countdown(s.h.expiresAt,now);s.c.textContent=lbl.label;s.c.className='flag-'+lbl.tone;}},30000);
  return t;
 }
 function holdActions(h){
  const f=el('form','workflow-form');const mode=field('Action',{value:'renew',options:[['renew','Renew hold'],['release','Release hold']]});const reason=field('Reason');reason.input.maxLength=500;const expiry=field('New expiry (local time)',{type:'datetime-local'});const save=el('button','','Save hold action');save.type='submit';const st=el('p','muted');st.setAttribute('role','status');
  f.append(mode.wrapper,reason.wrapper,expiry.wrapper,save,st);mode.input.addEventListener('change',()=>{expiry.wrapper.hidden=mode.input.value==='release';expiry.input.required=mode.input.value==='renew';});
  formAction(f,save,async()=>{const body={reason:reason.input.value};if(mode.input.value==='renew'){body.expires_at=Date.parse(expiry.input.value)/1000;if(!Number.isFinite(body.expires_at)||body.expires_at<=Date.now()/1000)throw new Error('Choose a future expiry.');}
   await post(token,'/api/v1/runs/'+encodeURIComponent(h.runId)+'/preservation-holds/'+encodeURIComponent(h.holdId)+'/'+mode.input.value,body);await refresh();summary.textContent='Hold '+(mode.input.value==='renew'?'renewed.':'released; retention and purge grace rules still apply.');},st);
  return f;
 }
 // Add hold: pick a run from a list, optionally scoped to one of its findings.
 addBox.append(el('summary','','Add a preservation hold'));
 const form=el('form','workflow-form');const runSel=field('Run',{value:'',options:[['','Choose a run…'],...runs.map(r=>[r.run_id,runLabel(r)])]});
 const scopeSel=field('Scope',{value:'',required:false,options:[['','Whole run']]});const owner=field('Hold owner',{value:rememberedReviewer()});const reason=field('Reason');reason.input.maxLength=500;
 const expiry=field('Expiry (local time; blank uses 90 days)',{type:'datetime-local',required:false});const save=el('button','primary','Preserve evidence');save.type='submit';const st=el('p','muted');st.setAttribute('role','status');
 form.append(runSel.wrapper,scopeSel.wrapper,owner.wrapper,reason.wrapper,expiry.wrapper,save,st);addBox.append(form);let key=crypto.randomUUID();
 function rebuildScope(selected=''){scopeSel.input.replaceChildren(Object.assign(el('option','','Whole run (all evidence)'),{value:''}));for(const [id,f] of findings)if(f.runId===runSel.input.value)scopeSel.input.append(Object.assign(el('option','','Finding: '+humanise(f.criterionId)+' · '+f.severity+' · '+shortId(id)),{value:id}));scopeSel.input.value=selected;}
 runSel.input.addEventListener('change',()=>rebuildScope());
 formAction(form,save,async()=>{let result;try{result=await post(token,'/api/v1/runs/'+encodeURIComponent(runSel.input.value)+'/preservation-holds',holdBody({owner:owner.input.value,reason:reason.input.value,expiry:expiry.input.value,findings:scopeSel.input.value,key}));}catch(e){const m=holdErrorMessage(e);if(m){key=crypto.randomUUID();st.textContent=m;return;}throw e;}rememberReviewer(owner.input.value);st.textContent='Hold created: '+result.hold_id;key=crypto.randomUUID();reason.input.value='';await refresh();},st);
 function prefill({runId,findingId='',owner:who=''}={}){
  if(runId&&![...runSel.input.options].some(o=>o.value===runId))runSel.input.append(Object.assign(el('option','',runId),{value:runId}));
  runSel.input.value=runId??'';rebuildScope(findingId);if(who)owner.input.value=who;addBox.open=true;addBox.scrollIntoView({block:'center'});reason.input.focus({preventScroll:true});
 }
 await refresh();
 const requested=new URLSearchParams(location.search).get('run');if(requested){const mine=tableBox.querySelector('tr.hold-focus');if(mine){mine.setAttribute('aria-current','true');mine.scrollIntoView?.({block:'center'});}else prefill({runId:requested});} // a run with an active hold is highlighted; otherwise the add-hold form opens for it
 return {prefill};
}
