import {api,el,TERMINAL} from './runs.mjs?v=ux-audit-20261018';
import {friendlyError} from './onboarding.mjs?v=ux-audit-20261018';

export const words = value => {const text=String(value??'Unknown').replaceAll('_',' ');return /^[A-Z0-9 ]+$/.test(text)?text.charAt(0)+text.slice(1).toLowerCase():text;};
export function field(label,{value='',type='text',required=true,options=null}={}){
 const wrapper=el('label');wrapper.append(document.createTextNode(label));
 const input=el(options?'select':type==='textarea'?'textarea':'input');if(!options&&type!=='textarea')input.type=type;if(type==='textarea')input.rows=4;
 if(options)for(const [id,name] of options)input.append(Object.assign(el('option','',name),{value:id}));
 input.value=value;input.required=required;wrapper.append(input);return {wrapper,input};
}
export function action(text,fn,cls='') {const b=el('button',cls,text);b.type='button';b.addEventListener('click',fn);return b;}
export function formAction(form,button,fn,status){
 form.addEventListener('submit',async e=>{e.preventDefault();if(button.disabled)return;button.disabled=true;status.textContent='Saving…';try{await fn();}catch(e){status.textContent=friendlyError(e);}finally{button.disabled=false;}});
}
export const post=(token,path,body)=>api(token,path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export function holdBody({owner,reason,expiry,findings='',key}){
 if(!owner.trim()||!reason.trim())throw new Error('Add an owner and a reason.');
 const expires_at=expiry?Date.parse(expiry)/1000:null;
 if(expires_at!=null&&(!Number.isFinite(expires_at)||expires_at<=Date.now()/1000))throw new Error('Choose a future expiry.');
 const ids=findings.split(/[\s,]+/).filter(Boolean);
 return {owner_id:owner.trim(),reason:reason.trim(),expires_at,finding_ids:ids.length?ids:null,run_wide:!ids.length,idempotency_key:key};
}
export function holdForm(root,token,runId,onSaved){
 const form=el('form','workflow-form');const owner=field('Hold owner');const reason=field('Reason');reason.input.maxLength=500;
 const expiry=field('Expiry (local time; blank uses 90 days)',{type:'datetime-local',required:false});
 const findings=field('Finding IDs (blank preserves the whole run)',{required:false});
 const save=el('button','primary','Preserve evidence');save.type='submit';const status=el('p','muted');status.setAttribute('role','status');
 form.append(owner.wrapper,reason.wrapper,expiry.wrapper,findings.wrapper,save,status);
 let key=crypto.randomUUID();
 formAction(form,save,async()=>{const result=await post(token,'/api/v1/runs/'+encodeURIComponent(runId)+'/preservation-holds',holdBody({owner:owner.input.value,reason:reason.input.value,expiry:expiry.input.value,findings:findings.input.value,key}));status.textContent='Hold created: '+result.hold_id;key=crypto.randomUUID();await onSaved?.();},status);
 root.append(form);
}
export function cloneRunConfig(config){
 const keys=['scenario_id','scenario_version','provider_config_id','fixture_seed','policy_profile','prompt_profile','input_mode','retain_audio','client_mode'];
 return {...Object.fromEntries(keys.filter(k=>config[k]!=null).map(k=>[k,config[k]])),prompt_profile:config.prompt_profile??'legacy-v1',repetitions:1};
}
export function testedIdentity(run,events){
 const binding=events.find(e=>e.type==='run.live_binding')?.content?.provider;
 if(binding?.requested_model_id&&binding?.family)return {provider:binding.family,model_id:binding.requested_model_id,family:binding.adapter??binding.family};
 if(run.config.provider_config_id==='mock-banking-v1')return {provider:'mock',model_id:'mock-banking-v1',family:'mock'};
 return null;
}
export async function downloadEvidence(token,runId,view='standard',format='json'){
 const response=await fetch('/api/v1/runs/'+encodeURIComponent(runId)+'/evidence/download?view='+view+'&format='+format,{headers:{Authorization:'Bearer '+token}});
 if(!response.ok)throw new Error('Export failed (HTTP '+response.status+').');
 const url=URL.createObjectURL(await response.blob());const a=el('a');a.href=url;a.download=runId+'-'+view+'.'+format;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function runActions(root,{token,runId,getData,onOpen,onRefresh}){
 const container=el('details','panel workflow-drawer');container.append(el('summary','','Run actions · rerun, assess, preserve & export'));
 const content=el('div','workflow-content');const toolbar=el('div','actions');const status=el('p','muted');status.setAttribute('role','status');
 // Reuse the launch key if a response is lost.
 let rerunKey=crypto.randomUUID();
 const launchAgain=action('Run again with the same settings',async()=>{
  const data=getData();if(!data)return;launchAgain.disabled=true;status.textContent='Launching a new run…';
  try{const result=await api(token,'/api/v1/runs',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':rerunKey},body:JSON.stringify(cloneRunConfig(data.run.config))});rerunKey=crypto.randomUUID();await onOpen(result.run_ids[0]);}catch(e){status.textContent=friendlyError(e);}finally{launchAgain.disabled=false;}
 });
 const compare=el('a','','Compare this run');compare.href='./compare.html?candidate='+encodeURIComponent(runId);
 toolbar.append(launchAgain,compare);content.append(toolbar,el('p','muted','Rerun creates a new evaluation with the stored seed and settings. It does not replay retained audio or guarantee identical provider output.'));
 const exportForm=el('form','workflow-form');const format=field('Export format',{value:'json',options:[['json','JSON evidence bundle'],['ndjson','NDJSON events and manifest']]});const view=field('Evidence view',{value:'standard',options:[['standard','Standard evidence'],['reviewer','Restricted reviewer evidence']]});
 const exportButton=el('button','','Download evidence');exportButton.type='submit';exportForm.append(format.wrapper,view.wrapper,exportButton);formAction(exportForm,exportButton,async()=>{await downloadEvidence(token,runId,view.input.value,format.input.value);status.textContent='Evidence download requested.';},status);content.append(exportForm);
 const assess=el('details','sub-workflow');assess.append(el('summary','','Run an assessment'));
 const assessForm=el('form','workflow-form');const judges=field('Assessment mode',{value:'deterministic',options:[['deterministic','Deterministic checks only'],['openai','Checks + OpenAI judge'],['bedrock','Checks + Bedrock judge'],['both','Checks + both independent judges']]});
 const assessmentButton=el('button','','Assess this run');assessmentButton.type='submit';assessForm.append(judges.wrapper,assessmentButton,el('p','muted','A new assessment preserves previous findings. Live judges require server opt-in and may incur charges. Missing judge coverage is not a pass.'));
 formAction(assessForm,assessmentButton,async()=>{const data=getData();if(!data||!TERMINAL.has(data.run.state))throw new Error('Wait until the run has finished.');const tested=testedIdentity(data.run,data.events);if(!tested)throw new Error('Model identity was not recorded. Assessment cannot safely establish judge independence.');const names=judges.input.value==='both'?['openai','bedrock']:judges.input.value==='deterministic'?[]:[judges.input.value];await post(token,'/api/v1/runs/'+encodeURIComponent(runId)+'/assessments',{tested,judges:names.map(backend=>({backend}))});status.textContent='Assessment saved. Review the refreshed verdict and review queue.';await onRefresh();},status);
 assess.append(assessForm);content.append(assess);
 const preserve=el('details','sub-workflow');preserve.append(el('summary','','Preserve this run’s evidence'));holdForm(preserve,token,runId,onRefresh);content.append(preserve,status);container.append(content);root.prepend(container);
}
