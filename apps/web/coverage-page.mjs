import { api } from "./runs.mjs";
import { load, render } from "./coverage.mjs";
import { friendlyError, renderGetStarted, renderReadiness } from "./onboarding.mjs";

const form = document.getElementById("auth");
const error = document.getElementById("error");
const root = document.getElementById("root");

async function show() {
  error.textContent = "";
  try {
    const token = document.getElementById("token").value;
    render(root, await load(location.origin, token));
    const readiness = document.getElementById("readiness");
    readiness.replaceChildren();
    try {
      const providers = await api(token, "/api/v1/live-providers");
      renderReadiness(readiness, providers);
    } catch (e) { readiness.textContent = "Could not check server readiness: " + e.message; }
    try { sessionStorage.setItem("harness-operator-token", token); } catch {}
    document.querySelector("#help details").open = false;
  } catch (e) {
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
