import {setConnected} from "./chrome.mjs?v=ux-audit-20261018";
import {callerRecordings} from "./caller-recordings.mjs";
import { renderCost } from "./cost-view.mjs?v=ux-audit-20261018";
import { runActions } from "./workflow.mjs?v=ux-audit-20261018";
import { createWorkspace, renderOverview, renderRecent } from "./live-view.mjs?v=ux-audit-20261018";
import { friendlyError, renderGetStarted, renderReadiness } from "./onboarding.mjs?v=ux-audit-20261018";
import {
  api, fetchEvents, groupedOptions, launchBody, launchOptions, mergeEvents, providerChoices, TERMINAL, el,
} from "./runs.mjs?v=ux-audit-20261018";

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

function callerConfig() {
  return {mode: $("caller-mode").value, fixed_backend: $("fixed-voice").value,
    persona: $("caller-persona").value, voice: $("caller-voice").value,
    max_turns: Number($("caller-turns").value), max_duration_seconds: Number($("caller-duration").value)};
}

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
  const [run, metrics, holds, assurance, cost] = await Promise.all([
    api(token, "/api/v1/runs/" + encodeURIComponent(runId)),
    api(token, "/api/v1/metrics/" + encodeURIComponent(runId)),
    api(token, "/api/v1/holds-register?state=all&run_id=" + encodeURIComponent(runId)),
    api(token, "/api/v1/runs/" + encodeURIComponent(runId) + "/assurance"),
    api(token, "/api/v1/runs/" + encodeURIComponent(runId) + "/cost").catch(e=>({unavailable_reason:e.message})),
  ]);
  const after = selected.events.length ? selected.events[selected.events.length - 1].sequence : 0;
  const incoming = await fetchEvents(token, runId, after);
  if (current !== selected) return true;
  selected.events = mergeEvents(selected.events, incoming);
  selected.run=run;
  selected.recordingsRoot.hidden = run.config?.caller?.mode !== "live_ai";
  renderCost(selected.costRoot,cost);
  workspace.update(run, selected.events, metrics, holds, assurance, options.find(option=>option.id===run.scenario?.scenario_id)?.title);
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
  current?.disposeRecordings?.();
  current = { runId, events: [], timer: null };
  workspace = createWorkspace($("detail"), cancelRun);
  runActions($("detail"), {token,runId,getData:()=>current?.runId===runId&&current.run?{run:current.run,events:current.events}:null,onOpen:openRun,onRefresh:paint});
  $("detail").scrollIntoView({behavior:"smooth", block:"start"});
  current.costRoot=el("section","panel");
  $("detail").append(current.costRoot);
  const recordings = el("section", "panel");
  recordings.hidden = true;
  current.recordingsRoot = recordings;
  $("detail").append(recordings);
  current.disposeRecordings = callerRecordings(recordings, token, runId);
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
  const caller = callerConfig();
  const selectedProvider = $("provider").value;
  providers = providerChoices(liveProvidersPayload, caller);
  $("provider").replaceChildren(...providers.map((p) => Object.assign(el("option", "", p.label), {value: p.id, disabled: p.disabled})));
  $("provider").value = providers.find(p => p.id === selectedProvider && !p.disabled)?.id ?? providers.find(p => !p.disabled)?.id ?? "";
  $("provider-field").hidden = !option?.live;
  $("caller-fields").hidden = !option?.live;
  $("interactive-fields").hidden = caller.mode !== "live_ai";
  $("fixed-voice-field").hidden = caller.mode !== "fixed";
  const supported = caller.mode !== "live_ai" || (liveProvidersPayload.interactive_caller?.scenario_ids ?? []).includes(option?.id);
  $("caller-support").textContent = !supported ? "Live AI caller is available for Balance after verification · clean studio. Choose that scenario or use a fixed caller." : "";
  const available = providers.some((p) => !p.disabled);
  $("launch-button").disabled = launching || !option || (option.live && (!available || !supported));
  $("readiness").textContent = option?.live
    ? available ? "Live evaluation · Uses the selected server configuration. Provider usage may incur charges."
      : "Live evaluation unavailable. Configure provider credentials, independent ASR and caller voice synthesis on the server, then reconnect. Scripted fixtures remain available."
    : "Scripted fixture · No live provider calls. Checks enforcement mechanics rather than voice quality.";
}

function renderExpectation() {
  const option = options[Number($("scenario").value)];
  const box = $("expectation");
  if (!option) { box.replaceChildren(); return; }
  box.replaceChildren(
    el("strong", "", option.title),
    el("p", "", option.expected ?? "No expected-outcome note is recorded for this scenario."),
    el("p", "muted", "Goal: " + option.summary + (option.tools.length ? " · Expected tools: " + option.tools.join(" → ") : " · No tool calls expected")
      + (option.criteria.length ? " · Assessed on: " + option.criteria.join(", ") : "")),
  );
}

async function connect() {
  error.textContent = "";
  try {
    options = launchOptions(await api(token, "/api/v1/scenarios"));
    const select = $("scenario");
    select.replaceChildren(...groupedOptions(options).map(({ group, items }) => {
      const optgroup = Object.assign(document.createElement("optgroup"), { label: group + " (" + items.length + ")" });
      optgroup.append(...items.map(({ option, index }) => Object.assign(el("option", "", option.title + (option.live ? "" : " · scripted")), { value: String(index) })));
      return optgroup;
    }));
    const scripted = options.findIndex((o) => !o.live);
    if (scripted >= 0) select.value = String(scripted); // start on a fixture that needs no provider setup
    const liveProviders = await api(token, "/api/v1/live-providers");
    liveProvidersPayload = liveProviders;
    providers = providerChoices(liveProviders);
    renderReadiness($("checklist"), liveProviders);
    $("provider").replaceChildren(...providers.map((p) => Object.assign(el("option", "", p.label), { value: p.id, disabled: p.disabled })));
    const firstAvailable = providers.find((p) => !p.disabled);
    if (firstAvailable) $("provider").value = firstAvailable.id;
    const requestedScenario = new URLSearchParams(location.search).get("scenario");
    const requestedIndex = options.findIndex(option => option.id === requestedScenario);
    if (requestedIndex >= 0) {
      select.value = String(requestedIndex);
      $("launch").closest("details").open = true;
    }
    syncProvider();
    renderExpectation();
    $("launch").hidden = false;
    remember(token);
    connected = true;
    setConnected(true);
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
    setConnected(false);
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
$("scenario").addEventListener("change", () => { syncProvider(); renderExpectation(); });
$("caller-mode").addEventListener("change", syncProvider);
$("fixed-voice").addEventListener("change", syncProvider);
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
    const body = launchBody(option, "strict-v1", option.live ? choice.id : null, callerConfig());
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
