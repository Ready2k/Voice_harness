import {setConnected} from './chrome.mjs?v=ux-final-20261025';
import {api, el} from './runs.mjs?v=ux-final-20261025';
const $ = id => document.getElementById(id), base = '/api/v1/telephone';
let providers = [], providerSignature = '', setupTemplates = {}, setupLoaded = false;
let token = '', timer, busy = false, urls = [], signature = '', defaults = {};
const edited = new Set();
const field = name => $('call-form').elements.namedItem(name);
$('call-form').addEventListener('input', e => { if (e.target.name) edited.add(e.target.name); });
function applyScenarioDefaults(force = false) {
  for (const [name, value] of Object.entries(defaults[field('scenario').value] ?? {})) {
    if (force || (!edited.has(name) && !field(name).value)) field(name).value = value;
  }
}
field('scenario').addEventListener('change', () => { applyScenarioDefaults(true); applySetupTemplate(); updateAgent(); });
field('response_mode').addEventListener('change', updateAgent);
field('audio_mode').addEventListener('change', updateAgent);
field('provider_config_id').addEventListener('change', updateAgent);
function updateAgent() {
  const ai = field('response_mode').value === 'ai';
  field('audio_mode').querySelector('[value="native"]').disabled = !ai;
  if (!ai) field('audio_mode').value = 'gated';
  field('parallel_speech_checks').disabled = field('audio_mode').value === 'native';
  if (field('parallel_speech_checks').disabled) field('parallel_speech_checks').checked = false;
  $('fixed-editor').hidden = ai;
  for (const node of $('fixed-editor').querySelectorAll('input, textarea')) node.required = !ai;
  const provider = providers.find(p => p.provider_config_id === field('provider_config_id').value);
  $('agent-note').textContent = provider ? (ai ? 'Reply agent: ' : 'Routing and phrase controller: ') + provider.label + ' · ' + provider.model_id + (provider.missing.length ? ' · Requires: ' + provider.missing.join(', ') : '') + (['gemini-live', 'nova-sonic-live'].includes(provider.provider_config_id) ? '. Experimental telephone integration; provider controls audio turn timing.' : '') : '';
}
async function loadTwilio() {
  $('twilio-status').textContent = 'Fetching caller numbers from Twilio…';
  try {
    const data = await request('/configuration'); defaults = data.scenario_defaults;
    $('twilio-numbers').replaceChildren(...data.items.map(n => { const option = el('option'); option.value = n.phone_number; option.label = n.friendly_name; return option; }));
    if (!edited.has('caller_id') && !field('caller_id').value && data.caller_id) field('caller_id').value = data.caller_id;
    applyScenarioDefaults();
    $('twilio-status').textContent = data.items.length ? 'Caller numbers fetched from Twilio. Destination and routing defaults come from our demo setup.' : 'No voice-capable Twilio numbers found. Enter a verified caller number.';
  } catch (e) { $('twilio-status').textContent = 'Could not fetch Twilio numbers: ' + e.message + '. You can enter the caller number manually.'; }
}
$('reload-twilio').onclick = loadTwilio;
const request = (path = '', options = {}) => api(token, base + path, options);
function labelled(text, input) { const label = el('label', '', text); label.append(input); return label; }
function input(value = '', max = 200) { const node = el('input'); node.value = value; node.maxLength = max; node.required = true; node.autocomplete = 'off'; return node; }
function choice(options, selected) { const select = el('select'); for (const [value, text] of options) { const option = el('option', '', text); option.value = value; select.append(option); } select.value = selected; return select; }
function addDetail(value = {}) {
  const row = el('fieldset'); row.className = 'supplied-detail';
  const label = input(value.label ?? '', 80), content = input(value.value ?? ''), when = input(value.when_asked ?? '', 100);
  label.dataset.field = 'label'; content.dataset.field = 'value'; when.dataset.field = 'when_asked';
  const kind = choice([['fact', 'Card / brand / fact'], ['identifier', 'Account ID / identity answer'], ['passcode', 'Passcode / PIN']], value.kind ?? 'fact'); kind.dataset.field = 'kind';
  const delivery = choice([['speech', 'Speak'], ['dtmf', 'Keypad tones']], value.delivery ?? 'speech'); delivery.dataset.field = 'delivery';
  const update = () => { content.type = kind.value === 'passcode' ? 'password' : 'text'; when.required = kind.value !== 'fact'; delivery.disabled = kind.value === 'fact'; if (kind.value === 'fact') delivery.value = 'speech'; };
  kind.onchange = update; update();
  const remove = el('button', '', 'Remove detail'); remove.type = 'button'; remove.onclick = () => row.remove();
  row.append(labelled('Detail type', kind), labelled('Label (for example, Visa debit card or Account ID)', label), labelled('Value supplied by you', content), labelled('When asked for (for example, account number or passcode)', when), labelled('Send using', delivery), remove); $('call-details').append(row);
}
function addResponse(value = {}) {
  const row = el('fieldset'); row.className = 'fixed-response';
  const when = input(value.when_asked ?? '', 120), text = el('textarea'); text.rows = 2; text.maxLength = 450; text.required = true; text.value = value.text ?? '';
  when.dataset.field = 'when_asked'; text.dataset.field = 'text';
  when.required = text.required = field('response_mode').value === 'static';
  const remove = el('button', '', 'Remove response'); remove.type = 'button'; remove.onclick = () => row.remove();
  row.append(labelled('When asked about', when), labelled('Say exactly', text), remove); $('fixed-responses').append(row);
}
function applySetupTemplate() {
  const template = setupTemplates[field('scenario').value]; if (!template) return;
  $('call-goal').value = template.goal; $('call-situation').value = template.situation; $('call-opening').value = template.opening; $('call-tone').value = template.tone;
  $('call-details').replaceChildren(); for (const detail of template.details) addDetail(detail);
  $('fixed-responses').replaceChildren(); for (const response of template.fixed_responses) addResponse(response);
}
function readRows(selector) { return [...document.querySelectorAll(selector)].map(row => Object.fromEntries([...row.querySelectorAll('[data-field]')].map(node => [node.dataset.field, node.value.trim()]))); }
function readCallSetup() { return {goal: $('call-goal').value.trim(), situation: $('call-situation').value.trim(), opening: $('call-opening').value.trim(), tone: $('call-tone').value, details: readRows('.supplied-detail'), fixed_responses: field('response_mode').value === 'static' ? readRows('.fixed-response') : []}; }
$('add-detail').onclick = () => addDetail(); $('add-response').onclick = () => addResponse();

function error(e) { $('error').textContent = e.message; }
async function artifact(id, name, parent) {
  try {
    const response = await fetch(`${base}/${id}/artifacts/${name}`, {headers: {Authorization: 'Bearer ' + token}});
    if (!response.ok) throw new Error('Recording unavailable');
    const url = URL.createObjectURL(await response.blob()); urls.push(url);
    if (name.endsWith('.wav')) { const audio = el('audio'); audio.controls = true; audio.src = url; parent.append(audio); }
    else { const a = el('a', '', 'Download ' + name); a.href = url; a.download = name; parent.append(a); }
  } catch (e) { error(e); }
}
function render(items) {
  const next = JSON.stringify(items); if (next === signature) return; signature = next;
  for (const url of urls) URL.revokeObjectURL(url); urls = [];
  $('calls').replaceChildren();
  if (!items.length) $('calls').append(el('p', 'muted', 'No telephone calls yet.'));
  for (const call of items) {
    const card = el('section', 'panel'); card.append(el('h3', '', `${call.call_id.slice(0, 8)} · ${call.state}`));
    if (call.setup) {
      const provider = providers.find(p => p.provider_config_id === call.setup.provider_config_id);
      if (call.setup.call_setup) {
        card.append(el('p', '', call.setup.call_setup.goal));
        const brief = el('details'); brief.append(el('summary', '', 'Call brief & supplied details'), el('pre', '', JSON.stringify(call.setup.call_setup, null, 2))); card.append(brief);
      }
      card.append(el('p', 'muted', (call.setup.response_mode === 'static' ? 'Static phrases' : 'AI replies') + ' · ' + (provider?.label ?? call.setup.provider_config_id) + ' · ' + call.setup.scenario + ' · ' + (call.setup.audio_mode === 'native' ? 'Native voice · unassessed' : 'Gated speech')));
    }
    if (call.state === 'running') {
      card.append(el('p', '', 'Preparing or calling. Stop requests hangup and preserves the evidence.'));
      const stop = el('button', 'danger', 'Stop call'); stop.onclick = async () => { stop.disabled = true; try { await request(`/${call.call_id}/stop`, {method: 'POST'}); } catch(e) { error(e); stop.disabled = false; } }; card.append(stop);
    }
    if (call.summary) { const pre = el('pre', '', JSON.stringify(call.summary, null, 2)); const detail = el('details'); detail.append(el('summary', '', 'Finding & evidence details'), pre); card.append(detail); }
    for (const name of call.artifacts) {
      const row = el('p'); const button = el('button', '', name.endsWith('.wav') ? 'Listen · ' + name : 'Download · ' + name);
      button.onclick = () => { button.disabled = true; artifact(call.call_id, name, row).finally(() => {button.disabled = false;}); }; row.append(button); card.append(row);
    }
    $('calls').append(card);
  }
}
async function refresh() {
  const data = await request();
  providers = data.providers ?? [];
  setupTemplates = data.setup_templates ?? {};
  if (!setupLoaded && setupTemplates[field('scenario').value]) { applySetupTemplate(); setupLoaded = true; }
  const ps = JSON.stringify(providers);
  if (ps !== providerSignature) {
    const selected = field('provider_config_id').value; providerSignature = ps;
    $('agent').replaceChildren(...providers.map(p => { const option = el('option', '', p.label + ' · ' + p.model_id + (p.missing.length ? ' · unavailable' : '')); option.value = p.provider_config_id; option.disabled = !!p.missing.length; return option; }));
    if (providers.some(p => p.provider_config_id === selected && !p.missing.length)) $('agent').value = selected;
    else $('agent').value = providers.find(p => !p.missing.length)?.provider_config_id ?? '';
  }
  updateAgent();
  $('readiness').textContent = data.ready ? 'Twilio and ingress configuration available. Provider and speech preflight run before dialling.' : 'Server setup required: ' + data.missing.join(', ');
  $('fields').disabled = busy || !data.ready || data.items.some(c => c.state === 'running');
  $('start').disabled = !providers.some(p => p.provider_config_id === $('agent').value && !p.missing.length); render(data.items);
}
async function connect(value) {
  token = value; await refresh(); sessionStorage.setItem('harness-operator-token', token); setConnected(true); $('workspace').hidden = false;
  await loadTwilio();
  clearInterval(timer); timer = setInterval(() => refresh().catch(error), 3000);
}
$('auth').onsubmit = e => { e.preventDefault(); connect($('token').value.trim()).catch(error); };
$('call-form').onsubmit = async e => {
  e.preventDefault(); busy = true; $('fields').disabled = true; $('error').textContent = '';
  const body = {};
  for (const input of e.target.querySelectorAll('[name]')) body[input.name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
  body.subtest_id ||= null;
  body.call_setup = readCallSetup();
  try { await request('', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); } catch(e) { error(e); }
  finally { busy = false; await refresh().catch(error); }
};
const saved = sessionStorage.getItem('harness-operator-token'); if (saved) connect(saved).catch(error);
window.addEventListener('pagehide', () => { clearInterval(timer); for (const url of urls) URL.revokeObjectURL(url); });
