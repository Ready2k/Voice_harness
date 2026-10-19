import {setConnected} from './chrome.mjs?v=ux-audit-20261018';
import {api, el} from './runs.mjs?v=ux-audit-20261018';
import {friendlyError} from './onboarding.mjs?v=ux-audit-20261018';
import {planSize, renderReport, scenarioGroups, words} from './bakeoff-report.mjs?v=bakeoff-20261019';

const $ = id => document.getElementById(id);
const NAMES = {'openai-realtime-live': 'OpenAI Realtime', 'nova-sonic-live': 'Amazon Nova Sonic', 'xai-voice-live': 'xAI Grok Voice', 'gemini-live': 'Google Gemini Live'};
let token = '', groups = [], current = null, view = null, poll = null, whatIf = null;

const post = (path, body) => api(token, path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
const report = e => { $('error').textContent = friendlyError(e); };

// ---------------------------------------------------------------- launch form
async function loadForm() {
  const [providers, scenarios] = await Promise.all([api(token, '/api/v1/live-providers'), api(token, '/api/v1/scenarios?limit=1000')]);
  $('bo-providers').replaceChildren(...providers.items.map(p => {
    const label = el('label', 'check'), input = el('input');
    input.type = 'checkbox'; input.value = p.provider_config_id; input.disabled = !p.available; input.checked = p.available;
    input.addEventListener('change', size);
    label.append(input, (NAMES[p.provider_config_id] ?? p.provider_config_id) + (p.available ? '' : ' · unavailable: ' + words(p.reason)));
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
function size() {
  const reps = Math.max(1, Math.min(10, Number($('bo-reps').value) || 1));
  const runs = planSize(chosenScenarios().length, chosenProviders().length, reps);
  $('bo-size').textContent = `${runs} live runs (${chosenScenarios().length} scenarios × ${chosenProviders().length} providers × ${reps}).`
    + (reps < 3 ? ' Use 5 or more repetitions to separate close providers.' : '');
  $('bo-launch').disabled = chosenProviders().length < 2 || !chosenScenarios().length || runs > 600;
  if (runs > 600) $('bo-size').textContent += ' That is over the 600-run limit for one bake-off.';
}
async function launch() {
  $('bo-launch').disabled = true;
  try {
    const all = groups.flatMap(g => g.ids), scenarios = chosenScenarios();
    const created = await post('/api/v1/bakeoffs', {
      name: $('bo-name').value.trim() || 'Bake-off ' + new Date().toISOString().slice(0, 16).replace('T', ' '),
      provider_config_ids: chosenProviders(), scenario_ids: scenarios.length === all.length ? [] : scenarios,
      repetitions: Number($('bo-reps').value) || 1,
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
    button.append(el('strong', '', b.name), el('span', 'muted', ` · ${words(b.state)} · ${b.cells_done}/${b.cells} runs`));
    button.addEventListener('click', () => open(b.bakeoff_id));
    root.append(button);
  }
}

async function open(id) {
  current = id; whatIf = null;
  const url = new URL(location.href); url.searchParams.set('bakeoff', id); history.replaceState(null, '', url);
  await refresh();
  await loadList();
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
  $('bo-progress').textContent = `${b.cells_done} of ${b.cells} runs settled · ${b.runs_in_flight} in flight · ${b.retries} retried · ${b.providers.map(p => NAMES[p] ?? p).join(', ')}`
    + (b.mode === 'imported' ? ' · built from existing runs' : '');
  $('bo-cancel').hidden = b.state !== 'running';
  weightInputs(body.weights, body.data.report.dimensions);
  draw(whatIf ?? body.data);
  if (b.state === 'running') poll = setTimeout(() => refresh().then(loadList).catch(report), 5000);
}

function draw(data) {
  const root = $('bo-report');
  if (!data.observations.length) {
    root.replaceChildren(el('div', 'panel', 'Runs are starting. The report appears as soon as the first run settles.'));
    view = null;
    return;
  }
  view = renderReport(root, data, {heading: false});
}

function weightInputs(stored, dimensions) {
  const box = $('bo-weight-inputs');
  if (box.dataset.for === current) return;  // keep what the operator typed across refreshes
  box.dataset.for = current;
  box.replaceChildren(...dimensions.map(d => {
    const label = el('label', '', d.label), input = el('input');
    input.type = 'number'; input.min = '0'; input.max = '1'; input.step = '0.05'; input.value = String(stored[d.dimension]); input.dataset.dimension = d.dimension;
    label.append(input);
    return label;
  }));
}

async function recompute() {
  const weights = Object.fromEntries([...$('bo-weight-inputs').querySelectorAll('input')].map(i => [i.dataset.dimension, Math.max(0, Math.min(1, Number(i.value) || 0))]));
  try { whatIf = (await post(`/api/v1/bakeoffs/${encodeURIComponent(current)}/report`, weights)).data; draw(whatIf); } catch (e) { report(e); }
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
    setConnected(true); sessionStorage.setItem('harness-operator-token', token);
    $('error').textContent = ''; $('workspace').hidden = false;
    const wanted = new URLSearchParams(location.search).get('bakeoff');
    if (wanted) await open(wanted);
  } catch (e) { setConnected(false); report(e); }
}

$('auth').addEventListener('submit', e => { e.preventDefault(); connect(); });
$('bo-launch').addEventListener('click', launch);
$('bo-download').addEventListener('click', download);
$('bo-cancel').addEventListener('click', async () => { try { await post(`/api/v1/bakeoffs/${encodeURIComponent(current)}/cancel`, {}); await refresh(); await loadList(); } catch (e) { report(e); } });
$('bo-recompute').addEventListener('click', recompute);
$('bo-reset').addEventListener('click', () => { whatIf = null; $('bo-weight-inputs').dataset.for = ''; refresh().catch(report); });
let resize;
window.addEventListener('resize', () => { clearTimeout(resize); resize = setTimeout(() => view?.redraw(), 150); });
try { const saved = sessionStorage.getItem('harness-operator-token'); if (saved) { $('token').value = saved; connect(); } } catch { /* no saved token */ }
