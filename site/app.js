const $ = (id) => document.getElementById(id);
const escapeHTML = (value) => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = value => value ? new Date(value + 'T12:00:00Z').toLocaleDateString('en-GB', {day:'numeric', month:'short', year:'numeric', timeZone:'UTC'}) : 'Undated';
const size = value => value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(0)} KB` : `${(value / 1048576).toFixed(1)} MB`;
const href = path => path.startsWith('https://') ? path : path.split('/').map(encodeURIComponent).join('/');
fetch('site/catalogue.json').then(response => {if (!response.ok) throw new Error('Catalogue unavailable'); return response.json();}).then(data => {
  const latest = data.features[0];
  $('stats').innerHTML = `<span><strong>${data.entries.length}</strong> retained files</span><span><strong>${data.entries.filter(e => e.kind === 'Web report').length}</strong> web reports & listening pages</span><span>Latest dated material <strong>${date(data.latest)}</strong></span>`;
  if (latest) $('featured').innerHTML = `<article class="hero-card"><div><p class="eyebrow">LATEST REPORT <span> / </span> ${escapeHTML(date(latest.date))}</p><h3>${escapeHTML(latest.title)}</h3><p>${escapeHTML(latest.description)}</p><div class="actions"><a class="primary" href="${href(latest.path)}">Read the assessment <span>↗</span></a><a href="${href(latest.path.replace('report.html', 'prompts.json'))}">Recorded prompts ↗</a><a href="${href(latest.path.replace('report.html', 'report.json'))}">Report data ↗</a></div></div><div class="signal" aria-hidden="true">${[20,36,64,42,90,60,100,76,42,65,30,48,20].map(h => `<i style="height:${h}%"></i>`).join('')}</div></article>`;
  $('cards').innerHTML = data.features.slice(1).filter(item => item.path !== 'executive-special.html').slice(0, 6).map(item => `<article class="card"><div class="card-meta"><span class="tag">${escapeHTML(item.status)}</span><time>${date(item.date)}</time></div><h3><a href="${href(item.path)}">${escapeHTML(item.title)} <span>↗</span></a></h3><p>${escapeHTML(item.description)}</p></article>`).join('');
  [...new Set(data.entries.map(e => e.kind))].sort().forEach(kind => {const option = document.createElement('option'); option.value = kind; option.textContent = kind; $('kind').append(option);});
  let limit = 10;
  function render() {
    const words = $('search').value.toLowerCase().trim().split(/[\s/_-]+/).filter(Boolean);
    const entries = data.entries.filter(e => (!$('kind').value || e.kind === $('kind').value) && words.every(word => `${e.path} ${e.kind}`.toLowerCase().includes(word)));
    entries.sort((a,b) => $('sort').value === 'name' ? a.path.localeCompare(b.path) : $('sort').value === 'oldest' ? a.date.localeCompare(b.date) || a.path.localeCompare(b.path) : b.date.localeCompare(a.date) || a.path.localeCompare(b.path));
    $('count').textContent = `${entries.length} files${entries.length > limit ? ` · showing ${limit}` : ''}`;
    $('results').innerHTML = entries.length ? entries.slice(0,limit).map(e => `<a class="file" href="${escapeHTML(href(e.url))}"><span class="file-kind">${escapeHTML(e.kind)}</span><span class="file-name">${escapeHTML(e.path.split('/').pop())}<small>${escapeHTML(e.path)}</small></span><span class="file-date">${date(e.date)}</span><span class="file-size">${size(e.size)}</span><span aria-hidden="true">↗</span></a>`).join('') : '<p class="empty">No files match. Try fewer search terms or another file type.</p>';
    $('more').hidden = entries.length <= limit;
  }
  ['search','kind','sort'].forEach(id => $(id).addEventListener(id === 'search' ? 'input' : 'change', () => {limit = 10; render();}));
  $('more').addEventListener('click', () => {limit += 10; render();});
  render();
}).catch(error => { $('stats').textContent = 'The archive could not be loaded. Please reload the page.'; $('results').innerHTML = '<p><a href="https://github.com/Ready2k/Voice_harness/tree/main/artifacts">Browse the artifacts on GitHub ↗</a></p>'; console.error(error); });
