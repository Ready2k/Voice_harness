import { metricRows } from './runs.mjs';
export const MATCH_FIELDS=['scenario_id','scenario_version','fixture_seed','policy_profile','prompt_profile','input_mode','client_mode','retain_audio'];
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

// ---- Presentation helpers (no scoring: values are shown as recorded) ----
const taskKey=r=>(r.scenario_id??'unknown')+'@'+(r.scenario_version??'?');
const byNewest=(a,b)=>Date.parse(b.started_at??'')-Date.parse(a.started_at??'')||0;
/** Pure: tasks with their run counts, tasks with 2+ runs first, then most runs, then newest. */
export function taskGroups(runs){
 const groups=new Map();
 for(const r of runs){const k=taskKey(r);const g=groups.get(k)??{key:k,count:0,latest:0};g.count++;g.latest=Math.max(g.latest,Date.parse(r.started_at??'')||0);groups.set(k,g);}
 return [...groups.values()].sort((a,b)=>(b.count>=2)-(a.count>=2)||b.count-a.count||b.latest-a.latest);
}
export function taskLabel(g){return g.key+' · '+g.count+' run'+(g.count===1?'':'s');}
export function defaultTask(runs){return taskGroups(runs)[0]?.key??null;}
/** Pure: newest run of each provider (at most `max`). */
export function latestPerProvider(runs,max=8){
 const seen=new Set();const out=[];
 for(const r of [...runs].sort(byNewest)){const p=r.provider_config_id??'unknown';if(seen.has(p))continue;seen.add(p);out.push(r.run_id);}
 return out.slice(0,max);
}
/** Pure: newest `max` runs of one provider. */
export function repeatsOfProvider(runs,provider,max=8){
 return [...runs].filter(r=>(r.provider_config_id??'unknown')===provider).sort(byNewest).slice(0,max).map(r=>r.run_id);
}
/** Pure: live controls only make sense while a run can still change. */
export function isLive(run){return !['completed','cancelled','failed'].includes(run?.state);}

const isLatency=n=>/latency|first_audio|ttfa|round_trip|response_time/i.test(n);
function show(m){
 if(!m)return null;
 if(m.value==null)return null;
 if(typeof m.value==='object'){return Object.entries(m.value).map(([k,v])=>k+' '+(typeof v==='number'?Number(v.toPrecision(4)):v)).join(' · ')+(m.unit?' '+m.unit:'');}
 return (typeof m.value==='number'?String(Number(m.value.toPrecision(6))):String(m.value))+(m.unit&&m.unit!=='count'?' '+m.unit:'');
}
/** Pure: X / Y / delta rows. Rows where neither run has a value are returned in `hidden` (name + reason). Latency rows come first. */
export function metricTable(x,y,label=n=>n){
 const a=metricRows(x.metrics),b=metricRows(y.metrics);
 const deltas=new Map(metricDeltas(x,y).map(d=>[d.name,d]));
 const names=[...new Set([...a,...b].map(m=>m.name))];
 const rows=[],hidden=[];
 for(const name of names){
  const l=a.find(m=>m.name===name),r=b.find(m=>m.name===name);
  const rawL=(x.metrics?.observations??[]).find(m=>(m.metric??m.name)===name),rawR=(y.metrics?.observations??[]).find(m=>(m.metric??m.name)===name);
  if(rawL?.value&&typeof rawL.value==='object'||rawR?.value&&typeof rawR.value==='object'){
   const lo=rawL?.value&&typeof rawL.value==='object'?rawL.value:{},ro=rawR?.value&&typeof rawR.value==='object'?rawR.value:{};
   const unit=rawL?.unit??rawR?.unit??'';const ok=!rawL?.exclusion_reason&&!rawR?.exclusion_reason&&(rawL?.unit??null)===(rawR?.unit??null);
   for(const k of ['count','min','mean','p50','p95','max']){
    const a1=lo[k],b1=ro[k];const na=typeof a1==='number'&&Number.isFinite(a1),nb=typeof b1==='number'&&Number.isFinite(b1);
    if(!na&&!nb)continue;
    const u=k==='count'?'':unit;const fmt=v=>typeof v==='number'?Number(v.toPrecision(6))+(u?' '+u:''):null;
    const dl=ok&&na&&nb?Number((b1-a1).toPrecision(8)):null;
    rows.push({name:name+'.'+k,label:label(name)+' · '+k,x:fmt(a1),y:fmt(b1),delta:dl,unit:u,direction:dl==null?null:dl>0?'up':dl<0?'down':'same',latency:isLatency(name)});
   }
   continue;
  }
  const xs=show(l),ys=show(r);
  if(xs==null&&ys==null){hidden.push({name,label:label(name),reason:l?.reason??r?.reason??'not measured'});continue;}
  const d=deltas.get(name);
  rows.push({name,label:label(name),x:xs,y:ys,delta:d?.delta??null,unit:d?.unit??'',direction:d?.delta==null?null:d.delta>0?'up':d.delta<0?'down':'same',latency:isLatency(name)});
 }
 rows.sort((p,q)=>Number(q.latency)-Number(p.latency));
 return {rows,hidden};
}

/** Pure: pair two transcripts turn by turn. A turn starts at each customer line; assistant lines belong to the latest turn.
 *  Superseded (earlier-version) assistant lines are dropped unless `showRevisions`. */
export function alignTurns(xLines,yLines,showRevisions=false){
 const turns=lines=>{
  const out=[];let cur=null;
  for(const l of lines??[]){
   if(l.speaker==='customer'||!cur){cur={customer:l.speaker==='customer'?l:null,assistant:[]};out.push(cur);if(l.speaker==='customer')continue;}
   if(!showRevisions&&l.state==='superseded')continue;
   cur.assistant.push(l);
  }
  return out;
 };
 const tx=turns(xLines),ty=turns(yLines);
 return Array.from({length:Math.max(tx.length,ty.length)},(_,i)=>({turn:i+1,x:tx[i]??null,y:ty[i]??null}));
}
export function hasRevisions(lines){return (lines??[]).some(l=>l.state==='superseded');}
