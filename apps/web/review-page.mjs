import {setConnected} from "./chrome.mjs?v=ux-final-20261025";
import { renderReviewQueue, renderHoldManagement } from "./review-workflow.mjs?v=ux-final-20261025";
import { api } from "./runs.mjs?v=ux-final-20261025";
import { friendlyError, renderGetStarted } from "./onboarding.mjs?v=ux-final-20261025";

const error = document.getElementById("error");

async function show(token) {
  error.textContent = "";
  try {
    await api(token, "/api/v1/holds-register?state=active");
    setConnected(true);
    document.getElementById("reviews").hidden=false;
    document.getElementById("hold-management").hidden=false;
    const holds = await renderHoldManagement(document.getElementById("hold-management"),token);
    await renderReviewQueue(document.getElementById("reviews"),token,{onAddHold:holds.prefill});
    try { sessionStorage.setItem("harness-operator-token", token); } catch { /* storage may be blocked */ }
    document.querySelector("#help details").open = false;
  } catch (e) {
    setConnected(false);
    document.getElementById("reviews").hidden=true;
    document.getElementById("hold-management").hidden=true;
    error.textContent = "Could not load review & holds: " + friendlyError(e);
  }
}

document.getElementById("auth").addEventListener("submit", (event) => {
  event.preventDefault();
  show(document.getElementById("token").value);
});

let saved = "";
const help = document.getElementById("help");
try { saved = sessionStorage.getItem("harness-operator-token") ?? ""; } catch { /* ignore */ }
renderGetStarted(help, !saved);
if (saved) {
  document.getElementById("token").value = saved;
  show(saved);
}
