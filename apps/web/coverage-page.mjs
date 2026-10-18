import { api, el } from "./runs.mjs";
import { load, render } from "./coverage.mjs";

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
      for (const p of providers.items) {
        const reasons = {NO_ASR_BACKEND_CONFIGURED: "Configure independent ASR and its API credentials on the server.", NO_CALLER_VOICE_SYNTH: "Caller voice synthesis is unavailable on this server.", NO_AWS_SESSION: "Configure an active AWS session on the server."};
        readiness.append(el("p", "meta", p.provider_config_id + " · " + (p.available ? "Ready to run" : "Unavailable: " + (reasons[p.reason] ?? p.reason))));
      }
    } catch (e) { readiness.textContent = "Could not check server readiness: " + e.message; }
    try { sessionStorage.setItem("harness-operator-token", token); } catch {}
  } catch (e) {
    root.replaceChildren();
    error.textContent = "Could not load coverage: " + e.message;
  }
}
form.addEventListener("submit", (event) => { event.preventDefault(); show(); });
try {
  const saved = sessionStorage.getItem("harness-operator-token");
  if (saved) { document.getElementById("token").value = saved; show(); }
} catch {}
