import {
  api, fetchEvents, launchBody, launchOptions, mergeEvents, renderRun, renderRunList, runRows, runView, TERMINAL, el,
} from "./runs.mjs";

const $ = (id) => document.getElementById(id);
const error = $("error");
let token = "";
let options = [];
let current = null; // {runId, events, timer}

function remember(value) {
  try { sessionStorage.setItem("harness-operator-token", value); } catch { /* storage may be blocked */ }
}
function recall() {
  try { return sessionStorage.getItem("harness-operator-token") ?? ""; } catch { return ""; }
}

async function refreshList() {
  renderRunList($("list"), runRows(await api(token, "/api/v1/runs?limit=50")), openRun);
}

async function paint() {
  const [run, metrics, holds] = await Promise.all([
    api(token, "/api/v1/runs/" + encodeURIComponent(current.runId)),
    api(token, "/api/v1/metrics/" + encodeURIComponent(current.runId)),
    api(token, "/api/v1/holds-register?state=all&run_id=" + encodeURIComponent(current.runId)),
  ]);
  const after = current.events.length ? current.events[current.events.length - 1].sequence : 0;
  current.events = mergeEvents(current.events, await fetchEvents(token, current.runId, after));
  renderRun($("detail"), runView(run, current.events, metrics, holds));
  return TERMINAL.has(run.state);
}

async function openRun(runId) {
  if (current?.timer) clearTimeout(current.timer);
  current = { runId, events: [], timer: null };
  const mine = current;
  const tick = async () => {
    try {
      const done = await paint();
      if (!done && current === mine) mine.timer = setTimeout(tick, 2000);
      else await refreshList();
    } catch (e) {
      error.textContent = "Could not load run: " + e.message;
    }
  };
  await tick();
}

async function connect() {
  error.textContent = "";
  try {
    options = launchOptions(await api(token, "/api/v1/scenarios"));
    const select = $("scenario");
    select.replaceChildren(...options.map((o, i) => Object.assign(el("option", "", o.label + (o.summary ? " - " + o.summary : "")), { value: String(i) })));
    $("launch").hidden = false;
    remember(token);
    await refreshList();
  } catch (e) {
    $("launch").hidden = true;
    error.textContent = "Could not connect: " + e.message;
  }
}

$("auth").addEventListener("submit", (event) => {
  event.preventDefault();
  token = $("token").value;
  connect();
});
$("refresh").addEventListener("click", () => refreshList().catch((e) => { error.textContent = e.message; }));
$("launch").addEventListener("submit", async (event) => {
  event.preventDefault();
  error.textContent = "";
  try {
    const body = launchBody(options[Number($("scenario").value)]);
    const key = "ui-" + Date.now() + "-" + Math.random().toString(36).slice(2);
    const launched = await api(token, "/api/v1/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": key },
      body: JSON.stringify(body),
    });
    await refreshList();
    await openRun(launched.run_ids[0]);
  } catch (e) {
    error.textContent = "Could not launch: " + e.message;
  }
});

const saved = recall();
if (saved) {
  $("token").value = saved;
  token = saved;
  connect();
}
const wanted = new URLSearchParams(location.search).get("run");
if (saved && wanted) openRun(wanted);
