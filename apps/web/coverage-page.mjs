import {setConnected} from "./chrome.mjs?v=ux-final-20261025";
import { api } from "./runs.mjs?v=ux-final-20261025";
import { load, loadProbeEvents, render, renderSummary } from "./coverage.mjs";
import { friendlyError, renderGetStarted, readinessItems } from "./onboarding.mjs?v=ux-final-20261025";

const form = document.getElementById("auth");
const error = document.getElementById("error");
const root = document.getElementById("root");

async function show() {
  error.textContent = "";
  try {
    const token = document.getElementById("token").value;
    const model = await load(location.origin, token);
    let readiness = null;
    let note = "";
    try { readiness = readinessItems(await api(token, "/api/v1/live-providers")); }
    catch (e) { note = "Could not check server readiness: " + e.message; }
    const summary = document.getElementById("summary");
    renderSummary(summary, model, readiness);
    if (note) { const p = document.createElement("p"); p.className = "muted"; p.textContent = note; summary.append(p); }
    if (readiness?.shared.length) {
      const box = document.createElement("div"); box.className = "panel";
      const h = document.createElement("strong"); h.textContent = "This server needs first (applies to every provider)";
      const ul = document.createElement("ul");
      for (const x of readiness.shared) { const li = document.createElement("li"); li.textContent = x.fix; ul.append(li); }
      box.append(h, ul); summary.append(box);
    }
    render(root, model, readiness, Date.now(), (family) => loadProbeEvents(location.origin, token, family));
    setConnected(true);
    document.getElementById("export-coverage").hidden=false;
    try { sessionStorage.setItem("harness-operator-token", token); } catch {}
    document.querySelector("#help details").open = false;
  } catch (e) {
    setConnected(false);
    document.getElementById("export-coverage").hidden=true;
    document.getElementById("summary").replaceChildren();
    root.replaceChildren();
    error.textContent = "Could not load coverage: " + friendlyError(e);
  }
}
let hadToken = false;
try { hadToken = Boolean(sessionStorage.getItem("harness-operator-token")); } catch {}
renderGetStarted(document.getElementById("help"), !hadToken);
form.addEventListener("submit", (event) => { event.preventDefault(); show(); });
try {
  const saved = sessionStorage.getItem("harness-operator-token");
  if (saved) { document.getElementById("token").value = saved; show(); }
} catch {}

document.getElementById('export-coverage').addEventListener('click',async()=>{
 const button=document.getElementById('export-coverage');button.disabled=true;
 try{const body=await api(document.getElementById('token').value,'/api/v1/providers/export');const url=URL.createObjectURL(new Blob([JSON.stringify(body,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='provider-coverage.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){error.textContent=friendlyError(e);}finally{button.disabled=false;}
});
