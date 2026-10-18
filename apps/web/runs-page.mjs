import { createWorkspace, renderOverview, renderRecent } from "./live-view.mjs?v=20261019-redesign2";
import { friendlyError, renderGetStarted, renderReadiness } from "./onboarding.mjs";
import {
  api, fetchEvents, launchBody, launchOptions, mergeEvents, providerChoices, TERMINAL, el,
} from "./runs.mjs";

const $ = (id) => document.getElementById(id);
const error = $("error");
let token = "";
let options = [];
let providers = [];
let liveProvidersPayload = {items:[]};
let workspace = null;
let overviewTimer = null;
let connected = false;
let launching = false;
let current = null; // {runId, events, timer}

function remember(value) {
  try { sessionStorage.setItem("harness-operator-token", value); } catch { /* storage may be blocked */ }
}
function recall() {
  try { return sessionStorage.getItem("harness-operator-token") ?? ""; } catch { return ""; }
}

async function refreshList() {
  const payload = await api(token, "/api/v1/runs?limit=50");
  renderRecent($("list"), payload, openRun);
  renderOverview($("overview"), payload, liveProvidersPayload.items);
}

async function paint() {
  const selected = current;
  const runId = selected.runId;
  const [run, metrics, holds, assurance] = await Promise.all([
    api(token, "/api/v1/runs/" + encodeURIComponent(runId)),
    api(token, "/api/v1/metrics/" + encodeURIComponent(runId)),
    api(token, "/api/v1/holds-register?state=all&run_id=" + encodeURIComponent(runId)),
    api(token, "/api/v1/runs/" + encodeURIComponent(runId) + "/assurance"),
  ]);
  const after = selected.events.length ? selected.events[selected.events.length - 1].sequence : 0;
  const incoming = await fetchEvents(token, runId, after);
  if (current !== selected) return true;
  selected.events = mergeEvents(selected.events, incoming);
  workspace.update(run, selected.events, metrics, holds, assurance);
  $("connection").textContent = TERMINAL.has(run.state) ? "Connected · Run finished. Review its assurance verdict below." : "Connected · Updating this run every two seconds.";
  return TERMINAL.has(run.state);
}

async function cancelRun(runId) {
  error.textContent = "";
  try {
    await api(token, "/api/v1/runs/" + encodeURIComponent(runId) + "/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "operator_cancelled" }),
    });
    await openRun(runId);
  } catch (e) {
    error.textContent = "Could not cancel: " + e.message;
  }
}

async function openRun(runId) {
  const url = new URL(location.href);
  url.searchParams.set("run", runId);
  history.replaceState(null, "", url);
  error.textContent = "";
  if (current?.timer) clearTimeout(current.timer);
  current = { runId, events: [], timer: null };
  workspace = createWorkspace($("detail"), cancelRun);
  addEvidenceAction(runId);
  $("detail").scrollIntoView({behavior:"smooth", block:"start"});
  const mine = current;
  const tick = async () => {
    try {
      const done = await paint();
      if (!done && current === mine) mine.timer = setTimeout(tick, 2000);
      else if (current === mine) await refreshList();
    } catch (e) {
      if (current !== mine) return;
      error.textContent = "Could not update run: " + e.message + ". Retrying in 5 seconds; use Refresh runs / reconnect to retry now.";
      $("connection").textContent = "Connection interrupted · Showing the last loaded results.";
      mine.timer = setTimeout(tick, 5000);
    }
  };
  await tick();
}

function syncProvider() {
  const option = options[Number($("scenario").value)];
  $("provider-field").hidden = !option?.live;
  const available = providers.some((p) => !p.disabled);
  $("launch-button").disabled = launching || !option || (option.live && !available);
  $("readiness").textContent = option?.live
    ? available ? "Live evaluation · Uses the selected server configuration. Provider usage may incur charges."
      : "Live evaluation unavailable. Configure provider credentials, independent ASR and caller voice synthesis on the server, then reconnect. Scripted fixtures remain available."
    : "Scripted fixture · No live provider calls. Checks enforcement mechanics rather than voice quality.";
}

async function connect() {
  error.textContent = "";
  try {
    options = launchOptions(await api(token, "/api/v1/scenarios"));
    const select = $("scenario");
    select.replaceChildren(...options.map((o, i) => Object.assign(el("option", "", o.label + (o.summary ? " - " + o.summary : "")), { value: String(i) })));
    const scripted = options.findIndex((o) => !o.live);
    if (scripted >= 0) select.value = String(scripted); // start on a fixture that needs no provider setup
    const liveProviders = await api(token, "/api/v1/live-providers");
    liveProvidersPayload = liveProviders;
    providers = providerChoices(liveProviders);
    renderReadiness($("checklist"), liveProviders);
    $("provider").replaceChildren(...providers.map((p) => Object.assign(el("option", "", p.label), { value: p.id, disabled: p.disabled })));
    const firstAvailable = providers.find((p) => !p.disabled);
    if (firstAvailable) $("provider").value = firstAvailable.id;
    syncProvider();
    $("launch").hidden = false;
    remember(token);
    connected = true;
    $("help").querySelector("details").open = false;
    $("connection").textContent = "Connected · Select a scenario to launch or inspect a recent run.";
    await refreshList();
    clearTimeout(overviewTimer);
    const refreshOverview = async () => {
      try { await refreshList(); } catch (e) { error.textContent = "Overview update interrupted: " + friendlyError(e); }
      if (connected) overviewTimer = setTimeout(refreshOverview, 5000);
    };
    overviewTimer = setTimeout(refreshOverview, 5000);
    const wanted = new URLSearchParams(location.search).get("run");
    if (wanted) await openRun(wanted);
  } catch (e) {
    connected = false;
    clearTimeout(overviewTimer);
    $("connection").textContent = "Not connected · Check your token and try again.";
    $("launch").hidden = true;
    error.textContent = "Could not connect: " + friendlyError(e);
  }
}

$("auth").addEventListener("submit", (event) => {
  event.preventDefault();
  token = $("token").value;
  connect();
});
$("scenario").addEventListener("change", syncProvider);
$("refresh").addEventListener("click", () => connect());
$("launch").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (launching || !connected) return;
  launching = true;
  syncProvider();
  error.textContent = "";
  try {
    const option = options[Number($("scenario").value)];
    const choice = providers.find((p) => p.id === $("provider").value);
    if (option.live && (!choice || choice.disabled)) throw new Error("no live provider is configured on the server");
    const body = launchBody(option, "strict-v1", option.live ? choice.id : null);
    body.fixture_seed = Number($("seed").value);
    body.retain_audio = $("retain").checked;
    const key = "ui-" + Date.now() + "-" + Math.random().toString(36).slice(2);
    const launched = await api(token, "/api/v1/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": key },
      body: JSON.stringify(body),
    });
    await refreshList();
    $("launch").closest("details").open = false;
    await openRun(launched.run_ids[0]);
  } catch (e) {
    error.textContent = "Could not launch: " + e.message;
  } finally { launching = false; syncProvider(); }
});

const saved = recall();
renderGetStarted($("help"), !saved);
if (saved) {
  $("token").value = saved;
  token = saved;
  connect();
}


function addEvidenceAction(runId) {
  const actions = el("div", "actions");
  const download = el("button", "", "Download evidence bundle");
  download.type = "button";
  download.addEventListener("click", async () => {
    download.disabled = true;
    error.textContent = "";
    try {
      const bundle = await api(token, "/api/v1/runs/" + encodeURIComponent(runId) + "/evidence");
      const url = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], {type: "application/json"}));
      const link = el("a");
      link.href = url;
      link.download = runId + "-evidence.json";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { error.textContent = "Could not export evidence: " + e.message; }
    finally { download.disabled = false; }
  });
  actions.append(download);
  $("detail").prepend(actions);
}
