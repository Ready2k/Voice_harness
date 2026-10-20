import {setConnected} from "./chrome.mjs?v=ux-final-20261023";
import {callerRecordings} from "./caller-recordings.mjs";
import { humanise } from "./format.mjs?v=ux-final-20261023";
import { providerName } from "./providers.mjs?v=ux-final-20261023";
import { renderCost } from "./cost-view.mjs?v=ux-final-20261023";
import { runActions } from "./workflow.mjs?v=ux-final-20261023";
import { createWorkspace, renderOverview, renderRunsTable } from "./live-view.mjs?v=ux-final-20261023";
import { friendlyError, renderGetStarted, renderReadiness } from "./onboarding.mjs?v=ux-final-20261023";
import {
  api, CRITERION_HELP, fetchEvents, filterRows, groupedOptions, launchBody, launchOptions, mergeEvents, providerChoices, runRows, sortRows, TERMINAL, el,
} from "./runs.mjs?v=ux-final-20261023";

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
let rows = [];
let titles = new Map(); // scenario id -> display title
let families = new Map(); // scenario id -> scenario family (presentation group)
let card = null; // active summary-card filter
const PAGE_SIZE = 50;
const AUDIO_LABEL = {clean_studio: "Clean studio", street_noise_10db: "Street noise", telephone_narrowband: "Telephone line", telephone_noisy: "Noisy telephone line", fixed_silence_fixture: "Scripted silence (no real audio)"};
const audioLabel = (v) => AUDIO_LABEL[v] ?? humanise(v);

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

function fillSelect(select, values, labelFor = (v) => v) {
  const chosen = select.value;
  const first = select.options[0];
  select.replaceChildren(first, ...values.map((v) => Object.assign(el("option", "", labelFor(v)), {value: v})));
  select.value = values.includes(chosen) ? chosen : "";
}

function currentFilters() {
  return {search: $("f-search").value, provider: $("f-provider").value, family: $("f-family").value, state: $("f-state").value,
    result: $("f-result").value, since: $("f-since").value, card, titles, families};
}

function renderRuns() {
  fillSelect($("f-provider"), [...new Set(rows.map((r) => r.provider).filter(Boolean))].sort(), providerName);
  const visible = sortRows(filterRows(rows, currentFilters()), $("f-sort").value);
  renderOverview($("overview"), rows, card, (id) => { card = id; renderRuns(); });
  renderRunsTable($("list"), visible, {onOpen: openRun, selectedId: current?.runId ?? null, titles});
  $("runs-count").textContent = visible.length === rows.length ? (nextCursor == null ? "" : "latest ") + rows.length : visible.length + " of " + rows.length + " shown";
  $("load-more").hidden = nextCursor == null;
}

let nextCursor = null; // offset of the next page of older runs, or null when everything is loaded
let olderRows = []; // pages added by "Load more"; kept across the periodic refresh of the newest page
async function refreshList() {
  const page = await api(token, "/api/v1/runs?limit=" + PAGE_SIZE);
  const first = runRows(page);
  const seen = new Set(first.map((r) => r.runId));
  olderRows = olderRows.filter((r) => !seen.has(r.runId));
  rows = [...first, ...olderRows];
  if (!olderRows.length) nextCursor = page.next_cursor ?? null;
  renderRuns();
}
async function loadMore() {
  if (nextCursor == null) return;
  const button = $("load-more");
  button.disabled = true;
  try {
    const page = await api(token, "/api/v1/runs?limit=" + PAGE_SIZE + "&cursor=" + encodeURIComponent(nextCursor));
    const seen = new Set(rows.map((r) => r.runId));
    olderRows = [...olderRows, ...runRows(page).filter((r) => !seen.has(r.runId))];
    nextCursor = page.next_cursor ?? null;
    rows = [...rows, ...olderRows.filter((r) => !seen.has(r.runId))];
    renderRuns();
  } catch (e) { error.textContent = "Could not load more runs: " + friendlyError(e); }
  finally { button.disabled = false; }
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
  $("connection").textContent = TERMINAL.has(run.state) ? "Connected · Run finished. Review its assurance verdict." : "Connected · Updating this run every two seconds.";
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

let returnFocus = null; // the control that opened the drawer, so closing it puts the keyboard back there
async function openRun(runId) {
  if ($("drawer").hidden) returnFocus = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null;
  const url = new URL(location.href);
  url.searchParams.set("run", runId);
  history.replaceState(null, "", url);
  error.textContent = "";
  if (current?.timer) clearTimeout(current.timer);
  current?.disposeRecordings?.();
  current = { runId, events: [], timer: null };
  showDrawer(true);
  renderRuns();
  workspace = createWorkspace($("detail"), cancelRun);
  runActions($("detail"), {token,runId,getData:()=>current?.runId===runId&&current.run?{run:current.run,events:current.events}:null,onOpen:openRun,onRefresh:paint});
  $("drawer").scrollTop = 0;
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
      error.textContent = "Could not update run: " + e.message + ". Retrying in 5 seconds.";
      $("connection").textContent = "Connection interrupted · Showing the last loaded results.";
      mine.timer = setTimeout(tick, 5000);
    }
  };
  await tick();
}

function showDrawer(open) {
  $("drawer").hidden = !open;
  $("drawer-backdrop").hidden = !open;
  document.body.classList.toggle("drawer-open", open);
  if (open) $("drawer-close").focus({preventScroll: true});
}

function closeRun() {
  const closedId = current?.runId;
  if (current?.timer) clearTimeout(current.timer);
  current?.disposeRecordings?.();
  current = null;
  workspace = null;
  const url = new URL(location.href);
  url.searchParams.delete("run");
  history.replaceState(null, "", url);
  const opener = returnFocus; returnFocus = null;
  showDrawer(false);
  renderRuns();
  const row = closedId ? $("list").querySelector('[data-run-id="' + CSS.escape(closedId) + '"]') : null;
  (row ?? (opener?.isConnected ? opener : null))?.focus({preventScroll: true});
}

function syncProvider() {
  const option = options[Number($("scenario").value)];
  const caller = callerConfig();
  const selectedProvider = $("provider").value;
  providers = providerChoices(liveProvidersPayload, caller);
  $("provider").replaceChildren(...providers.map((p) => Object.assign(el("option", "", p.label), {value: p.id, disabled: p.disabled})));
  $("provider").value = providers.find(p => p.id === selectedProvider && !p.disabled)?.id ?? providers.find(p => !p.disabled)?.id ?? "";
  $("provider-field").hidden = !option?.live;
  $("prompt-field").hidden = !option?.live;
  $("readiness-details").hidden = !option?.live; // provider readiness is irrelevant to a scripted fixture
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

function chips(values, helpFor = () => "") {
  const list = el("span", "chips");
  values.forEach((v, i) => {
    if (i && helpFor === null) list.append(el("span", "arrow", "\u2192"));
    const chip = el("span", "chip-tag", v);
    const help = helpFor ? helpFor(v) : "";
    if (help) { chip.title = help; chip.tabIndex = 0; }
    list.append(chip);
  });
  return list;
}

function renderExpectation() {
  const option = options[Number($("scenario").value)];
  const box = $("expectation");
  if (!option) { box.replaceChildren(); return; }
  const dl = el("dl", "config scenario-details");
  dl.append(el("dt", "", "Expected outcome"), el("dd", "", option.expected ?? "No expected-outcome note is recorded for this scenario."));
  dl.append(el("dt", "", "Goal"), el("dd", "", option.summary || "Not recorded"));
  const tools = el("dd"); if (option.tools.length) tools.append(chips(option.tools, null)); else tools.textContent = "No tool calls expected";
  dl.append(el("dt", "", "Expected tools"), tools);
  const criteria = el("dd"); if (option.criteria.length) criteria.append(chips(option.criteria, (code) => CRITERION_HELP[code] ?? "")); else criteria.textContent = "None declared";
  dl.append(el("dt", "", "Assessed on"), criteria);
  if (option.locale || option.audio) dl.append(el("dt", "", "Locale · audio"), el("dd", "", [option.locale, audioLabel(option.audio)].filter(Boolean).join(" · ")));
  box.replaceChildren(el("strong", "", option.title), dl);
}

function populateScenarios() {
  const locale = $("f-locale").value, audio = $("f-audio").value;
  const select = $("scenario");
  const keep = select.value;
  const matching = options.map((option, index) => ({option, index})).filter(({option}) => (!locale || option.locale === locale) && (!audio || option.audio === audio));
  const wanted = new Set(matching.map(({index}) => index));
  select.replaceChildren(...groupedOptions(options).map(({group, items}) => {
    const shown = items.filter(({index}) => wanted.has(index));
    if (!shown.length) return null;
    const optgroup = Object.assign(document.createElement("optgroup"), {label: group + " (" + shown.length + ")"});
    optgroup.append(...shown.map(({option, index}) => Object.assign(el("option", "", option.title + (option.live ? "" : " \u00b7 scripted")), {value: String(index)})));
    return optgroup;
  }).filter(Boolean));
  if (keep !== "" && wanted.has(Number(keep))) select.value = keep;
  else {
    const scripted = matching.find(({option}) => !option.live) ?? matching[0];
    if (scripted) select.value = String(scripted.index); // start on a fixture that needs no provider setup
  }
}

async function connect() {
  error.textContent = "";
  try {
    options = launchOptions(await api(token, "/api/v1/scenarios"));
    titles = new Map(options.map((o) => [o.id, o.title]));
    families = new Map(options.map((o) => [o.id, o.group]));
    const sorted = (values) => [...new Set(values.filter(Boolean))].sort();
    fillSelect($("f-family"), groupedOptions(options).map((g) => g.group));
    fillSelect($("f-locale"), sorted(options.map((o) => o.locale)));
    fillSelect($("f-audio"), sorted(options.map((o) => o.audio)), audioLabel);
    const select = $("scenario");
    populateScenarios();
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
      openLaunch();
    }
    syncProvider();
    renderExpectation();
    $("launch").hidden = false;
    remember(token);
    connected = true;
    setConnected(true);
    $("help").hidden = true;
    $("intro").hidden = true;
    $("workspace").hidden = false;
    $("new-eval").hidden = false;
    $("connection").textContent = "Connected · Select a run to inspect it, or start a new evaluation.";
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
    $("workspace").hidden = true;
    $("new-eval").hidden = true;
    $("help").hidden = false;
    $("intro").hidden = false;
    error.textContent = "Could not connect: " + friendlyError(e);
  }
}

$("auth").addEventListener("submit", (event) => {
  event.preventDefault();
  token = $("token").value;
  connect();
});
function openLaunch() {
  const dialog = $("launch-dialog");
  if (!dialog.open) dialog.showModal();
}
function closeLaunch() { $("launch-dialog").close(); }

$("scenario").addEventListener("change", () => { syncProvider(); renderExpectation(); });
for (const id of ["f-locale", "f-audio"]) $(id).addEventListener("change", () => { populateScenarios(); syncProvider(); renderExpectation(); });
for (const id of ["f-family", "f-provider", "f-result", "f-state", "f-since", "f-sort"]) $(id).addEventListener("change", renderRuns);
$("f-search").addEventListener("input", renderRuns);
$("f-clear").addEventListener("click", () => {
  for (const id of ["f-search", "f-family", "f-provider", "f-result", "f-state", "f-since"]) $(id).value = "";
  $("f-sort").value = "newest";
  card = null;
  renderRuns();
});
$("new-eval").addEventListener("click", openLaunch);
$("launch-close").addEventListener("click", closeLaunch);
$("drawer-close").addEventListener("click", closeRun);
$("drawer-backdrop").addEventListener("click", closeRun);
document.addEventListener("keydown", (event) => { // keep Tab inside the open run drawer
  if (event.key !== "Tab" || $("drawer").hidden || $("launch-dialog").open) return;
  const items = [...$("drawer").querySelectorAll("a[href],button,input,select,textarea,summary,[tabindex]:not([tabindex='-1'])")].filter((n) => !n.disabled && n.getClientRects().length);
  if (!items.length) return;
  const first = items[0], last = items.at(-1);
  if (!$("drawer").contains(document.activeElement)) { event.preventDefault(); first.focus(); }
  else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("drawer").hidden && !$("launch-dialog").open) closeRun();
});
$("caller-mode").addEventListener("change", syncProvider);
$("fixed-voice").addEventListener("change", syncProvider);
$("load-more").addEventListener("click", loadMore);
$("refresh").addEventListener("click", async () => {
  error.textContent = "";
  try { await refreshList(); } catch { connect(); } // a failed refresh falls back to a full reconnect
});
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
    if (option.live) body.prompt_profile = $("prompt-profile").value;
    body.fixture_seed = Number($("seed").value);
    body.retain_audio = $("retain").checked;
    const key = "ui-" + Date.now() + "-" + Math.random().toString(36).slice(2);
    const launched = await api(token, "/api/v1/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": key },
      body: JSON.stringify(body),
    });
    await refreshList();
    closeLaunch();
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
