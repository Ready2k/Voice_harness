const $ = id => document.getElementById(id);
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const audio = $('audio');
let selected = null, queue = [], position = -1;
function controls() {
  $('previous').disabled = position <= 0;
  $('next').disabled = position < 0 || position >= queue.length - 1;
  document.querySelectorAll('.sample').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.id === selected?.id)));
}
async function play(clip) {
  selected = clip; position = queue.findIndex(c => c.id === clip.id);
  $('now-title').textContent = clip.provider;
  $('now-detail').textContent = clip.detail;
  $('source-link').href = clip.path; $('source-link').hidden = false;
  $('playback-status').textContent = '';
  audio.src = clip.path; audio.playbackRate = Number($('speed').value); controls();
  try { await audio.play(); } catch (error) { if (selected === clip && error.name !== 'AbortError') $('playback-status').textContent = 'Playback could not start. Use the player’s play button or open the source file.'; }
}
$('speed').addEventListener('change', () => {audio.playbackRate = Number($('speed').value);});
$('previous').addEventListener('click', () => {if(position > 0) play(queue[position-1]);});
$('next').addEventListener('click', () => {if(position < queue.length-1) play(queue[position+1]);});
audio.addEventListener('ended', () => {if($('autonext').checked && position < queue.length-1) play(queue[position+1]);});
audio.addEventListener('error', () => {$('playback-status').textContent='This recording could not be loaded. Try opening the source file.';});

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
