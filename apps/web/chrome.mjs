import {el} from './runs.mjs?v=ux-audit-20261018';
const main=document.querySelector('main');
if(main){
 main.id='main-content';const skip=el('a','skip-link','Skip to main content');skip.href='#main-content';document.body.prepend(skip);
 const nav=main.querySelector('nav');if(nav){const header=el('header','app-header');const logo=el('a','app-brand','◈ Voice Harness');logo.href='./runs.html';header.append(logo,nav);main.prepend(header);for(const link of nav.querySelectorAll('a'))if(link.getAttribute('href')==='./review.html')link.textContent='Review & holds';}
 const auth=document.getElementById('auth');if(auth){const wrapper=el('details','connection-drawer');const summary=el('summary','','Connection & access');wrapper.append(summary);auth.before(wrapper);wrapper.append(auth);const note=el('p','muted','The access token stays in this tab. Provider credentials remain on the server.');const disconnect=el('button','','Disconnect this tab');disconnect.type='button';disconnect.addEventListener('click',()=>{sessionStorage.removeItem('harness-operator-token');location.reload();});wrapper.append(note,disconnect);let saved=false;try{saved=Boolean(sessionStorage.getItem('harness-operator-token'));}catch{}wrapper.open=!saved;}

}
export function setConnected(connected){const drawer=document.querySelector('.connection-drawer');if(!drawer)return;drawer.querySelector('summary').textContent=connected?'Connected · access & disconnect':'Connection required';drawer.open=!connected;}
