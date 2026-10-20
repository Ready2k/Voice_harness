import {setConnected} from './chrome.mjs?v=ux-final-20261023';
import {api, el} from './runs.mjs?v=ux-final-20261023';
import {friendlyError} from './onboarding.mjs?v=ux-final-20261023';
import {estimateCost, normaliseWeights, planSize, renderReport, scenarioGroups, usdEstimate, joinParts, weightPercents, weightsAreCustom, weightsTotal, words} from './bakeoff-report.mjs?v=ux-final-20261023';

const $ = id => document.getElementById(id);
import {providerName} from './providers.mjs?v=ux-final-20261023';
import {revealResult} from './nav.mjs?v=ux-final-20261023';
let token = '', groups = [], current = null, view = null, poll = null, whatIf = null, baseData = null, stored = {}, costHistory = null, recomputeTimer = null, seq = 0;

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
    const label = el('label', 'check'), input = el('input');
    input.type = 'checkbox'; input.value = String(i); input.checked = true; input.addEventListener('change', size);
    label.append(input, `${g.group} (${g.ids.length})`);
    return label;
  }));
  $('bo-reps').addEventListener('input', size);
  size();
}
const chosenProviders = () => [...$('bo-providers').querySelectorAll('input:checked')].map(i => i.value);
const chosenScenarios = () => [...$('bo-groups').querySelectorAll('input:checked')].flatMap(i => groups[Number(i.value)].ids);
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
  const {providers, scenarios, reps, runs, estimate} = plan();
  $('bo-confirm').hidden = true;
  $('bo-size').textContent = `${runs} live runs (${scenarios.length} scenarios × ${providers.length} providers × ${reps}).`
    + (reps < 3 ? ' Use 5 or more repetitions to separate close providers.' : '')
    + (estimate?.text ? ` Estimated cost ${estimate.text}, from the median per-run voice cost recorded in "${costHistory.name}".` : ' No recorded per-run cost for every chosen provider, so no estimate.');
  $('bo-launch').textContent = `Launch bake-off · ${runs} runs` + (estimate?.text ? ' · ' + estimate.text : '');
  $('bo-launch').disabled = providers.length < 2 || !scenarios.length || runs > 600;
  if (runs > 600) $('bo-size').textContent += ' That is over the 600-run limit for one bake-off.';
}
function askToLaunch() {
  const {providers, runs, estimate} = plan();
  $('bo-confirm-text').textContent = `Launch ${runs} live runs on ${providers.map(providerName).join(', ')}? You are billed by each provider`
    + (estimate?.text ? ` (estimated ${estimate.text}, not a quote).` : ' (cost unknown: no recorded per-run cost for every chosen provider).');
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
      repetitions: repsValue(),
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
  current = id; whatIf = null; setSide(false);
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
  $('bo-state').textContent = words(b.state);
  $('bo-state').className = 'badge state-' + (b.state === 'running' ? 'running' : b.state === 'completed' ? 'completed' : 'cancelled');
  $('bo-bar').style.width = (b.cells ? b.cells_done / b.cells * 100 : 0) + '%';
  $('bo-progress').textContent = `${b.cells_done} of ${b.cells} runs settled · ${b.runs_in_flight} in flight · ${b.retries} retried · ${b.providers.map(p => providerName(p)).join(', ')}`
    + (b.mode === 'imported' ? ' · built from existing runs' : '');
  $('bo-cancel').hidden = b.state !== 'running';
  baseData = body.data; stored = body.weights;
  weightInputs(body.weights, body.data.report.dimensions);
  if (customNow()) await recompute(); else draw(body.data);
  if (b.state === 'running') poll = setTimeout(() => refresh().then(loadList).catch(report), 5000);
}

function draw(data) {
  const root = $('bo-report');
  if (!data.observations.length) {
    root.replaceChildren(el('div', 'panel', 'Runs are starting. The report appears as soon as the first run settles.'));
    view = null;
    return;
  }
  // Display names come from providers.mjs (hover = technical id); the server data and the export are unchanged.
  const names = Object.fromEntries(Object.keys(data.names ?? {}).map(id => [id, providerName(id)]));
  const short = Object.fromEntries(Object.keys(data.short ?? {}).map(id => [id, data.short[id]]));
  view = renderReport(root, {...data, names, short}, {heading: false, reveal: revealResult});
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
$('bo-cancel').addEventListener('click', async () => { try { await post(`/api/v1/bakeoffs/${encodeURIComponent(current)}/cancel`, {}); await refresh(); await loadList(); } catch (e) { report(e); } });
$('bo-reset').addEventListener('click', resetWeights);
$('bo-reset-banner').addEventListener('click', resetWeights);
$('bo-list-toggle').addEventListener('click', () => setSide($('workspace').classList.contains('side-collapsed')));
$('bo-confirm-yes').addEventListener('click', launch);
$('bo-confirm-no').addEventListener('click', () => { $('bo-confirm').hidden = true; });
let resize;
window.addEventListener('resize', () => { clearTimeout(resize); resize = setTimeout(() => view?.redraw(), 150); });
try { const saved = sessionStorage.getItem('harness-operator-token'); if (saved) { $('token').value = saved; connect(); } } catch { /* no saved token */ }
