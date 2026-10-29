'use strict';
(() => {
  const data = JSON.parse(document.getElementById('setup-data').textContent);
  const $ = id => document.getElementById(id);
  const node = (tag, text) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el; };
  const display = value => value == null || value === 'not_recorded' ? 'Not recorded' : typeof value === 'string' ? value : JSON.stringify(value);
  const json = value => JSON.stringify(value, null, 2);
  const link = (label, path, download = false) => { const a = node('a', label); a.href = path; if (download) a.download = ''; return a; };
  const rows = values => values.map(v => `${display(v.value)} — ${v.observations} observations`).join('\n');
  function detail(parent, title, text, copy = false) {
    const d = node('details'); d.append(node('summary', title));
    if (copy) {
      const b = node('button', 'Copy text'); b.type = 'button';
      b.addEventListener('click', async () => { try { await navigator.clipboard.writeText(text); b.textContent = 'Copied'; } catch { b.textContent = 'Copy unavailable — select the text below'; } }); d.append(b);
    }
    d.append(node('pre', text)); parent.append(d);
  }
  function cellRow(parent, values, heading = false) {
    const tr = node('tr'); values.forEach((value, i) => { const c = node(heading && i === 0 ? 'th' : 'td', value); if (heading && i === 0) c.scope = 'row'; tr.append(c); }); parent.append(tr);
  }
  const expSelect = $('experiment'), providerSelect = $('provider');
  data.forEach(e => { const o = node('option', e.label); o.value = e.id; expSelect.append(o); });
  const initial = new URLSearchParams(location.search);
  if (data.some(e => e.id === initial.get('experiment'))) expSelect.value = initial.get('experiment');
  function experiment() { return data.find(e => e.id === expSelect.value); }
  function populateProviders(preferred) {
    providerSelect.replaceChildren(); experiment().providers.forEach(p => { const o = node('option', p.name); o.value = p.id; providerSelect.append(o); });
    if (experiment().providers.some(p => p.id === preferred)) providerSelect.value = preferred;
  }
  function render() {
    const e = experiment(), p = e.providers.find(p => p.id === providerSelect.value), b = p.binding;
    const query = new URLSearchParams({experiment:e.id,provider:p.id}); history.replaceState(null, '', `${location.pathname}?${query}`);
    $('selection-status').textContent = `${e.label} · Assessment revision: ${e.generated_at} · ${p.name}`;
    $('comparison').replaceChildren(); e.providers.forEach(v => cellRow($('comparison'), [v.name, display(v.config?.model_id), v.binding.profile, display(v.config?.voice), display(v.config?.sampling_policy), String(v.observations)]));
    $('experiment-links').replaceChildren(link('Open associated results ↗', e.results), link('Run notes ↗', e.notes));
    $('limitations').replaceChildren(); if (e.limitations.length) { const list=node('ul'); e.limitations.forEach(l=>list.append(node('li',l))); $('limitations').append(list); }
    $('provider-title').textContent = p.name;
    $('badges').replaceChildren(); [b.profile, b.vendor_tuned ? 'Provider-tuned instructions' : 'No vendor-tuning flag', `${p.observations} observations`].forEach(t=>{const n=node('span',t);n.className='status';$('badges').append(n);});
    $('settings').replaceChildren();
    cellRow($('settings'), ['Model label (report)',display(p.config?.model_id)]);
    cellRow($('settings'), ['Delivered model roles', b.delivered ? b.delivered.models.map(m=>`${m.role}: ${display(m.model_id)}`).join('; ') : 'No separate model-role delivery record']);
    const labels = {voice:'Output voice',voice_source:'Voice setting source',sampling_policy:'Sampling policy',sampling_overrides:'Sampling overrides sent',output_limits:'Output limits',stored_agent_temperature:'Stored agent temperature',provider_input_rate:'Provider input sample rate (Hz)',client_truncation:'Client truncation',commit_delay_ms:'Commit delay (ms)',confirmation_delivery:'Confirmation delivery',context_channel:'Context channel',agent_llm:'Stored agent LLM (if recorded)'};
    for (const [key,label] of Object.entries(labels)) cellRow($('settings'),[label,rows(p.settings[key])]);
    cellRow($('settings'), ['Turn control',rows(p.turn_control)]); cellRow($('settings'),['Audio conditions',rows(p.audio_profiles)]);
    const prompts=$('prompts'); prompts.replaceChildren();
    detail(prompts, 'Full bound instructions',b.instructions,true);
    detail(prompts,'Lines shared by every provider in this experiment',e.common_lines.join('\n'));
    detail(prompts,'Selected provider: lines not shared by every provider',p.differences.join('\n') || 'No differing non-empty lines.');
    if (b.delivered) {
      prompts.append(node('p',`Recorded adapter wrapper: ${b.delivered.wrapper_id}. The following are the exact delivered instructions retained for each model role.`));
      for (const m of b.delivered.models) detail(prompts,`${m.role} model · ${display(m.model_id)} · delivered instructions`,m.instructions,true);
    } else prompts.append(node('p','No separate delivered-to-model record is present. The full bound instructions above are retained; no additional wrapper is inferred.'));
    detail(prompts,`Tool schemas (${b.tools.length} tools)`,json(b.tools),true);
    const assessment=$('assessment'); assessment.replaceChildren();
    const assessorTable=node('table'); assessorTable.className='setting-table'; const assessorRows=node('tbody'); assessorTable.append(assessorRows);
    cellRow(assessorRows,['ASR models',p.asr.map(v=>`${v.value?.length ? v.value.map(a=>`${display(a.vendor)} / ${display(a.model)}`).join('; ') : 'No ASR configuration listed'} — ${v.observations} observations`).join('\n')]);
    cellRow(assessorRows,['Judge models',p.judges.map(v=>`${v.value?.length ? v.value.map(j=>`${display(j.model_id)} (${display(j.status)})`).join('; ') : 'No judge configuration listed'} — ${v.observations} observations`).join('\n')]);
    assessment.append(assessorTable);
    for (const [key,label] of [['asr','Independent ASR configurations'],['judges','Judge configurations and status'],['assessment_disclosures','Assessment disclosures']]) detail(assessment,label,json(p[key]));
    const verification=$('verification'); verification.replaceChildren();
    const names={instructions_hash_matches:'Instruction SHA-256',tools_hash_matches:'Tool-schema SHA-256',requested_instructions_match_binding:'Requested instructions vs. saved binding',requested_tools_match_binding:'Requested tools vs. saved binding',delivered_hashes_match:'Delivered instruction SHA-256'};
    const checks=node('ul'); for (const [key,value] of Object.entries(p.checks)) checks.append(node('li',`${names[key]}: ${value === null ? 'No separate delivery record' : value ? 'Match' : 'MISMATCH — inspect source files'}`)); verification.append(checks);
    detail(verification,'Recorded prompt and tool hashes',json({instructions_sha256:b.instructions_sha256,tools_sha256:b.tools_sha256,delivered:b.delivered?.models.map(m=>({role:m.role,model_id:m.model_id,instructions_sha256:m.instructions_sha256}))}));
    const sources=$('sources'); sources.replaceChildren(); for (const [key,source] of Object.entries(e.sources)) {const li=node('li');li.append(link(`Download ${key} JSON`,source.path,true),node('code',`Source: ${source.path}`),node('code',`File SHA-256: ${source.sha256}`));sources.append(li);}
  }
  populateProviders(initial.get('provider')); render();
  expSelect.addEventListener('change',()=>{populateProviders(providerSelect.value);render();}); providerSelect.addEventListener('change',render);
})();
