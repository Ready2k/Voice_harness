const $ = id => document.getElementById(id);
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const audio = $('audio');
let data, selected = null, queue = [], position = -1;
function setQueue(line) {
  queue = data.providers.map(provider => data.clips.find(c => c.provider === provider && c.line === line && c.condition === $('condition').value)).filter(Boolean);
}
function controls() {
  $('previous').disabled = position <= 0;
  $('next').disabled = position < 0 || position >= queue.length - 1;
  document.querySelectorAll('.sample').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.id === selected?.id)));
}
async function play(clip) {
  selected = clip; position = queue.findIndex(c => c.id === clip.id);
  $('now-title').textContent = clip.provider;
  $('now-detail').textContent = clip.detail || `Prompt ${clip.line + 1} · ${clip.condition === 'phone' ? 'Telephone quality' : 'Clean audio'} · ${clip.recorded} · Clip ${clip.id}`;
  $('source-link').href = clip.path; $('source-link').hidden = false;
  $('playback-status').textContent = '';
  audio.src = clip.path; audio.playbackRate = Number($('speed').value); controls();
  try { await audio.play(); } catch (error) { if (selected === clip && error.name !== 'AbortError') $('playback-status').textContent = 'Playback could not start. Use the player’s play button or open the source WAV.'; }
}
function providerDescription(provider) {
  const sample = data.clips.find(c => c.provider === provider);
  const meta = sample.metadata;
  const model = meta?.receipt?.model_id || meta?.configuration?.model_id || 'not recorded';
  const voice = meta?.configuration?.voice || (provider === 'ElevenLabs (George)' ? 'George' : 'not recorded by name');
  return `Model: ${esc(model)}<br>Voice: ${esc(voice)}<br>Recorded: ${esc(sample.recorded)}${meta?.receipt?.backend_model_id ? `<br>Backend: ${esc(meta.receipt.backend_model_id)}` : ''}`;
}
function render() {
  const condition = $('condition').value;
  $('matrix').innerHTML = `<caption>Matched banking scripts by captured provider and voice</caption><thead><tr><th scope="col">Spoken script / prompt</th>${data.providers.map(p => `<th scope="col">${esc(p)}<small>${providerDescription(p)}</small></th>`).join('')}</tr></thead><tbody>${data.lines.map((line,i) => {const available = data.clips.filter(c => c.line === i && c.condition === condition); return `<tr><th scope="row">PROMPT ${String(i+1).padStart(2,'0')}<p>${esc(line)}</p>${available.length ? `<button class="compare-prompt" data-line="${i}">Compare this prompt →</button>` : ''}</th>${data.providers.map(p => {const c = available.find(c => c.provider === p);return `<td>${c ? `<button class="sample" data-id="${c.id}" aria-pressed="${selected?.id === c.id}" aria-label="Play prompt ${i+1}, ${esc(p)}, ${condition}">▶ Play sample<small>${c.seconds.toFixed(1)} seconds · ${c.id}</small></button>` : '<span class="missing">Not recorded</span>'}</td>`;}).join('')}</tr>`;}).join('')}</tbody>`;
  $('library-status').textContent = `${data.clips.filter(c => c.condition === condition).length} available samples`;
  document.querySelectorAll('#matrix .sample').forEach(button => button.addEventListener('click', () => {const clip = data.clips.find(c => c.id === button.dataset.id);setQueue(clip.line);play(clip);}));
  document.querySelectorAll('.compare-prompt').forEach(button => button.addEventListener('click', () => {setQueue(Number(button.dataset.line));$('autonext').checked = true;play(queue[0]);}));
}
$('condition').addEventListener('change', () => {audio.pause();selected=null;queue=[];position=-1;audio.removeAttribute('src');audio.load();$('now-title').textContent='Choose a voice sample';$('now-detail').textContent='Playback stays here while you explore the grid.';$('source-link').hidden=true;$('playback-status').textContent='';render();controls();});
$('speed').addEventListener('change', () => {audio.playbackRate = Number($('speed').value);});
$('previous').addEventListener('click', () => {if(position > 0) play(queue[position-1]);});
$('next').addEventListener('click', () => {if(position < queue.length-1) play(queue[position+1]);});
audio.addEventListener('ended', () => {if($('autonext').checked && position < queue.length-1) play(queue[position+1]);});
audio.addEventListener('error', () => {$('playback-status').textContent='This recording could not be loaded. Try opening the source WAV.';});
fetch('site/voice-library.json').then(r => {if(!r.ok) throw new Error('Manifest unavailable');return r.json();}).then(manifest => {data=manifest;const variants = data.providers.length; $('dataset-summary').textContent = `${data.clips.length} retained clips · ${variants} voice variants. Matched scripts recorded 25 and 28 October 2026; recording dates differ. Representative scripted speech, not per-run bake-off evidence. Delivery can vary with instructions, settings and context.`;render();}).catch(() => {$('library-status').textContent='The library could not be loaded. Reload or open the original listening page linked below.';});

const matrixWrap = document.querySelector('.matrix-wrap');
function updateMatrixNavigation() {
  const remaining = matrixWrap.scrollWidth - matrixWrap.clientWidth - matrixWrap.scrollLeft;
  $('matrix-navigation').hidden = matrixWrap.scrollWidth <= matrixWrap.clientWidth + 1;
  $('voices-left').disabled = matrixWrap.scrollLeft <= 1;
  $('voices-right').disabled = remaining <= 1;
  document.querySelector('#matrix-navigation span').textContent = remaining <= 1 ? 'Last voice column · use ← to compare earlier voices' : 'More voices to the right — including GPT-Live →';
}
$('voices-left').addEventListener('click', () => matrixWrap.scrollBy({left: -350, behavior:'smooth'}));
$('voices-right').addEventListener('click', () => matrixWrap.scrollBy({left: 350, behavior:'smooth'}));
new ResizeObserver(updateMatrixNavigation).observe(matrixWrap);
new MutationObserver(updateMatrixNavigation).observe($('matrix'), {childList:true});
matrixWrap.addEventListener('scroll', updateMatrixNavigation, {passive:true});

fetch('site/voice-conversations.json').then(r => {if(!r.ok) throw new Error('unavailable');return r.json();}).then(manifest => {
  const label = {common: 'Common', tuned: 'Tuned'};
  const clips = manifest.clips.map(c => ({...c, recorded: manifest.recorded, detail: `${manifest.scenarios.find(s => s.id === c.scenario).title} · ${label[c.condition]} prompt · voice ${c.voice} · model ${c.model} · ${c.seconds.toFixed(1)} s · caller left, assistant right · Clip ${c.id}`}));
  const describe = p => { const c = clips.find(x => x.provider === p); return `Model: ${esc(c.model)}<br>Voice: ${esc(c.voice)}<br>Recorded: ${esc(c.recorded)}`; };
  const table = $('conversation-matrix');
  const cell = (p, s) => ['common', 'tuned'].map(k => { const c = clips.find(x => x.provider === p && x.scenario === s.id && x.condition === k); return c ? `<button class="sample" data-convo="${esc(c.id)}" aria-pressed="false" aria-label="Play ${esc(s.title)}, ${esc(p)}, ${label[k]} prompt">▶ ${label[k]}<small>${c.seconds.toFixed(1)} s · ${esc(c.id)}</small></button>` : '<span class="missing">Not recorded</span>'; }).join('');
  table.innerHTML = `<caption>Conversations by scenario and provider, common and tuned prompt side by side</caption><thead><tr><th scope="col">Scenario / caller lines</th>${manifest.providers.map(p => `<th scope="col">${esc(p)}<small>${describe(p)}</small></th>`).join('')}</tr></thead><tbody>${manifest.scenarios.map((s, i) => `<tr><th scope="row">SCENARIO ${String(i + 1).padStart(2, '0')} · ${esc(s.title)}<p>${s.turns.map(t => `“${esc(t)}”`).join('<br>')}</p><p><em>${esc(s.review)}</em></p><button class="compare-prompt" data-scenario="${esc(s.id)}">Compare this scenario →</button></th>${manifest.providers.map(p => `<td class="pair">${cell(p, s)}</td>`).join('')}</tr>`).join('')}</tbody>`;
  table.querySelectorAll('.sample').forEach(button => {
    const clip = clips.find(c => c.id === button.dataset.convo);
    button.dataset.id = clip.id;
    button.addEventListener('click', () => {queue = clips.filter(c => c.scenario === clip.scenario); play(clip);});
  });
  table.querySelectorAll('.compare-prompt').forEach(button => button.addEventListener('click', () => {
    queue = clips.filter(c => c.scenario === button.dataset.scenario);
    $('autonext').checked = true;
    play(queue[0]);
  }));
}).catch(() => {const t = $('conversation-matrix'); if (t) t.innerHTML = '<caption>Conversations are unavailable. Use the blind listening study.</caption>';});
