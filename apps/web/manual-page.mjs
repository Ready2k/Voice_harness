import {setConnected} from './chrome.mjs?v=ux-final-20261025';
import {api, el, mergeEvents} from './runs.mjs?v=ux-final-20261025';
import {words} from './workflow.mjs?v=ux-final-20261025';
import {friendlyError} from './onboarding.mjs?v=ux-final-20261025';
import {EnergyVad, Framer, HARNESS_RATE, Resampler, STATUS_LABEL, base64ToBytes, feedItem, floatToPcm16, formatClock,
  liveChecks, pcm16ToFloat, promptProgress, reduceTranscript, rms, sessionStatus, CHALLENGE_LABEL, challengeOptions, challengeSource, historyRow, streamedImpact} from './manual.mjs?v=ux-final-20261025';

const $ = id => document.getElementById(id);
const radio = name => document.querySelector(`input[name="${name}"]:checked`)?.value;
import {providerName} from './providers.mjs?v=ux-final-20261025';
import {revealResult} from './nav.mjs?v=ux-final-20261025';
import {relativeTime, shortId, scrollRegion} from './format.mjs?v=ux-final-20261025';
let token = '', catalogue = null, journeyId = null, challengeId = null, s = null;

function button(text, fn, cls = '') { const b = el('button', cls, text); b.type = 'button'; b.addEventListener('click', fn); return b; }
function journey() { return catalogue.items.find(j => j.journey_id === journeyId); }

// ---------------------------------------------------------------- setup
function renderSetup() {
  const list = $('journeys');
  list.replaceChildren();
  for (const j of catalogue.items) {
    const card = el('label', 'journey-card');
    const input = el('input'); input.type = 'radio'; input.name = 'journey'; input.value = j.journey_id;
    input.checked = j.journey_id === journeyId;
    input.addEventListener('change', () => { journeyId = j.journey_id; challengeId = null; renderPreview(); });
    const body = el('span', 'journey-body');
    body.append(el('strong', '', j.title));
    body.title = 'Happy: ' + j.happy.title + '\n' + CHALLENGE_LABEL + ': ' + j.failure.title;
    card.append(input, body);
    list.append(card);
  }
  const select = $('provider');
  select.replaceChildren();
  for (const p of catalogue.providers) {
    const option = el('option', '', providerName(p.provider_config_id) + (p.available ? '' : ' · unavailable (' + words(p.reason) + ')'));
    option.value = p.provider_config_id; option.disabled = !p.available;
    select.append(option);
  }
  const ready = catalogue.providers.find(p => p.available);
  if (ready) select.value = ready.provider_config_id;
  $('provider-note').textContent = ready ? 'Credentials stay on the server. Only configured providers can be chosen.'
    : 'No live provider is configured for manual testing. Start the server with `make serve-live` and the provider keys, plus VOICE_HARNESS_LIVE_ASR=openai for independent assessment.';
  $('start').disabled = !ready;
  renderPreview();
}

/** The journey as run: its challenge path may come from another journey that owns the chosen scenario. */
function effectiveJourney() {
  const j = journey(), wanted = challengeId ?? j.failure.scenario_id, from = challengeSource(catalogue.items, j, wanted);
  return from === j ? {...j, failureJourneyId: j.journey_id} : {...j, failure: from.failure, failureJourneyId: from.journey_id};
}

function renderChallenge() {
  const j = journey(), isChallenge = radio('path') === 'failure', options = challengeOptions(catalogue.items);
  $('challenge-row').hidden = $('challenge-note').hidden = !isChallenge;
  if (!isChallenge) return;
  const select = $('challenge'), current = challengeId ?? j.failure.scenario_id;
  select.replaceChildren();
  for (const o of options) {
    const others = o.journeys.filter(x => x.journey_id !== j.journey_id).map(x => x.title);
    const option = el('option', '', o.title + (o.journeys.some(x => x.journey_id === j.journey_id) ? ' (this journey)' : '') + (others.length ? ' · also used by ' + others.join(', ') : ''));
    option.value = o.scenario_id; select.append(option);
  }
  select.value = current;
  const shared = options.find(o => o.scenario_id === current)?.journeys ?? [];
  $('challenge-note').textContent = shared.length > 1
    ? 'This challenge scenario is shared by ' + shared.map(x => x.title).join(' and ') + '. Pick a different one above if it is not the adversarial caller you want to test.'
    : 'This challenge scenario belongs to this journey only.';
}

function renderPreview() {
  const j = effectiveJourney(), path = radio('path') === 'failure' ? j.failure : j.happy;
  renderChallenge();
  const root = $('path-preview');
  root.replaceChildren(el('p', 'eyebrow', 'You will evaluate'), el('strong', '', path.title), el('p', 'muted', path.expected_outcome ?? ''));
  const warning = $('playback-warning'), streamed = radio('playback') === 'streamed', impact = streamedImpact(path);
  warning.hidden = !streamed; warning.replaceChildren();
  if (streamed) {
    warning.append(el('strong', '', 'Playback gate bypassed'), el('p', '', 'You hear the agent as it speaks, so unsafe speech can reach you. Replies are assessed afterwards.'));
    warning.append(el('p', '', impact.untestable.length ? 'Untestable in this mode: ' + impact.untestable.join(', ') + ' (cannot pass). Still tested: ' + (impact.testable.join(', ') || 'none') + '.'
      : 'No criterion on this path depends on the playback gate.'));
  }
  $('playback-note').textContent = streamed ? '' : 'Each reply is independently assessed before playback. Assessment can miss issues; approval is not a guarantee of safety. You will hear a pause while the reply is checked.';
}

// ---------------------------------------------------------------- audio
async function openAudio(session) {
  session.playCtx = new AudioContext({sampleRate: HARNESS_RATE, latencyHint: 'interactive'});
  await session.playCtx.audioWorklet.addModule(new URL('./manual-worklets.js?v=ux-final-20261025', import.meta.url));
  session.playNode = new AudioWorkletNode(session.playCtx, 'manual-playback', {numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1]});
  session.playNode.connect(session.playCtx.destination);
  session.playNode.port.onmessage = ({data}) => {
    if (data.type === 'playing') { session.aiPlaying = data.value; renderStatus(); }
    if (data.type === 'played') send({type: 'played', unit_id: data.unitId, end_sample: data.endSample});
    // A barge-in carries what was heard, so the server can acknowledge it and truncate the provider's history.
    if (data.type === 'flushed' && data.reason === 'barge_in') send({type: 'barge_in', unit_id: data.unitId, end_sample: data.endSample});
    else if (data.type === 'flushed' && data.unitId) send({type: 'played', unit_id: data.unitId, end_sample: data.endSample});
  };
  await session.playCtx.resume();
}

async function openMic(session) {
  if (session.capCtx) return;
  const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
  const ctx = new AudioContext({latencyHint: 'interactive'});
  await ctx.audioWorklet.addModule(new URL('./manual-worklets.js?v=ux-final-20261025', import.meta.url));
  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, 'manual-capture', {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1]});
  const sink = ctx.createGain(); sink.gain.value = 0; // keeps the capture node pulled without monitoring the mic
  source.connect(node).connect(sink).connect(ctx.destination);
  const resampler = new Resampler(ctx.sampleRate, HARNESS_RATE), framer = new Framer(), vad = new EnergyVad();
  node.port.onmessage = ({data}) => {
    for (const frame of framer.push(resampler.push(data))) onFrame(session, frame, vad);
  };
  Object.assign(session, {capCtx: ctx, stream, vad});
  await ctx.resume();
}

function capturing(session) { return session.connected && !session.ended && !session.paused && !session.muted && session.mode === 'voice'; }

function onFrame(session, frame, vad) {
  const level = rms(frame);
  $('meter-fill').style.width = Math.min(100, level * 600) + '%';
  if (!capturing(session)) { if (vad.reset() === 'end') { session.speaking = false; renderStatus(); } return; }
  const edge = vad.push(level);
  if (edge === 'start') {
    session.speaking = true;
    send({type: 'speech', phase: 'start'});
    if (session.aiPlaying && session.playbackMode === 'gated' && $('barge').checked) flushPlayback('barge_in');
    renderStatus();
  }
  session.socket.send(floatToPcm16(frame));
  if (edge === 'end') { session.speaking = false; send({type: 'speech', phase: 'end'}); renderStatus(); }
}

function flushPlayback(reason = null) { s?.playNode?.port.postMessage({type: 'flush', reason}); }

function closeAudio(session) {
  session.stream?.getTracks().forEach(t => t.stop());
  for (const ctx of [session.capCtx, session.playCtx]) ctx?.close().catch(() => {});
}

// ---------------------------------------------------------------- session
function send(message) { if (s?.socket?.readyState === WebSocket.OPEN) s.socket.send(JSON.stringify(message)); }

async function start(path = radio('path'), reuse = null) {
  const j = reuse?.journey ?? effectiveJourney(), provider = reuse?.provider ?? $('provider').value, playbackMode = reuse?.playbackMode ?? radio('playback');
  const mode = reuse?.mode ?? radio('input');
  $('start').disabled = true; $('start-status').textContent = 'Starting…'; $('error').textContent = '';
  const session = s = {journey: j, path, pathInfo: path === 'failure' ? j.failure : j.happy, provider, playbackMode, mode,
    paused: false, muted: false, connected: false, ended: false, aiPlaying: false, speaking: false, assessing: 0, turn: 0,
    identity: 'unverified', events: [], assessments: [], lines: [], feed: [], confirmation: null, viewing: path};
  try {
    await openAudio(session);
    if (mode === 'voice') await openMic(session);
    const created = await api(token, '/api/v1/manual-sessions', {method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID()},
      body: JSON.stringify({provider_config_id: provider, journey_id: path === 'failure' ? j.failureJourneyId ?? j.journey_id : j.journey_id, path, playback_mode: playbackMode})});
    session.runId = created.run_id;
    const {ticket} = await api(token, '/api/v1/runs/' + created.run_id + '/ws-ticket', {method: 'POST'});
    const url = new URL(created.websocket, location.origin); url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    session.socket = new WebSocket(url, ['manual.v1', 'ticket.' + ticket]);
    session.socket.onmessage = ({data}) => onMessage(session, JSON.parse(data));
    session.socket.onclose = () => { if (!session.ended) finish(session, null, 'The connection closed before the session ended.'); };
    showSession();
  } catch (e) {
    closeAudio(session);
    $('start').disabled = false; $('start-status').textContent = '';
    $('error').textContent = e?.name === 'NotAllowedError' ? 'Microphone access was refused. Allow it in the browser, or start in text mode.' : friendlyError(e);
  }
}

function onMessage(session, m) {
  if (session !== s) return;
  const item = feedItem(m);
  if (item) { session.feed.unshift(item); session.feed.length = Math.min(session.feed.length, 60); }
  switch (m.type) {
    case 'status': $('start-status').textContent = m.stage === 'evaluating' ? 'Evaluating the run…' : 'Opening the provider session…';
      if (m.stage === 'evaluating') { session.evaluating = true; } break;
    case 'ready':
      Object.assign(session, {connected: true, startedAt: Date.now(), maxMs: m.max_session_seconds * 1000});
      if (session.mode === 'text') send({type: 'control', action: 'mode_text'});
      session.ping = setInterval(() => send({type: 'ping'}), 10000);
      session.tick = setInterval(renderTimer, 1000);
      break;
    case 'state': Object.assign(session, {paused: m.paused, muted: m.muted, mode: m.mode, turn: m.turn}); break;
    case 'turn': session.turn = m.turn; break;
    case 'audio': {
      const samples = pcm16ToFloat(base64ToBytes(m.audio));
      session.playNode.port.postMessage({type: 'enqueue', unitId: m.unit_id ?? null, endSample: m.end_sample, samples}, [samples.buffer]);
      break;
    }
    case 'flush': if (m.reason !== 'barge_in') flushPlayback(); break;
    case 'assessing': session.assessing += 1; break;
    case 'assessment': session.assessing = Math.max(0, session.assessing - 1); session.assessments.push(m); break;
    case 'tool': session.identity = m.identity ?? session.identity; break;
    case 'ledger': session.events = mergeEvents(session.events, m.events); break;
    case 'confirmation': session.confirmation = m; break;
    case 'session_ended': session.endReason = m.reason; break;
    case 'ended': finish(session, m); return;
    default:
  }
  session.lines = reduceTranscript(session.lines, m);
  render();
}

function control(action) { send({type: 'control', action}); }

async function toggleMode() {
  if (s.mode === 'text') {
    try { await openMic(s); } catch (e) { $('error').textContent = 'Microphone access was refused, so the session stays in text mode.'; return; }
    control('mode_voice');
  } else control('mode_text');
}

function togglePause() {
  if (s.paused) { s.playCtx.resume(); control('resume'); } else { s.playCtx.suspend(); control('pause'); }
}

function sendText(text) {
  if (!text.trim() || s.paused || s.ended) return;
  send({type: 'text', text});
  s.lines = reduceTranscript(s.lines, {type: 'local_text', text});
  render();
}

function stop() {
  $('stop').disabled = true; $('stop').textContent = 'Evaluating…';
  send({type: 'stop'});
}

async function finish(session, ended, problem = null) {
  if (session.ended) return;
  session.ended = true;
  clearInterval(session.ping); clearInterval(session.tick);
  closeAudio(session);
  render();
  let assurance = null, run = null;
  try {
    [assurance, run] = await Promise.all([api(token, '/api/v1/runs/' + session.runId + '/assurance'), api(token, '/api/v1/runs/' + session.runId)]);
  } catch { /* the summary below still renders from the final message */ }
  renderResult(session, ended, assurance, run, problem);
}

// ---------------------------------------------------------------- rendering
function showSession() {
  $('setup').hidden = true; $('result').hidden = true; $('session').hidden = false;
  $('stop').disabled = false; $('stop').textContent = 'Stop & evaluate';
  render();
}

function renderTimer() {
  if (!s?.startedAt || s.ended) return;
  const used = Date.now() - s.startedAt;
  $('timer').textContent = formatClock(used) + ' · ' + formatClock(s.maxMs - used) + ' left';
}

function renderStatus() {
  if (!s) return;
  const status = s.evaluating ? {tone: 'info', text: 'Evaluating…'} : sessionStatus({...s, assessing: s.assessing > 0});
  const chip = $('status-chip');
  chip.textContent = status.text; chip.className = 'status-chip tone-' + status.tone;
}

function render() {
  if (!s) return;
  renderStatus();
  $('session-label').textContent = s.journey.title + ' · ' + (s.path === 'failure' ? 'challenge path' : 'happy path') + ' · ' + (s.playbackMode === 'streamed' ? 'streamed playback' : 'gated playback');
  $('pause').textContent = s.paused ? 'Resume' : 'Pause'; $('pause').setAttribute('aria-pressed', String(s.paused));
  $('mute').textContent = s.muted ? 'Unmute mic' : 'Mute mic'; $('mute').setAttribute('aria-pressed', String(s.muted));
  $('mute').disabled = s.mode !== 'voice' || s.ended;
  $('mode').textContent = s.mode === 'voice' ? 'Switch to text' : 'Switch to voice';
  for (const id of ['pause', 'mode']) $(id).disabled = !s.connected || s.ended;
  $('composer').hidden = s.mode !== 'text'; $('voice-hint').hidden = s.mode === 'text';
  $('message').disabled = s.paused || s.ended;
  renderConversation(); renderGuide(); renderChecks(); renderFeed();
}

function renderConversation() {
  const root = $('conversation');
  root.replaceChildren();
  if (!s.lines.length) root.append(el('p', 'muted', s.connected ? 'Say the first line from the guide, or type it in text mode.' : 'Opening the provider session…'));
  for (const line of s.lines) {
    const row = el('div', 'bubble bubble-' + line.role + (line.status ? ' status-' + line.status : ''));
    row.append(el('span', 'speaker', line.role === 'user' ? (line.source === 'text' ? 'You · typed' : 'You · spoken') : 'Agent'));
    row.append(el('p', '', line.text || (line.role === 'user' ? '(speech)' : '…')));
    if (line.role === 'assistant' && line.status) {
      const streamedLabel = {approved: 'Played before assessment · approved', held: 'Played before assessment · judged unsafe'}[line.status];
      row.append(el('span', 'chip chip-' + line.status, (s.playbackMode === 'streamed' && streamedLabel) || STATUS_LABEL[line.status] || words(line.status)));
      if (line.heard && line.heard !== line.text) row.append(el('span', 'muted', 'Independent ASR heard: “' + line.heard + '”'));
      if (line.reasons?.length && line.status !== 'approved') row.append(el('span', 'muted', line.reasons.map(words).join(' · ')));
    }
    root.append(row);
  }
  root.scrollTop = root.scrollHeight;
}

function renderGuide() {
  for (const p of ['happy', 'failure']) {
    const tab = $('tab-' + p);
    tab.setAttribute('aria-selected', String(s.viewing === p));
    tab.textContent = (p === 'happy' ? 'Happy path' : CHALLENGE_LABEL) + (s.path === p ? ' · evaluating' : '');
  }
  const info = s.viewing === 'failure' ? s.journey.failure : s.journey.happy, active = s.viewing === s.path;
  const root = $('guide');
  root.replaceChildren(el('p', 'eyebrow', active ? 'Being evaluated in this session' : 'Not evaluated in this session'), el('h2', '', info.title));
  root.append(el('p', 'muted', info.expected_outcome ?? ''));
  const list = el('ol', 'prompts');
  for (const p of promptProgress(info.prompts, active ? s.turn : -1)) {
    const li = el('li', 'prompt prompt-' + (active ? p.state : 'upcoming'));
    if (active && p.state === 'next') li.append(el('span', 'eyebrow', 'Say next'));
    li.append(el('q', '', p.text));
    if (active && p.state !== 'done' && s.mode === 'text' && !s.ended) li.append(button('Send this', () => sendText(p.text), 'link'));
    list.append(li);
  }
  root.append(list);
  if (active && s.turn >= info.prompts.length) root.append(el('p', 'muted', 'Script complete. Keep going in your own words, or stop and evaluate.'));
  if (info.tips?.length) { const tips = el('ul', 'tips'); for (const t of info.tips) tips.append(el('li', '', t)); root.append(el('h3', '', 'Try this'), tips); }
  if (active && info.mechanics.includes('out_of_band_confirmation')) {
    const confirm = button(s.confirmation?.stage === 'awaiting_voice' ? 'Waiting for your “yes”…' : 'Request confirmation', () => send({type: 'confirm_request'}));
    confirm.disabled = s.ended || s.mode !== 'voice' || s.confirmation?.stage === 'awaiting_voice';
    root.append(confirm, el('p', 'muted', 'Issues a trusted cancellation request. Your next utterance is checked by independent ASR before the agent hears it.'));
  }
  if (!active) {
    root.append(el('p', 'muted', 'This path has its own rubric. To evaluate it, stop this session and start the other path.'));
    if (s.ended) root.append(button('Start this path', () => start(s.viewing, s)));
  }
}

function renderChecks() {
  const root = $('checks');
  root.replaceChildren();
  for (const c of liveChecks({path: s.pathInfo, events: s.events, assessments: s.assessments, identity: s.identity, playbackMode: s.playbackMode, confirmation: s.confirmation})) {
    const li = el('li', 'check-item tone-' + c.status);
    li.append(el('span', 'check-mark', {pass: '✓', fail: '✕', warn: '!', pending: '…', info: 'i'}[c.status]), el('strong', '', c.label), el('span', 'muted', c.detail));
    root.append(li);
  }
}

function renderFeed() {
  const root = $('feed');
  root.replaceChildren();
  if (!s.feed.length) root.append(el('li', 'muted', 'Tool calls, policy decisions and assessments appear here as they happen.'));
  for (const f of s.feed) {
    const li = el('li', 'feed-item tone-' + f.tone);
    li.append(el('time', 'muted', new Date(f.at).toLocaleTimeString()), el('span', '', f.text));
    root.append(li);
  }
}

function tile(label, value) { const box = el('div', 'insight-stat verdict-' + (value ?? 'unknown')); box.append(el('span', 'muted', label), el('strong', '', words(value ?? 'not assessed'))); return box; }

function renderResult(session, ended, assurance, run, problem) {
  render();
  const root = $('result');
  root.hidden = false; root.replaceChildren();
  const panel = el('section', 'panel');
  panel.append(el('p', 'eyebrow', 'Session result'), el('h2', '', session.journey.title + ' · ' + (session.path === 'failure' ? 'challenge path' : 'happy path')));
  if (problem) panel.append(el('p', 'error-text', problem));
  const state = ended?.state ?? run?.state ?? 'unknown';
  if (state === 'failed' && !problem) panel.append(el('p', 'error-text', 'The run failed and was not assessed. '
    + (session.feed.find(f => f.kind === 'error' || f.kind === 'provider_error')?.text ?? 'Open the run evidence for the recorded reason.')));
  panel.append(el('p', 'muted', 'Run ' + session.runId + ' · ' + words(state) + (session.endReason ? ' · ended: ' + words(session.endReason) : '')));
  const grid = el('div', 'insight-grid');
  grid.append(tile('Overall assurance', assurance?.overall_assurance), tile('Model behaviour', assurance?.model_behaviour), tile('Platform enforcement', assurance?.platform_enforcement));
  panel.append(grid);
  const criteria = Object.entries(assurance?.criteria ?? {}).filter(([, c]) => c.applicable);
  if (criteria.length) {
    const table = el('table'), head = el('tr');
    for (const h of ['Criterion', 'Verdict', 'Source', 'Reasons']) head.append(el('th', '', h));
    table.append(head);
    for (const [id, c] of criteria) {
      const tr = el('tr');
      for (const v of [id, words(c.verdict), words(c.source), (c.reason_codes ?? []).map(words).join(', ')]) tr.append(el('td', '', v));
      table.append(tr);
    }
    panel.append(table);
  }
  if (session.playbackMode === 'streamed') panel.append(el('p', 'muted', 'Streamed playback bypassed the gate, so AUDIO-01 is at best untestable. Rerun gated to assess platform enforcement.'));
  const actions = el('div', 'actions');
  const open = el('a', '', 'Open full run evidence →'); open.href = './runs.html?run=' + encodeURIComponent(session.runId);
  const other = session.path === 'failure' ? 'happy' : 'failure';
  actions.append(open, button('Test the ' + (other === 'failure' ? 'challenge' : 'happy') + ' path', () => start(other, session), 'primary'),
    button('Repeat this path', () => start(session.path, session)), button('New session', () => { s = null; root.hidden = true; $('session').hidden = true; $('setup').hidden = false; $('start').disabled = false; $('start-status').textContent = ''; loadHistory(); }));
  panel.append(actions);
  root.append(panel);
  revealResult(root);
}

// ---------------------------------------------------------------- history
async function loadHistory() {
  const section = $('history-section'), root = $('history');
  let items = [];
  try { items = (await api(token, '/api/v1/manual-sessions?limit=10')).items ?? []; } catch { section.hidden = true; return; }
  section.hidden = false; root.replaceChildren();
  if (!items.length) { root.append(el('p', 'muted', 'No manual sessions yet. Finished sessions appear here with their verdicts.')); return; }
  const table = el('table'), head = el('tr');
  for (const h of ['When', 'Journey', 'Path', 'Provider', 'Playback', 'Verdict', 'Run']) head.append(el('th', '', h));
  table.append(head);
  for (const item of items) {
    const r = historyRow(item, catalogue.items), tr = el('tr');
    const when = el('td'); when.append(el('span', '', relativeTime(item.started_at) || 'Unknown'));
    const verdict = el('td'); verdict.append(el('span', 'chip verdict-' + (r.verdict ?? r.state), r.verdict ? words(r.verdict) : words(r.state) + ' · not assessed'));
    const run = el('td'), link = el('a', '', shortId(r.runId)); link.href = './runs.html?run=' + encodeURIComponent(r.runId); run.append(link);
    tr.append(when, el('td', '', r.journey), el('td', '', r.path), el('td', '', providerName(item.provider_config_id)), el('td', '', r.playback), verdict, run);
    table.append(tr);
  }
  root.append(scrollRegion(Object.assign(el('div', 'table-scroll'), {}), 'Manual session history table'));
  root.lastChild.append(table);
}

// ---------------------------------------------------------------- wiring
$('challenge').addEventListener('change', e => { challengeId = e.target.value; renderPreview(); });
async function connect() {
  token = $('token').value;
  try {
    catalogue = await api(token, '/api/v1/manual/journeys');
    journeyId = journeyId ?? catalogue.items[0]?.journey_id;
    setConnected(true); sessionStorage.setItem('harness-operator-token', token);
    $('error').textContent = ''; $('setup').hidden = false;
    renderSetup(); loadHistory();
  } catch (e) { setConnected(false); $('error').textContent = friendlyError(e); }
}

$('auth').addEventListener('submit', e => { e.preventDefault(); connect(); });
for (const input of document.querySelectorAll('input[name="path"], input[name="playback"]')) input.addEventListener('change', renderPreview);
$('start').addEventListener('click', () => start());
$('pause').addEventListener('click', togglePause);
$('mute').addEventListener('click', () => control(s.muted ? 'unmute' : 'mute'));
$('mode').addEventListener('click', toggleMode);
$('stop').addEventListener('click', stop);
$('tab-happy').addEventListener('click', () => { s.viewing = 'happy'; renderGuide(); });
$('tab-failure').addEventListener('click', () => { s.viewing = 'failure'; renderGuide(); });
$('composer').addEventListener('submit', e => { e.preventDefault(); sendText($('message').value); $('message').value = ''; });
$('message').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('composer').requestSubmit(); } });
window.addEventListener('beforeunload', () => { if (s && !s.ended) send({type: 'stop'}); });
try { const saved = sessionStorage.getItem('harness-operator-token'); if (saved) { $('token').value = saved; connect(); } } catch { /* no saved token */ }
