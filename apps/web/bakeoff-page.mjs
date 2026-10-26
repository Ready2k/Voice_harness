import {setConnected} from './chrome.mjs?v=ux-final-20261025';
import {api, el} from './runs.mjs?v=ux-final-20261025';
import {friendlyError} from './onboarding.mjs?v=ux-final-20261025';
import {MAX_PLAN_RUNS, defaultRepetitions, estimateCost, failingRun, failureText, ignoredWeightsText, knownSpend, launchConfirmText, normaliseWeights, observationsCsv, planSize, providerProgress, renderReport, scenarioGroups, usd, usdEstimate, joinParts, weightPercents, weightsAreCustom, weightsTotal, words} from './bakeoff-report.mjs?v=bakeoff-ux-20261022-2';

const $ = id => document.getElementById(id);
// Older cached HTML predates this control. Keep it compatible with this module.
if (!$('bo-resume')) {
  const button = el('button', '', 'Recheck providers and resume');
  button.id = 'bo-resume'; button.type = 'button'; button.hidden = true;
  $('bo-download').after(button);
}
// Cached HTML from before these controls existed: create them so this module still works.
for (const [id, label] of [['bo-clone', 'Clone this bake-off'], ['bo-csv', 'Download observations (CSV)'], ['bo-json', 'Download observations (JSON)']]) {
  if ($(id)) continue;
  const button = el('button', '', label); button.id = id; button.type = 'button'; $('bo-download').after(button);
}
if (!$('bo-ignored-weights')) { const note = el('p', 'muted'); note.id = 'bo-ignored-weights'; note.hidden = true; $('bo-weight-inputs').before(note); }
if (!$('bo-provider-progress')) { const box = el('div', 'bakeoff-provider-progress'); box.id = 'bo-provider-progress'; $('bo-progress').after(box); }
if (!$('bo-pause')) { const box = el('div', 'bakeoff-pause'); box.id = 'bo-pause'; box.hidden = true; box.setAttribute('role', 'status'); $('bo-progress').after(box); }
if (!$('bo-action-confirm')) {
  const box = el('div', 'bakeoff-confirm'), text = el('p'), actions = el('div', 'actions'), yes = el('button', 'primary'), no = el('button', '', 'Keep going');
  box.id = 'bo-action-confirm'; box.hidden = true; box.setAttribute('role', 'alertdialog'); box.setAttribute('aria-labelledby', 'bo-action-text');
  text.id = 'bo-action-text'; yes.id = 'bo-action-yes'; no.id = 'bo-action-no'; yes.type = no.type = 'button';
  actions.append(yes, no); box.append(text, actions); $('bo-progress').after(box);
}
import {providerName} from './providers.mjs?v=ux-final-20261025';
import {revealResult} from './nav.mjs?v=ux-final-20261025';
import {runLink} from './format.mjs?v=ux-final-20261025';
let token = '', groups = [], current = null, view = null, poll = null, whatIf = null, baseData = null, stored = {}, costHistory = null, recomputeTimer = null, seq = 0;
let lastBody = null, drawn = null, repsTouched = false, pendingAction = null;

const post = (path, body) => api(token, path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
const report = e => { $('error').textContent = friendlyError(e); };

// ---------------------------------------------------------------- launch form
async function loadForm() {
  const [providers, scenarios] = await Promise.all([api(token, '/api/v1/live-providers'), api(token, '/api/v1/scenarios?limit=1000')]);
  $('bo-providers').replaceChildren(...providers.items.map(p => {
    const label = el('label', 'check'), input = el('input');
    input.type = 'checkbox'; input.value = p.provider_config_id; input.disabled = !p.available; input.checked = p.available;
    input.addEventListener('change', size);
    label.append(input, providerName(p.provider_config_id) + (p.available ? '' : ' · unavailable: ' + words(p.reason)));
    return label;
  }));
  groups = scenarioGroups(scenarios.items);
  $('bo-groups').replaceChildren(...groups.map((g, i) => {
    const wrap = el('div', 'bakeoff-group'), label = el('label', 'check'), input = el('input');
    input.type = 'checkbox'; input.value = String(i); input.checked = true; input.dataset.group = String(i);
    label.append(input, `${g.group} (${g.ids.length})`);
    const details = el('details'), summary = el('summary', 'muted'), list = el('div', 'bakeoff-checks bakeoff-scenarios');
    summary.id = 'bo-group-count-' + i;
    for (const item of g.items) {
      const l = el('label', 'check'), box = el('input');
      box.type = 'checkbox'; box.value = item.id; box.checked = true; box.dataset.scenario = item.id; box.dataset.group = String(i);
      box.addEventListener('change', () => { syncGroup(i); size(); });
      l.append(box, item.title);
      list.append(l);
    }
    input.addEventListener('change', () => { for (const box of list.querySelectorAll('input')) box.checked = input.checked; syncGroup(i); size(); });
    details.append(summary, list); wrap.append(label, details);
    syncGroup(i, wrap);
    return wrap;
  }));
  $('bo-reps').addEventListener('input', () => { repsTouched = true; size(); });
  size();
}
/** Group checkbox follows its scenarios (checked, partly checked, unchecked) and the count line says how many are chosen. */
function syncGroup(i, scope = document) {
  const boxes = [...scope.querySelectorAll(`input[data-scenario][data-group="${i}"]`)], on = boxes.filter(b => b.checked).length;
  const group = scope.querySelector(`input[data-group="${i}"]:not([data-scenario])`);
  group.checked = on === boxes.length; group.indeterminate = on > 0 && on < boxes.length;
  scope.querySelector('#bo-group-count-' + i).textContent = `${on} of ${boxes.length} scenarios · choose individually`;
}
const chosenProviders = () => [...$('bo-providers').querySelectorAll('input:checked')].map(i => i.value);
const chosenScenarios = () => [...$('bo-groups').querySelectorAll('input[data-scenario]:checked')].map(i => i.dataset.scenario);
const repsValue = () => Math.max(1, Math.min(10, Number($('bo-reps').value) || 1));
/** Run count and a cost estimate from the last bake-off's recorded per-run medians (never a made-up figure). */
function plan() {
  const providers = chosenProviders(), scenarios = chosenScenarios(), reps = repsValue();
  const runs = planSize(scenarios.length, providers.length, reps);
  let estimate = null;
  if (costHistory && providers.length) {
    const e = estimateCost(costHistory.costs, providers, scenarios.length * reps);
    estimate = {...e, text: e.missing.length ? null : (usdEstimate(e.usd).startsWith('<') ? '' : '~') + usdEstimate(e.usd)};
  }
  return {providers, scenarios, reps, runs, estimate};
}
function size() {
  let cappedNote = '';
  if (!repsTouched) {  // until the operator chooses, default to 5 repetitions, fewer if 5 would pass the run limit
    const {reps: fit, capped} = defaultRepetitions(chosenScenarios().length, chosenProviders().length);
    $('bo-reps').value = String(fit);
    if (capped) cappedNote = ` Repetitions are limited to ${fit} so the plan stays within the ${MAX_PLAN_RUNS}-run limit.`;
  }
  const {providers, scenarios, reps, runs, estimate} = plan();
  $('bo-confirm').hidden = true;
  $('bo-size').textContent = `${runs} scored live runs plus ${providers.length} provider checks (${scenarios.length} scenarios × ${providers.length} providers × ${reps}).`
    + (reps < 3 ? ' Use 5 or more repetitions to separate close providers.' : '') + cappedNote
    + (estimate?.text ? ` Estimated scored-run cost ${estimate.text}, from the median per-run voice cost recorded in "${costHistory.name}".` : ' No recorded per-run cost for every chosen provider, so no estimate.');
  $('bo-launch').textContent = `Launch bake-off · ${runs} runs` + (estimate?.text ? ' · ' + estimate.text : '');
  $('bo-launch').disabled = providers.length < 2 || !scenarios.length || runs > MAX_PLAN_RUNS;
  if (runs > MAX_PLAN_RUNS) $('bo-size').textContent += ` That is over the ${MAX_PLAN_RUNS}-run limit for one bake-off.`;
}
function askToLaunch() {
  const {providers, scenarios, reps, runs, estimate} = plan();
  const single = reps > 1 && costHistory && estimate?.text ? estimateCost(costHistory.costs, providers, scenarios.length) : null;
  $('bo-confirm-text').textContent = launchConfirmText({providerNames: providers.map(providerName), scenarios: scenarios.length, reps, runs, estimate: estimate?.text || null,
    singleEstimate: single && !single.missing.length ? usdEstimate(single.usd) : null});
  $('bo-confirm').hidden = false;
  $('bo-confirm-yes').focus();
}
async function loadCostHistory() {
  try {
    const {items} = await api(token, '/api/v1/bakeoffs');
    for (const b of items.slice(0, 5)) {
      const body = await api(token, '/api/v1/bakeoffs/' + encodeURIComponent(b.bakeoff_id));
      const costs = body.data?.report?.cost;
      if (costs?.some(c => c.median_usd != null)) { costHistory = {costs, name: b.name}; break; }
    }
  } catch { /* no history: the button shows the run count only */ }
  size();
}
async function launch() {
  $('bo-launch').disabled = true; $('bo-confirm').hidden = true;
  try {
    const all = groups.flatMap(g => g.ids), scenarios = chosenScenarios();
    const created = await post('/api/v1/bakeoffs', {
      name: $('bo-name').value.trim() || 'Bake-off ' + new Date().toISOString().slice(0, 16).replace('T', ' '),
      provider_config_ids: chosenProviders(), scenario_ids: scenarios.length === all.length ? [] : scenarios,
      repetitions: repsValue(), prompt_profile: $('bo-prompt-profile').value,
    });
    $('new-drawer').open = false;
    await loadList();
    await open(created.bakeoff_id);
  } catch (e) { report(e); } finally { size(); }
}

// ---------------------------------------------------------------- list and detail
async function loadList() {
  const {items} = await api(token, '/api/v1/bakeoffs');
  const root = $('bo-list');
  root.replaceChildren();
  if (!items.length) root.append(el('p', 'muted', 'None yet. Launch one above.'));
  for (const b of items) {
    const button = el('button', 'queue-item');
    button.type = 'button';
    button.setAttribute('aria-current', String(current === b.bakeoff_id));
    const meta = joinParts([words(b.state), `${b.cells_done}/${b.cells} runs`]);
    if (b.name?.trim()) button.append(el('strong', '', b.name), el('span', 'muted', ' · ' + meta)); else button.append(el('span', 'muted', meta));
    button.addEventListener('click', () => open(b.bakeoff_id));
    root.append(button);
  }
}

async function open(id) {
  current = id; whatIf = null; setSide(false); hideAction();
  const url = new URL(location.href); url.searchParams.set('bakeoff', id); history.replaceState(null, '', url);
  await refresh();
  await loadList();
  revealResult($('bo-view'));
}

async function refresh() {
  if (!current) return;
  clearTimeout(poll);
  const body = await api(token, '/api/v1/bakeoffs/' + encodeURIComponent(current));
  const b = body.bakeoff;
  $('bo-empty').hidden = true; $('bo-view').hidden = false;
  $('bo-title').textContent = b.name;
  $('bo-state').textContent = b.state === 'running' && b.phase === 'preflight' ? 'Checking providers' : words(b.state);
  $('bo-state').className = 'badge state-' + (b.state === 'running' ? 'running' : b.state === 'completed' ? 'completed' : 'cancelled');
  $('bo-bar').style.width = (b.cells ? b.cells_done / b.cells * 100 : 0) + '%';
  $('bo-progress').textContent = `${b.cells_done} of ${b.cells} runs settled · ${b.runs_in_flight} in flight · ${b.retries} retried · ${b.providers.map(p => providerName(p)).join(', ')}`
    + (b.mode === 'imported' ? ' · built from existing runs' : '');
  $('bo-cancel').hidden = !['running', 'paused'].includes(b.state);
  $('bo-resume').hidden = b.state !== 'paused';
  $('bo-resume').disabled = b.runs_in_flight > 0;
  lastBody = body;
  drawProgress(body);
  // The weights the report actually used (rescaled, with any weight for a dimension no longer scored dropped), not the raw stored ones:
  // an older bake-off stored a reliability weight, and comparing against that would flag every one of them as custom.
  baseData = body.data; stored = body.data.report.weights ?? body.weights;
  weightInputs(stored, body.data.report.dimensions);
  const ignored = ignoredWeightsText(body.data.report.scoring), note = $('bo-ignored-weights');
  note.textContent = ignored ?? ''; note.hidden = !ignored;
  if (customNow()) await recompute(); else draw(body.data);
  if (b.state === 'running' || (b.state === 'paused' && b.runs_in_flight > 0)) poll = setTimeout(() => refresh().then(loadList).catch(report), 5000);
}

/** Per-provider progress, known spend so far, and (when paused) the run that stopped it. */
function drawProgress(body) {
  const b = body.bakeoff, rows = providerProgress(body.cells, b.providers, body.data.report), box = $('bo-provider-progress');
  const spend = knownSpend(rows);
  const table = el('table', 'bakeoff-provider-table'), head = table.createTHead().insertRow();
  for (const h of ['Provider', 'Settled', 'In flight', 'Failed', 'Known spend']) head.append(el('th', '', h));
  const tbody = table.createTBody();
  for (const r of rows) {
    const tr = tbody.insertRow();
    for (const v of [providerName(r.provider), `${r.done} of ${r.total}`, String(r.inFlight), String(r.failed), r.spend == null ? '—' : usd(r.spend)]) tr.insertCell().textContent = v;
  }
  box.replaceChildren(table, el('p', 'muted', spend == null ? 'No spend recorded yet.' : `Known spend so far ${usd(spend)}: settled runs only, so a lower bound while runs are in flight. Provider checks are billed separately and not counted.`));
  const pause = $('bo-pause');
  pause.replaceChildren();
  pause.hidden = b.state !== 'paused' || !b.pause_reason;
  if (!pause.hidden) {
    const why = b.pause_reason, run = failingRun({pause: why, checks: body.data.report.readiness?.preflight?.checks, cells: body.cells});
    pause.append(el('strong', '', `Paused on ${providerName(why.provider)}. `), failureText(why.reason) + ' ');
    if (run) { const link = el('a', '', 'Open the failing run'); link.href = runLink(run.runId); pause.append(link); }
  }
}

function draw(data) {
  const root = $('bo-report');
  // The poll redraws every few seconds; skip an identical report, and carry the reader's place across a changed one.
  const json = JSON.stringify(data), same = drawn?.id === current, keep = same && view ? view.state() : null, y = window.scrollY;
  if (same && drawn.json === json) return;
  // Display names come from providers.mjs (hover = technical id); the server data and the export are unchanged.
  const names = Object.fromEntries(Object.keys(data.names ?? {}).map(id => [id, providerName(id)]));
  const short = Object.fromEntries(Object.keys(data.short ?? {}).map(id => [id, data.short[id]]));
  view = renderReport(root, {...data, names, short}, {heading: false, reveal: revealResult, state: keep});
  drawn = {id: current, json};
  if (keep) window.scrollTo({top: y, behavior: 'instant'});
}

function weightInputs(storedWeights, dimensions) {
  const box = $('bo-weight-inputs');
  if (box.dataset.for === current) return;  // keep what the operator typed across refreshes
  box.dataset.for = current;
  const pc = weightPercents(storedWeights);
  box.replaceChildren(...dimensions.map(d => {
    const row = el('div', 'bakeoff-weight-row'), label = el('label', '', d.label), range = el('input'), num = el('input');
    range.type = 'range'; range.min = '0'; range.max = '100'; range.step = '1'; range.value = String(pc[d.dimension]); range.setAttribute('aria-label', d.label + ' weight slider');
    num.type = 'number'; num.min = '0'; num.max = '100'; num.step = 'any'; num.value = String(pc[d.dimension]); num.dataset.dimension = d.dimension; num.setAttribute('aria-label', d.label + ' weight, percent');
    range.addEventListener('input', () => { num.value = range.value; weightsChanged(); });
    num.addEventListener('input', () => { range.value = String(Math.max(0, Math.min(100, Number(num.value) || 0))); weightsChanged(); });
    row.append(label, range, num);
    return row;
  }));
  weightsChanged(false);
}
const percents = () => Object.fromEntries([...$('bo-weight-inputs').querySelectorAll('input[type=number]')].map(i => [i.dataset.dimension, Number(i.value) || 0]));
const customNow = () => $('bo-weight-inputs').children.length > 0 && weightsAreCustom(stored, percents());
function weightsChanged(recomputeNow = true) {
  const pc = percents(), total = weightsTotal(pc), off = Math.abs(total - 100) > 0.05;
  const t = $('bo-weight-total');
  t.textContent = `Total ${Math.round(total * 10) / 10}%` + (total <= 0 ? ': set at least one weight above 0.' : off ? ': will be scaled to 100% when applied.' : '');
  t.classList.toggle('off', off);
  const custom = customNow();
  $('bo-custom').hidden = !custom;
  if (!recomputeNow) return;
  clearTimeout(recomputeTimer);
  if (!custom) { whatIf = null; if (baseData) draw(baseData); return; }
  recomputeTimer = setTimeout(() => recompute().catch(report), 250);
}

async function recompute() {
  const weights = normaliseWeights(percents());
  if (!weights) return;
  const mine = ++seq;
  try {
    const data = (await post(`/api/v1/bakeoffs/${encodeURIComponent(current)}/report`, weights)).data;
    if (mine !== seq) return;  // a newer edit superseded this one
    whatIf = data; draw(data);
  } catch (e) { report(e); }
}
function resetWeights() {
  clearTimeout(recomputeTimer); seq++;
  const pc = weightPercents(stored);
  for (const i of $('bo-weight-inputs').querySelectorAll('input[type=number]')) {
    i.value = String(pc[i.dataset.dimension]);
    i.parentElement.querySelector('input[type=range]').value = String(pc[i.dataset.dimension]);
  }
  whatIf = null; weightsChanged(false); if (baseData) draw(baseData);
}
function setSide(open) {
  $('workspace').classList.toggle('side-collapsed', !open);
  $('bo-list-toggle').setAttribute('aria-expanded', String(open));
  $('bo-list-toggle').textContent = open ? 'Hide bake-off list' : 'All bake-offs and new';
}

/** An inline confirmation for an action that cannot be undone or is billable (same pattern as the launch confirmation). */
function askAction(text, yesLabel, run) {
  pendingAction = run;
  $('bo-action-text').textContent = text; $('bo-action-yes').textContent = yesLabel; $('bo-action-confirm').hidden = false;
  $('bo-action-no').focus();
}
function hideAction() { pendingAction = null; $('bo-action-confirm').hidden = true; }

function saveText(name, type, text) {
  const href = URL.createObjectURL(new Blob([text], {type}));
  const a = el('a'); a.href = href; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

/** Fill the launch form from the open bake-off's plan, so a variation starts from what was run. */
function cloneCurrent() {
  if (!lastBody) return;
  const b = lastBody.bakeoff, ids = new Set(lastBody.cells.map(c => c.scenario_id));
  const reps = Math.max(1, ...lastBody.cells.map(c => c.repetition + 1));
  setSide(true); $('new-drawer').open = true;
  $('bo-name').value = `Copy of ${b.name}`.slice(0, 120);
  $('bo-prompt-profile').value = b.prompt_profile || 'standardised-v2';
  const left = [];
  for (const input of $('bo-providers').querySelectorAll('input')) {
    const wanted = b.providers.includes(input.value);
    input.checked = wanted && !input.disabled;
    if (wanted && input.disabled) left.push(providerName(input.value));
  }
  for (const input of $('bo-groups').querySelectorAll('input[data-scenario]')) input.checked = ids.has(input.dataset.scenario);
  groups.forEach((_, i) => syncGroup(i));
  $('bo-reps').value = String(Math.min(10, reps)); repsTouched = true;
  size();
  if (left.length) $('bo-size').textContent += ` Not selected because unavailable: ${left.join(', ')}.`;
  revealResult($('new-drawer'));
}

async function download() {
  try {
    const response = await fetch('/api/v1/bakeoffs/' + encodeURIComponent(current) + '/report.html', {headers: {Authorization: 'Bearer ' + token}});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const href = URL.createObjectURL(await response.blob());
    const a = el('a'); a.href = href; a.download = 'bakeoff-' + current + '.html'; a.click();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  } catch (e) { report(e); }
}

async function connect() {
  token = $('token').value;
  try {
    await loadList();
    await loadForm();
    loadCostHistory();
    setConnected(true); sessionStorage.setItem('harness-operator-token', token);
    $('error').textContent = ''; $('workspace').hidden = false;
    const wanted = new URLSearchParams(location.search).get('bakeoff');
    if (wanted) await open(wanted);
  } catch (e) { setConnected(false); report(e); }
}

$('auth').addEventListener('submit', e => { e.preventDefault(); connect(); });
$('bo-launch').addEventListener('click', askToLaunch);
$('bo-download').addEventListener('click', download);
const act = (action, id) => async () => { try { await post(`/api/v1/bakeoffs/${encodeURIComponent(id)}/${action}`, {}); await refresh(); await loadList(); } catch (e) { report(e); } };
$('bo-cancel').addEventListener('click', () => {
  const b = lastBody?.bakeoff;
  askAction(`Cancel "${b?.name ?? 'this bake-off'}"? Runs in flight are stopped and no more launch. Finished runs stay in the report, but a cancelled bake-off cannot be restarted.`, 'Cancel bake-off', act('cancel', current));
});
$('bo-resume').addEventListener('click', () => {
  const n = lastBody?.bakeoff.providers.length ?? 0;
  askAction(`Resume starts a new provider check on all ${n} providers before any failed run is retried. Those checks are billable runs, and the retried runs are billed too. Continue?`, 'Recheck and resume', act('resume', current));
});
$('bo-action-yes').addEventListener('click', () => { const run = pendingAction; hideAction(); run?.(); });
$('bo-action-no').addEventListener('click', hideAction);
$('bo-clone').addEventListener('click', cloneCurrent);
$('bo-csv').addEventListener('click', () => baseData && saveText(`bakeoff-${current}-observations.csv`, 'text/csv', observationsCsv(baseData.observations, current)));
$('bo-json').addEventListener('click', () => baseData && saveText(`bakeoff-${current}-observations.json`, 'application/json',
  JSON.stringify({bakeoff_id: current, name: baseData.title, generated_at: baseData.generated_at, providers: baseData.providers, weights: stored, observations: baseData.observations}, null, 2)));
$('bo-reset').addEventListener('click', resetWeights);
$('bo-reset-banner').addEventListener('click', resetWeights);
$('bo-list-toggle').addEventListener('click', () => setSide($('workspace').classList.contains('side-collapsed')));
$('bo-confirm-yes').addEventListener('click', launch);
$('bo-confirm-no').addEventListener('click', () => { $('bo-confirm').hidden = true; });
let resize;
window.addEventListener('resize', () => { clearTimeout(resize); resize = setTimeout(() => view?.redraw(), 150); });
try { const saved = sessionStorage.getItem('harness-operator-token'); if (saved) { $('token').value = saved; connect(); } } catch { /* no saved token */ }
