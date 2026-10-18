import {setConnected} from "./chrome.mjs?v=ux-audit-20261018";
import { api } from "./runs.mjs?v=ux-audit-20261018";
import { load, render } from "./coverage.mjs";
import { friendlyError, renderGetStarted, renderReadiness } from "./onboarding.mjs?v=ux-audit-20261018";

const form = document.getElementById("auth");
const error = document.getElementById("error");
const root = document.getElementById("root");

async function show() {
  error.textContent = "";
  try {
    const token = document.getElementById("token").value;
    render(root, await load(location.origin, token));
    setConnected(true);
    document.getElementById("export-coverage").hidden=false;
    const readiness = document.getElementById("readiness");
    readiness.replaceChildren();
    try {
      const providers = await api(token, "/api/v1/live-providers");
      renderReadiness(readiness, providers);
    } catch (e) { readiness.textContent = "Could not check server readiness: " + e.message; }
    try { sessionStorage.setItem("harness-operator-token", token); } catch {}
    document.querySelector("#help details").open = false;
  } catch (e) {
    setConnected(false);
    document.getElementById("export-coverage").hidden=true;
    document.getElementById("readiness").textContent="Connect to check current readiness.";
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
