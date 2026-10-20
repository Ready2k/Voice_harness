/** Evidence-bound presentation helpers. No new safety verdicts are inferred here. */
export function runExplanation(run,events,assessment){
 const verdicts=assessment?.status==='assessed'?assessment:run.assurance??{};
 const denied=events.filter(e=>e.type==='tool.denied');
 const blocked=events.filter(e=>e.type==='decision'&&e.content?.target==='audio_chunk'&&['deny','redact','replace','escalate'].includes(e.content.action));
 const failed=events.filter(e=>e.type==='run.failed');
 const facts=[];
 if(verdicts.model_behaviour)facts.push('Model behaviour: '+verdicts.model_behaviour+'.');
 if(verdicts.platform_enforcement)facts.push('Platform enforcement: '+verdicts.platform_enforcement+'.');
 if(denied.length){
  const reasons=[...new Set(denied.map(e=>e.content?.error_code).filter(Boolean))];
  const labels={IDENTITY_REQUIRED:'identity verification was required',OWNERSHIP_MISMATCH:'account ownership did not match',CONFIRMATION_REQUIRED:'confirmation was required'};
  facts.push(denied.length+' tool request'+(denied.length===1?' was':'s were')+' denied'+(reasons.length?' because '+reasons.map(reason=>labels[reason]??'the recorded reason was '+reason).join('; '):'')+'.');
 }
 if(blocked.length)facts.push(blocked.length+' audio policy decision'+(blocked.length===1?' blocked or changed':'s blocked or changed')+' generated speech.');
 if(!facts.length)facts.push('No model or platform assessment is available yet.');
 return {text:facts.join(' '),next:failed.length?'Inspect the execution failure before drawing conclusions about the provider.':verdicts.model_behaviour==='fail'||verdicts.platform_enforcement==='fail'?'Review the failing criteria and supporting evidence.':'Inspect the recorded conversation and assessment coverage before making a decision.',evidence:[...denied,...blocked,...failed].filter(e=>e.event_id).map(e=>({id:e.event_id,sequence:e.sequence,type:e.type}))};
}
export function causationLinks(event,events){
 const byId=new Map(events.map(e=>[e.event_id,e]));
 return (event?.causation_event_ids??[]).filter(id=>byId.has(id)).map(id=>byId.get(id));
}
/** Cache an unchanged view without discarding focus or open disclosures. */
const signatures=new WeakMap();
export function viewChanged(root,value){const signature=JSON.stringify(value);if(signatures.get(root)===signature)return false;signatures.set(root,signature);return true;}
