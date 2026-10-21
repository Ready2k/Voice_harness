import {api,el} from './runs.mjs?v=ux-final-20261025';
import {action,words} from './workflow.mjs?v=ux-final-20261025';
export function mountReports(root,token){
 const details=el('details','panel');details.append(el('summary','','Saved statistical comparison reports'));
 const content=el('div');details.append(content);let loaded=false;
 details.addEventListener('toggle',async()=>{if(!details.open||loaded)return;loaded=true;try{const response=await api(token,'/api/v1/comparisons');if(!response.items.length){content.append(el('p','muted','No stored statistical reports. Run comparison above is descriptive; it does not fabricate confidence intervals.'));return;}for(const record of response.items)content.append(action(record.comparison_id+' · '+new Date(record.created_at*1000).toLocaleString(),()=>open(record.comparison_id),'queue-item'));}catch(e){loaded=false;content.textContent='Could not load reports: '+e.message;}});
 const selected=el('section');content.append(selected);
 async function open(id){
  try{const result=await api(token,'/api/v1/comparisons/'+encodeURIComponent(id)),report=result.report;selected.replaceChildren(el('h3','',id),el('p','',report.statement),el('p','muted',report.exploratory_note??report.evidence_grade),el('p','trace-insight','These reports use submitted observations. Resolving a run ID confirms the run exists; it does not prove that reported measurements were extracted from its ledger.'));
   for(const [group,label] of [['model_outcomes','Model outcomes'],['platform_outcomes','Platform outcomes'],['latency','Latency']]){selected.append(el('h3','',label),el('p','muted','Providers: '+(report.providers??[]).join(', ')));const t=el('table');const head=el('tr');for(const name of ['Measure / pair','Paired clusters','A − B','95% interval','Limitations'])head.append(el('th','',name));t.append(head);for(const [name,measure] of Object.entries(report[group]??{}))for(const [index,pair] of (measure.paired??[]).entries()){const tr=el('tr');for(const value of [words(name)+' · '+pairedLabels(report.providers??[],measure.paired??[])[index],pair.paired_clusters,pair.difference_a_minus_b??'Unavailable',pair.interval?.join(' to ')??'Unavailable',words(pair.reason??pair.interval_status??pair.unit)])tr.append(el('td','',String(value)));t.append(tr);}selected.append(t);}
   const raw=el('details');raw.append(el('summary','','Sample counts, exclusions, versions & report evidence'),el('pre','event-json',JSON.stringify(result,null,2)));selected.append(raw,action('Download report with provenance',async()=>{try{const body=await api(token,'/api/v1/comparisons/'+encodeURIComponent(id)+'/export');const url=URL.createObjectURL(new Blob([JSON.stringify(body,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download=id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){selected.append(el('p','',e.message));}}));
  }catch(e){selected.textContent='Could not load report: '+e.message;}
 }
 root.replaceChildren(details);
}

export function pairedLabels(providers,pairs){
 const labels=[];for(let a=0;a<providers.length;a++)for(let b=a+1;b<providers.length;b++)labels.push(providers[a]+' vs '+providers[b]);
 return pairs.map((_,index)=>labels.length===pairs.length?labels[index]:'Pair '+(index+1)+' (see source report)');
}
