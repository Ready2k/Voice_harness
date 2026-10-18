import {api,el,fetchEvents} from './runs.mjs?v=ux-audit-20261018';
import {field,action,formAction,post,words,holdForm} from './workflow.mjs?v=ux-audit-20261018';

export function reviewDecision({reviewer,disposition,rationale,evidence}){
 if(!reviewer.trim()||!rationale.trim())throw new Error('Add your reviewer ID and explain the decision.');
 const ids=evidence.split(/[\s,]+/).filter(Boolean);
 return {reviewer_id:reviewer.trim(),disposition,rationale:rationale.trim(),evidence_event_ids:ids};
}
export async function renderReviewQueue(root,token){
 root.replaceChildren(el('h2','','Findings awaiting review'));
 const filter=field('Review state',{value:'open',options:[['open','Open'],['in_review','Claimed / in review'],['decided','Decided']]});
 const list=el('div','review-queue');const detail=el('section','panel review-detail');detail.append(el('p','muted','Select a finding to inspect its evidence and record a review.'));const columns=el('div','review-columns');columns.append(list,detail);const status=el('p','muted');status.setAttribute('role','status');const more=action('Load more findings',()=>load(true));
 root.append(filter.wrapper,status,columns,more);let cursor=null;
 async function load(append=false){
  more.disabled=true;
  try{const result=await api(token,'/api/v1/reviews?state='+filter.input.value+(append&&cursor?'&cursor='+encodeURIComponent(cursor):''));if(!append)list.replaceChildren();for(const r of result.items){const b=action((r.criterion_id??'Finding')+' · '+words(r.reason)+' · '+r.run_id,()=>open(r.review_id),'queue-item');list.append(b);}cursor=result.next_cursor;more.hidden=!cursor;status.textContent=list.childElementCount?list.childElementCount+' findings loaded.':'No findings in this state.';}catch(e){status.textContent=e.message;}finally{more.disabled=false;}
 }
 async function open(id){
  status.textContent='Loading finding…';
  try{const r=await api(token,'/api/v1/reviews/'+encodeURIComponent(id));const url=new URL(location.href);url.searchParams.set('review',id);history.replaceState(null,'',url);detail.replaceChildren(el('h3','',r.criterion?.title??r.criterion_id),el('p','',words(r.reason)),el('p','muted','State: '+words(r.state)+' · '+r.review_id));const link=el('a','','Inspect run evidence');link.href='./runs.html?run='+encodeURIComponent(r.run_id);detail.append(link);
   const evidence=el('details');evidence.append(el('summary','','Finding details & cited evidence'),el('pre','event-json',JSON.stringify(r,null,2)));detail.append(evidence);
   if(r.state==='decided'){detail.append(el('p','', 'Decision: '+words(r.decision?.disposition)+' · '+words(r.decision?.effect)));status.textContent='Decision is recorded as human evidence.';return;}
   const form=el('form','workflow-form');const reviewer=field('Reviewer ID',{value:r.claimed_by??''});const rationale=field('Decision rationale',{type:'textarea'});rationale.input.maxLength=4000;
   const disposition=field('Disposition',{value:'confirm_fail',options:[['confirm_fail','Confirm failure'],['confirm_pass','Confirm pass'],['confirm_untestable','Confirm untestable'],['overturn_fail','Overturn failure (requires supporting evidence)']]});const cited=field('Supporting event IDs (comma separated)',{required:false});
   const correct=field('Correction evidence / check version',{required:false});correct.input.placeholder='Recorded check version, e.g. 1';const correction=el('input');correction.type='checkbox';const correctionLabel=el('label','check');correctionLabel.append(correction,document.createTextNode('Include a deterministic check correction'));
   const submit=el('button','primary',r.state==='open'?'Claim this review':'Record decision');submit.type='submit';form.append(reviewer.wrapper);
   if(r.state==='in_review')form.append(disposition.wrapper,rationale.wrapper,cited.wrapper,el('p','muted','Missing evidence cannot be overridden by assertion. Changing a deterministic failure requires a check correction with the recorded version and supporting evidence. The server determines whether the decision applies.'));
   if(r.state==='in_review')form.append(correctionLabel,correct.wrapper);correct.wrapper.hidden=true;correction.addEventListener('change',()=>{correct.wrapper.hidden=!correction.checked;correct.input.required=correction.checked;});
   const formStatus=el("p","muted");formStatus.setAttribute("role","status");form.append(submit,formStatus);detail.append(form);
   const preserve=el('details');preserve.append(el('summary','','Preserve evidence while reviewing'));holdForm(preserve,token,r.run_id);detail.append(preserve);
   formAction(form,submit,async()=>{
    if(r.state==='open'){await post(token,'/api/v1/reviews/'+encodeURIComponent(id)+'/claim',{reviewer_id:reviewer.input.value.trim()});await load();await open(id);}
    else{const body=reviewDecision({reviewer:reviewer.input.value,disposition:disposition.input.value,rationale:rationale.input.value,evidence:cited.input.value});
     const events=await fetchEvents(token,r.run_id);const valid=new Set(events.map(e=>e.event_id));if(body.evidence_event_ids.some(id=>!valid.has(id)))throw new Error('A cited event does not belong to this run. Inspect the evidence and use its event IDs.');
     if(correction.checked){if(!body.evidence_event_ids.length)throw new Error('A check correction requires supporting event IDs.');body.check_correction={correction_id:crypto.randomUUID(),corrected_check_id:r.criterion_id,corrected_check_version:correct.input.value.trim(),rationale:body.rationale,evidence_event_ids:body.evidence_event_ids};}
     const result=await post(token,'/api/v1/reviews/'+encodeURIComponent(id)+'/decision',body);await load();await open(id);status.textContent='Decision recorded: '+words(result.effect)+' · Effective verdict: '+words(result.effective_verdict);}
   },formStatus);
   status.textContent='Review the cited evidence before recording a disposition.';
  }catch(e){status.textContent=e.message;}
 }
 filter.input.addEventListener('change',()=>load());await load();
 const requested=new URLSearchParams(location.search).get('review');if(requested)await open(requested);
}
export async function renderHoldManagement(root,token){
 root.replaceChildren(el('h2','','Manage preservation holds'));
 const run=field('Run ID');const loadButton=action('Load run holds',load);const status=el('p','muted');status.setAttribute('role','status');const list=el('div');const create=el('div');root.append(run.wrapper,loadButton,status,list,create);
 async function load(){
  if(!run.input.value.trim()){status.textContent='Enter the run ID to manage.';return;}loadButton.disabled=true;
  try{const base='/api/v1/runs/'+encodeURIComponent(run.input.value.trim())+'/preservation-holds';const result=await api(token,base);list.replaceChildren();create.replaceChildren(el('h3','','Create a preservation hold'));holdForm(create,token,run.input.value.trim(),load);
   for(const h of result.items){const box=el('section','panel');box.append(el('h3','',h.hold_id),el('p','muted','Owner: '+h.owner_id+' · Expiry: '+new Date(h.expires_at*1000).toLocaleString()+(h.released_at?' · Released':'')));
    if(!h.released_at){const f=el('form','workflow-form');const mode=field('Action',{value:'renew',options:[['renew','Renew hold'],['release','Release hold']]});const reason=field('Reason');reason.input.maxLength=500;const expiry=field('New expiry (local time)',{type:'datetime-local'});const save=el('button','','Save hold action');save.type='submit';f.append(mode.wrapper,reason.wrapper,expiry.wrapper,save);mode.input.addEventListener('change',()=>{expiry.wrapper.hidden=mode.input.value==='release';expiry.input.required=mode.input.value==='renew';});formAction(f,save,async()=>{const body={reason:reason.input.value};if(mode.input.value==='renew'){body.expires_at=Date.parse(expiry.input.value)/1000;if(!Number.isFinite(body.expires_at)||body.expires_at<=Date.now()/1000)throw new Error('Choose a future expiry.');}await post(token,base+'/'+encodeURIComponent(h.hold_id)+'/'+mode.input.value,body);await load();status.textContent='Hold '+(mode.input.value==='renew'?'renewed.':'released; retention and purge grace rules still apply.');},status);box.append(f);}
    list.append(box);
   }status.textContent=result.items.length+' holds loaded.';
  }catch(e){status.textContent=e.message;}finally{loadButton.disabled=false;}
 }
 const requested=new URLSearchParams(location.search).get('run');if(requested){run.input.value=requested;await load();}
}
