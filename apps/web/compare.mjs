import { metricRows } from './runs.mjs';
export const MATCH_FIELDS=['scenario_id','scenario_version','fixture_seed','policy_profile','input_mode','client_mode','retain_audio'];
export function comparisonCheck(runs){
 if(runs.length<2||runs.length>8)throw new Error('Choose between 2 and 8 distinct runs.');
 if(new Set(runs.map(r=>r.run_id)).size!==runs.length)throw new Error('Choose distinct runs for comparison.');
 const first=runs[0].config;
 if(runs.some(r=>r.config.scenario_id!==first.scenario_id||r.config.scenario_version!==first.scenario_version))throw new Error('Runs must use the same scenario and version.');
 const differences=MATCH_FIELDS.filter(k=>runs.some(r=>r.config[k]!==first[k]));
 const callerKey = config => {
  const c = config.caller ?? {mode:'fixed',fixed_backend:'system'};
  return c.mode === 'live_ai' ? JSON.stringify([c.mode,c.persona,c.voice,c.max_turns,c.max_duration_seconds])
    : JSON.stringify(['fixed',c.fixed_backend??'system']);
 };
 if(runs.some(r=>callerKey(r.config)!==callerKey(first)))differences.push('caller');
 const missing=MATCH_FIELDS.filter(k=>runs.some(r=>r.config[k]==null));
 return {differences,missing,adaptiveCaller:runs.some(r=>r.config.caller?.mode==='live_ai'),providers:[...new Set(runs.map(r=>r.config.provider_config_id))]};
}
export function batchPlan(option, providers, seed, count, retain, batchId){
 if(!Number.isSafeInteger(seed)||!Number.isSafeInteger(seed+count-1))throw new Error('Use a safe integer fixture seed.');
 if(!Number.isInteger(count)||count<1||count>5||!providers.length||providers.length*count>8||providers.length*count<2)throw new Error('A batch needs 2–8 runs, with 1–5 runs per configuration.');
 if(new Set(providers).size!==providers.length)throw new Error('Duplicate provider configuration.');
 if(!option.live&&(providers.length!==1||providers[0]!=='mock-banking-v1'))throw new Error('Scripted fixtures use the mock configuration only.');
 if(option.live&&providers.includes('mock-banking-v1'))throw new Error('Live scenarios require live provider configurations.');
 return providers.flatMap(provider=>Array.from({length:count},(_,i)=>({key:batchId+'-'+provider+'-'+i,body:{scenario_id:option.id,scenario_version:option.version,provider_config_id:provider,fixture_seed:seed+i,policy_profile:'strict-v1',input_mode:'fixed_audio',repetitions:1,retain_audio:retain}})));
}
export function metricMatrix(entries){
 const names=[...new Set(entries.flatMap(e=>metricRows(e.metrics).map(m=>m.name)))];
 return names.map(name=>({name,values:entries.map(e=>{const m=metricRows(e.metrics).find(m=>m.name===name);return !m?'Unavailable':m.value==null?'Unavailable: '+m.reason:String(m.value)+' '+m.unit;})}));
}
export function costDescription(cost){
 if(!cost)return 'Unavailable';
 const parts=(cost.summaries??[]).map(s=>s.currency+' '+(s.total_cost??s.known_subtotal)+' ('+(s.total_cost==null?'known subtotal':'total')+'); provenance: '+Object.entries(s.by_provenance??{}).map(([p,v])=>p+' '+v).join(', '));
 return (cost.completeness??'unknown')+' · '+(parts.join(' | ')||'No known monetary amount')+' · Unknown lines: '+(cost.unknown_line_count??'unknown')+' · Unknown components: '+((cost.unknown_components??[]).join(', ')||'none recorded');
}
export function metricDeltas(x,y){
 const a=x.metrics?.observations??[],b=y.metrics?.observations??[];
 return [...new Set([...a,...b].map(m=>m.metric??m.name))].map(name=>{
  const left=a.find(m=>(m.metric??m.name)===name),right=b.find(m=>(m.metric??m.name)===name);
  const eligible=left&&right&&typeof left.value==='number'&&typeof right.value==='number'&&Number.isFinite(left.value)&&Number.isFinite(right.value)&&left.unit===right.unit&&!left.exclusion_reason&&!right.exclusion_reason;
  return {name,delta:eligible?Number((right.value-left.value).toPrecision(8)):null,unit:eligible?left.unit??'':'',reason:eligible?null:'Unavailable: requires numeric measurements with matching units and no exclusions.'};
 });
}
