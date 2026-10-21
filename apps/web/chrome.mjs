import {el} from './runs.mjs?v=ux-final-20261025';
import {PAGES,pageMeta} from './nav.mjs?v=ux-final-20261025';
import {renderGetStarted} from './onboarding.mjs?v=ux-final-20261025';
const THEMES=['auto','light','dark'];
export function applyTheme(choice){const root=document.documentElement;if(choice==='light'||choice==='dark')root.dataset.theme=choice;else delete root.dataset.theme;}
export function savedTheme(){try{const t=localStorage.getItem('harness-theme');return THEMES.includes(t)?t:'auto';}catch{return 'auto';}}
applyTheme(savedTheme());
const main=document.querySelector('main');
if(main){
 main.id='main-content';const skip=el('a','skip-link','Skip to main content');skip.href='#main-content';document.body.prepend(skip);
 const nav=main.querySelector('nav');if(nav){const header=el('header','app-header');const logo=el('a','app-brand','◈ Voice Harness');logo.href='./runs.html';header.append(logo,nav);main.prepend(header);for(const link of nav.querySelectorAll('a'))if(link.getAttribute('href')==='./review.html')link.textContent='Review & holds';}
 const current=nav?.querySelector('a[aria-current="page"]')?.textContent??PAGES[location.pathname.split('/').pop()];
 if(current){const meta=pageMeta(current);document.title=meta.title;let crumb=main.querySelector('.eyebrow');if(!crumb){crumb=el('p','eyebrow');const h1=main.querySelector('h1');if(h1)h1.before(crumb);else main.querySelector('header')?.after(crumb);}crumb.textContent=meta.breadcrumb;if(current==='Review & holds'){const h1=main.querySelector('h1');if(h1)h1.textContent=current;}}
 const auth=document.getElementById('auth');if(auth){const wrapper=el('details','connection-drawer');const summary=el('summary','','Connection & access');wrapper.append(summary);auth.before(wrapper);wrapper.append(auth);const note=el('p','muted','The access token stays in this tab. Provider credentials remain on the server.');wrapper.append(note);
  const signOut=()=>{try{sessionStorage.removeItem('harness-operator-token');}catch{}location.reload();};
  // Once connected, the setup panel gives way to a status pill in the header.
  const pill=el('div','conn-pill');pill.hidden=true;pill.setAttribute('role','status');const dot=el('span','conn-dot');const label=el('span','','Connected');const out=el('button','link','Disconnect');out.type='button';out.addEventListener('click',signOut);pill.append(dot,label,out);
  const header=main.querySelector('.app-header');
  let saved=false;try{saved=Boolean(sessionStorage.getItem('harness-operator-token'));}catch{}wrapper.open=!saved;
  // Pages without their own #help get the Get started guide here; setConnected hides it once connected.
  if(!document.getElementById('help')){const help=el('div');help.id='help';wrapper.before(help);renderGetStarted(help,!saved);}
  if(header){const tg=el('label','theme-toggle','Theme ');const sel=el('select');sel.setAttribute('aria-label','Colour theme');for(const t of THEMES){const o=el('option','',t[0].toUpperCase()+t.slice(1));o.value=t;sel.append(o);}sel.value=savedTheme();sel.addEventListener('change',()=>{applyTheme(sel.value);try{localStorage.setItem('harness-theme',sel.value);}catch{}});tg.append(sel);header.append(tg,pill);}}

}
export function setConnected(connected){const drawer=document.querySelector('.connection-drawer');if(!drawer)return;drawer.hidden=connected;drawer.open=!connected;const pill=document.querySelector('.conn-pill');if(pill)pill.hidden=!connected;const help=document.getElementById('help');if(help)help.hidden=connected;}
