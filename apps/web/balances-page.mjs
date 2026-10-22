import {setConnected} from './chrome.mjs?v=ux-final-20261025';
import {api, el} from './runs.mjs?v=ux-final-20261025';
import {friendlyError} from './onboarding.mjs?v=ux-final-20261025';
import {balanceSummary, renderBalanceCard} from './balances.mjs';

const token=document.getElementById('token'), error=document.getElementById('error');
const refresh=document.getElementById('refresh'), root=document.getElementById('balance-grid');
const summary=document.getElementById('balance-summary'), checked=document.getElementById('checked');
const cards=new Map();let busy=false, connected=false, current=null;
function draw(data){
  current=data;
  for(const item of data.items){
    if(!cards.has(item.id)){
      const card=renderBalanceCard(item,async(id,settings)=>{
        await api(token.value,'/api/v1/provider-balances/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings)});
        await load();
      });cards.set(item.id,card);root.append(card.card);
    }
    cards.get(item.id).update(item);
  }
  const counts=balanceSummary(data.items);
  summary.replaceChildren();
  for(const [n,label] of [[counts.live,'of 4 live readings'],[counts.low,'at or beyond alert threshold'],[4-counts.live,'without a current live reading']]){
    const tile=el('div','tile');tile.append(el('strong','',String(n)),el('span','',label));summary.append(tile);
  }
}
async function load(){
  if(busy)return;busy=true;refresh.disabled=true;refresh.textContent='Checking…';error.textContent='';
  try{
    const data=await api(token.value,'/api/v1/provider-balances');connected=true;setConnected(true);
    try{sessionStorage.setItem('harness-operator-token',token.value);}catch{}
    draw(data);checked.textContent='Page checked '+new Date(data.generated_at*1000).toLocaleTimeString()+'. Credit checks are cached for up to one minute; AWS spend is cached for up to 24 hours.';
  }catch(e){
    error.textContent='Could not refresh balances: '+friendlyError(e)+'. Previous figures, if shown, are not a fresh check.';
    if(e.message==='AUTH_REQUIRED'||e.message==='FORBIDDEN'){
      connected=false;setConnected(false);root.replaceChildren();summary.replaceChildren();cards.clear();current=null;checked.textContent='';
    }
  }finally{busy=false;refresh.disabled=false;refresh.textContent='Refresh readings';}
}
document.getElementById('auth').addEventListener('submit',e=>{e.preventDefault();load();});
refresh.addEventListener('click',load);
try{token.value=sessionStorage.getItem('harness-operator-token')??'';if(token.value)load();}catch{}
setInterval(()=>{if(current)draw(current);if(connected&&!document.hidden)load();},60000);
document.addEventListener('visibilitychange',()=>{if(connected&&!document.hidden)load();});
