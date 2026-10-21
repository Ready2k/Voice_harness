import {setConnected} from './chrome.mjs?v=ux-final-20261025';
import {api,el} from './runs.mjs?v=ux-final-20261025';
import {post,formAction,action} from './workflow.mjs?v=ux-final-20261025';
import {friendlyError} from './onboarding.mjs?v=ux-final-20261025';
import {relativeTime,runLink} from './format.mjs?v=ux-final-20261025';
import {providerEl} from './providers.mjs?v=ux-final-20261025';
import {audioLabel,bakeoffIndex,buildGroups,filterOptions,latestResults,matches,matrix,summaryModel,tally,toolLabel} from './scenarios-view.mjs?v=ux-final-20261025';
const $=id=>document.getElementById(id);
let token='',items=[],cursor=null,results=new Map(),bakeoffs=new Map();
const selected=new Map(),expanded=new Set(),tabs=new Map(),collapsed=new Set();
const FILTERS={query:'search',group:'f-group',locale:'f-locale',audio:'f-audio',criterion:'f-criterion',mode:'mode'};
const filters=()=>Object.fromEntries(Object.entries(FILTERS).map(([k,id])=>[k,$(id).value]));

async function loadResults(){
  try{
    const runs=[];let c=0;
    for(let i=0;i<3;i++){const page=await api(token,'/api/v1/runs?limit=200&cursor='+c);runs.push(...page.items);if(page.next_cursor==null)break;c=page.next_cursor;}
    results=latestResults(runs);
  }catch{results=new Map();}
  try{
    const list=(await api(token,'/api/v1/bakeoffs')).items.slice(0,15);
    const details=(await Promise.allSettled(list.map(b=>api(token,'/api/v1/bakeoffs/'+encodeURIComponent(b.bakeoff_id))))).filter(r=>r.status==='fulfilled').map(r=>r.value);
    bakeoffs=bakeoffIndex(details);
  }catch{bakeoffs=new Map();}
}
async function load(more=false){
  const result=await api(token,'/api/v1/scenarios?limit=100'+(more&&cursor!=null?'&cursor='+cursor:''));
  items=more?[...items,...result.items]:result.items;cursor=result.next_cursor;$('more').hidden=cursor==null;
  fillOptions();render();
}
function fillOptions(){
  const o=filterOptions(items);
  const fill=(id,values,label)=>{const sel=$(id),keep=sel.value;sel.replaceChildren(...[['all',label],...values.map(v=>[v,id==='f-audio'?audioLabel(v):v])].map(([v,t])=>{const opt=el('option','',t);opt.value=v;return opt;}));sel.value=[...sel.options].some(x=>x.value===keep)?keep:'all';};
  fill('f-group',o.groups,'All groups');fill('f-locale',o.locales,'All locales');fill('f-audio',o.audios,'All audio');fill('f-criterion',o.criteria,'All criteria');
}
function resultsList(s){
  const map=results.get(s.scenario_id);
  if(!map||!map.size)return el('p','muted','No runs yet for this scenario.');
  const ul=el('ul','results');ul.setAttribute('aria-label','Latest result per provider');
  for(const r of map.values()){
    const li=el('li');li.append(providerEl(r.provider),el('span','res-'+r.result.key,r.result.label));
    const run=el('a','','run');run.href=runLink(r.run_id);run.title='Open run '+r.run_id;li.append(run);
    const b=bakeoffs.get(r.run_id);if(b?.id){const a=el('a','','bake-off');a.href='./bakeoff.html?bakeoff='+encodeURIComponent(b.id);a.title=b.name??b.id;li.append(a);}
    if(r.at)li.append(el('span','muted',relativeTime(r.at)));
    ul.append(li);
  }
  return ul;
}
function dl(rows){const d=el('dl');for(const[k,v]of rows){d.append(el('dt','',k));const dd=el('dd');if(v instanceof Node)dd.append(v);else dd.textContent=v;d.append(dd);}return d;}
function summaryPanel(s){
  const m=summaryModel(s),box=el('div','summary');
  const crit=el('ul');for(const c of m.criteria){const li=el('li'),t=el('span','crit',c.id);t.title=c.help;t.tabIndex=0;li.append(t,' – '+c.help);crit.append(li);}
  const toolList=(ids,sep)=>{const w=el('span');ids.forEach((id,i)=>{if(i)w.append(sep);const c=el('span','tool-name',toolLabel(id));c.title=id;w.append(c);});return w;};
  const tools=el('div');tools.append(m.tools.none?el('span','',m.tools.text):toolList(m.tools.sequence,' → '));if(m.tools.note)tools.append(el('p','muted',m.tools.note));if(m.tools.allowed.length&&!m.tools.none){const p=el('p','muted','Allowed: ');p.append(toolList(m.tools.allowed,', '));tools.append(p);};
  const rows=[['Persona',m.persona+(m.vulnerability!=='None'?' · vulnerability: '+m.vulnerability:'')],['Locale and audio',m.locale+' · '+m.audio],['Initial state',m.identity+' caller · fixture '+m.fixture],['Goal',m.goal]];
  if(m.caller.length){const u=el('ul');for(const l of m.caller)u.append(el('li','','“'+l+'”'));rows.push(['Caller says',u]);}
  rows.push(['Expected tools',tools]);
  if(m.stops.length)rows.push(['Stops when',m.stops.join('; ')]);
  if(m.faults.length)rows.push(['Injected faults',m.faults.join('; ')]);
  if(m.mechanics.length)rows.push(['Mechanics',m.mechanics.join('; ')]);
  rows.push(['Limits',m.limits],['Criteria',crit]);
  box.append(dl(rows));return box;
}
function download(s){return action('Download definition',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(s.definition,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download=s.scenario_id+'-v'+s.version+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});}
function familyCard(f,visible){
  const card=el('section','panel family');card.dataset.key=f.key;
  const sel=visible.find(v=>v.scenario_id===selected.get(f.key))??visible[0];selected.set(f.key,sel.scenario_id);
  const redraw=(focus)=>{const next=familyCard(f,visible);card.replaceWith(next);if(focus)next.querySelector(focus)?.focus();};
  card.append(el('h3','',f.title));
  if(f.outcome)card.append(el('p','',f.outcome));
  const mx=matrix(visible);
  if(mx.isMatrix){
    const t=el('table','matrix');t.setAttribute('aria-label','Locale and audio variants');
    const head=el('tr');head.append(el('th','','Locale'));for(const a of mx.audios)head.append(el('th','',audioLabel(a)));t.append(head);
    for(const row of mx.rows){const tr=el('tr');tr.append(el('th','',row.locale));row.cells.forEach((v,i)=>{const td=el('td');if(!v)td.append(el('span','none','–'));else{const tl=tally(results.get(v.scenario_id));const label=tl.pass||tl.fail||tl.other?(tl.pass?'✓'+tl.pass:'')+(tl.fail?' ✗'+tl.fail:'')+(tl.other&&!tl.pass&&!tl.fail?'…':''):'No runs yet';const b=el('button','',label.trim());b.type='button';b.setAttribute('aria-pressed',String(v.scenario_id===sel.scenario_id));const nr=!(tl.pass||tl.fail||tl.other);const cd=v.scenario_id+' · '+row.locale+' · '+audioLabel(mx.audios[i]);b.title=nr?cd+' · No runs yet for this variant. Select it to view details or start a run.':cd;if(nr)b.setAttribute('aria-label',b.title);b.dataset.id=v.scenario_id;b.addEventListener('click',()=>{selected.set(f.key,v.scenario_id);redraw('button[data-id="'+v.scenario_id+'"]');});td.append(b);}tr.append(td);});t.append(tr);}
    card.append(t);
  }
  card.append(el('p','muted vid',sel.scenario_id+' · v'+sel.version+' · '+(!sel.runnable?'Stored definition':sel.execution==='live_adapter'?'Live provider':'Scripted fixture')),resultsList(sel));
  const open=expanded.has(sel.scenario_id);
  const toggle=el('button','quiet',open?'Hide details':'Show details');toggle.type='button';toggle.setAttribute('aria-expanded',String(open));
  toggle.addEventListener('click',()=>{open?expanded.delete(sel.scenario_id):expanded.add(sel.scenario_id);redraw('.toggle');});toggle.classList.add('toggle');
  const actions=el('div','actions');
  if(sel.runnable){const launch=el('a','','Configure evaluation →');launch.href='./runs.html?scenario='+encodeURIComponent(sel.scenario_id);actions.append(launch);}
  actions.append(download(sel),toggle);card.append(actions);
  if(open){
    const tab=tabs.get(sel.scenario_id)??'summary';
    const bar=el('div','tabs');bar.setAttribute('role','tablist');
    for(const[id,label]of[['summary','Summary'],['raw','Raw JSON']]){const b=el('button','',label);b.type='button';b.setAttribute('role','tab');b.setAttribute('aria-selected',String(tab===id));b.dataset.tab=id;b.addEventListener('click',()=>{tabs.set(sel.scenario_id,id);redraw('button[data-tab="'+id+'"]');});bar.append(b);}
    card.append(bar,tab==='raw'?el('pre','event-json',JSON.stringify(sel.definition,null,2)):summaryPanel(sel));
  }
  return card;
}
function render(){
  const f=filters(),visible=items.filter(s=>matches(s,f));
  $('count').textContent=visible.length+' of '+items.length+' scenarios shown';
  const lib=$('library');lib.replaceChildren();
  for(const g of buildGroups(visible)){
    const d=el('details','group');d.open=!collapsed.has(g.group);
    const count=g.families.length;
    d.append(el('summary','',g.group));d.firstChild.append(el('span','muted',count+(count===1?' scenario':' scenarios')+' · '+g.families.reduce((n,x)=>n+x.variants.length,0)+' variants'));
    d.addEventListener('toggle',()=>{d.open?collapsed.delete(g.group):collapsed.add(g.group);});
    const grid=el('div','family-grid');for(const fam of g.families)grid.append(familyCard(fam,fam.variants));
    d.append(grid);lib.append(d);
  }
  if(!visible.length)lib.append(el('p','muted','No matching scenarios. Try clearing a filter.'));
}
async function connect(){token=$('token').value;try{await load();setConnected(true);$('library-controls').hidden=false;$('import-open').hidden=false;sessionStorage.setItem('harness-operator-token',token);$('error').textContent='';loadResults().then(render);}catch(e){setConnected(false);$('error').textContent=friendlyError(e);}}
$('auth').addEventListener('submit',e=>{e.preventDefault();connect();});
for(const id of Object.values(FILTERS))$(id).addEventListener(id==='search'?'input':'change',render);
$('clear').addEventListener('click',()=>{for(const id of Object.values(FILTERS))$(id).value=id==='search'?'':'all';render();});
$('more').addEventListener('click',()=>load(true).catch(e=>$('error').textContent=friendlyError(e)));
$('import-open').addEventListener('click',()=>$('import-dialog').showModal());$('import-close').addEventListener('click',()=>$('import-dialog').close());
formAction($('import'),$('save'),async()=>{let definition;try{definition=JSON.parse($('definition').value);}catch{throw new Error('The definition is not valid JSON. Check quotes, commas and brackets.');}const result=await post(token,'/api/v1/scenarios',definition);$('import-status').textContent=(result.created?'Stored new definition: ':'Definition already exists: ')+result.scenario_id+' · v'+result.version;await load();},$('import-status'));
try{const saved=sessionStorage.getItem('harness-operator-token');if(saved){$('token').value=saved;connect();}}catch{}
