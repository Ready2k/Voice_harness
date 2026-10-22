import {api, el} from './runs.mjs?v=ux-final-20261025';
import {balanceView} from './balances.mjs';
let started=false;
export function startBalanceNotice(){
  if(started||location.pathname.endsWith('/balances.html'))return;
  started=true;
  const notice=el('p','balance-notice');notice.setAttribute('role','status');notice.hidden=true;
  const text=el('span');const link=el('a','','View balances →');link.href='./balances.html';notice.append(text,document.createTextNode(' '),link);
  document.querySelector('main h1')?.before(notice);
  let busy=false;
  async function check(){
    if(busy||document.hidden)return;
    let token;try{token=sessionStorage.getItem('harness-operator-token')||document.getElementById('token')?.value;}catch{}if(!token)return;
    busy=true;
    try{
      const data=await api(token,'/api/v1/provider-balances');
      const low=data.items.filter(item=>balanceView(item).low).map(item=>item.name);
      const unknown=data.items.filter(item=>{const v=balanceView(item);return !v.live||v.stale;});
      text.textContent=low.length ? 'Low or exhausted credit reported for '+low.join(', ')+'. Check before starting calls.' : unknown.length ? `Credit visibility: ${unknown.length} of ${data.items.length} providers have no current live balance.` : '';
      notice.hidden=!text.textContent;
    }catch{
      text.textContent='Provider balances could not be checked. Verify credit before starting calls.';notice.hidden=false;
    }finally{busy=false;}
  }
  check();setInterval(check,60000);
}
