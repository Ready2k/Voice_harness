import {setConnected} from "./chrome.mjs?v=ux-audit-20261018";
import { renderReviewQueue, renderHoldManagement } from "./review-workflow.mjs?v=ux-audit-20261018";
import { load, render } from "./review.mjs";
import { friendlyError, renderGetStarted } from "./onboarding.mjs?v=ux-audit-20261018";

const error = document.getElementById("error");
const root = document.getElementById("root");

async function show(token) {
  error.textContent = "";
  try {
    render(root, await load(token));
    setConnected(true);
    document.getElementById("reviews").hidden=false;
    document.getElementById("hold-management").hidden=false;
    await renderReviewQueue(document.getElementById("reviews"),token);
    await renderHoldManagement(document.getElementById("hold-management"),token);
    try { sessionStorage.setItem("harness-operator-token", token); } catch { /* storage may be blocked */ }
    document.querySelector("#help details").open = false;
  } catch (e) {
    setConnected(false);
    document.getElementById("reviews").hidden=true;
    document.getElementById("hold-management").hidden=true;
    root.replaceChildren();
    error.textContent = "Could not load holds: " + friendlyError(e);
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
