import { load, render } from "./review.mjs";
import { friendlyError, renderGetStarted } from "./onboarding.mjs";

const error = document.getElementById("error");
const root = document.getElementById("root");

async function show(token) {
  error.textContent = "";
  try {
    render(root, await load(token));
    try { sessionStorage.setItem("harness-operator-token", token); } catch { /* storage may be blocked */ }
    document.querySelector("#help details").open = false;
  } catch (e) {
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
