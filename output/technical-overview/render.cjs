// Renders the 10 business-overview slides to frames/NN.png (1920x1080). Playwright + local Chrome.
const fs = require('fs'), path = require('path');
const {chromium} = require('/Users/jamescregeen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = __dirname; fs.mkdirSync(path.join(root, 'frames'), {recursive: true});
const INK = '#071D29', PAPER = '#EDF4EE', TEAL = '#087C68', MINT = '#74E8C0', MUTED = '#4E686B';
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
const bars = (rows, max, fmt) => `<div class="bars">${rows.map(([n, v]) =>
  `<div class="row"><div class="name">${esc(n)}</div><div class="track"><div class="bar" style="width:${(v / max * 100).toFixed(1)}%"></div></div><div class="val">${fmt(v)}</div></div>`).join('')}</div>`;
const big = (n, l) => `<div class="stat"><strong>${esc(n)}</strong><span>${esc(l)}</span></div>`;
const node = (t, sub, cls = '') => `<div class="node ${cls}"><b>${esc(t)}</b><span>${esc(sub)}</span></div>`;
const arrow = `<svg class="arr" viewBox="0 0 70 30"><path d="M2 15H58M46 5l12 10-12 10" fill="none" stroke="${TEAL}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const darr = `<svg class="arr" viewBox="0 0 70 30"><path d="M12 15H58M24 5L12 15l12 10M46 5l12 10-12 10" fill="none" stroke="${TEAL}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const wave = Array.from({length: 64}, (_, i) => { const h = 30 + 150 * Math.abs(Math.sin(i * 0.37) * Math.cos(i * 0.11 + 1)); return `<rect x="${i * 13}" y="${200 - h / 2}" width="7" height="${h.toFixed(0)}" rx="3.5"/>`; }).join('');
const chain = (items, cls = '') => `<div class="chain ${cls}">${items.map((x, i) => `<div class="blockc">${esc(x)}</div>${i < items.length - 1 ? '<em>⟶</em>' : ''}`).join('')}</div>`;
const pill = (t, cls = '') => `<span class="pillx ${cls}">${esc(t)}</span>`;
const lat = [['Google Gemini Live', 1166, 1065], ['ElevenLabs Agents', 1383, null], ['Amazon Nova Sonic', 1442, 934], ['OpenAI GPT-Live', 1464, null], ['OpenAI GPT-Realtime', 1654, 932], ['xAI Grok Voice', 2073, 1154]];
const scenes = [
 {dark: 1, tag: 'VOICE HARNESS · TECHNICAL OVERVIEW', h: 'How do you test a voice agent that talks and acts?', sub: 'Architecture, controls, evidence, fairness, results and limits.',
  body: `<svg class="wave" viewBox="0 0 832 400" width="832" height="400"><g fill="${MINT}" opacity=".85">${wave}</g></svg>`},
 {tag: 'WHY TEXT-ONLY TESTING MISSES IT', h: 'The customer hears it before a test can read it.',
  body: `<div class="tl">
    <div class="tl-axis"><span>time →</span></div>
    <div class="tl-row"><div class="tl-name">What the customer hears<small>audio stream</small></div><div class="tl-track">
      <div class="seg s1" style="left:2%;width:30%">“Sure, I can help with that.”</div>
      <div class="seg s2" style="left:34%;width:36%">“I’ve cancelled your payment.”</div></div></div>
    <div class="tl-row"><div class="tl-name">What the service does<small>tool call, same stream</small></div><div class="tl-track">
      <div class="seg tool" style="left:50%;width:30%">cancel_payment( … )</div></div></div>
    <div class="tl-row"><div class="tl-name">What a text-only test sees<small>transcript, after the fact</small></div><div class="tl-track">
      <div class="seg tx" style="left:72%;width:26%">“…cancelled your payment.”</div></div></div>
    <div class="tl-ov"><div class="gapband" style="left:44%;width:28%"><span>unchecked gap</span></div><div class="vline" style="left:44%"></div><div class="vline" style="left:72%"></div>
     <div class="vlab" style="left:44%">Customer hears “cancelled”</div><div class="vlab" style="left:72%">Transcript first available</div></div>
   </div>
   <div class="gapbox">Unchecked speech and actions reach the customer in this gap. Only a harness that controls audio release and tool execution can test it.</div>
   <div class="difs"><b>Services also differ on:</b> ${['interruption', 'truncation', 'tool handling', 'usage reporting', 'transcripts'].map(x => `<span>${x}</span>`).join('')}<em>So capabilities need evidence, not vendor claims.</em></div>`},
 {tag: 'THE HARNESS', h: 'A controlled world around the service under test.',
  body: `<div class="arch">
    <div class="r">${node('Scenario runner', 'synthetic caller · 20 ms chunks', 'soft')}${darr}${node('Provider adapter', 'closed events · native payload hashes', 'soft')}${darr}${node('Voice service', 'system under test · external', 'hot dashed')}</div>
    <div class="r mid">${node('Orchestrator', 'run lifecycle and event routing', 'soft wide')}</div>
    <div class="r two"><div class="path"><h5>ACTION PATH</h5>${chain(['Guardrail', 'MCP gateway', 'Synthetic bank'], 'sm')}</div><div class="path"><h5>AUDIO PATH</h5>${chain(['Approval buffer', 'ASR + policy', 'Playback gate'], 'sm')}</div></div>
    <div class="bar">Evidence ledger: append-only, hash-chained, signed export</div></div>`},
 {tag: 'CONTROL 1 · ACTION PATH', h: 'Nothing touches the bank until it is allowed.',
  body: `<div class="flow">${node('Tool proposal', 'from the voice service', 'soft')}${arrow}${node('Guardrail', 'identity + confirmation state', 'gate')}${arrow}${node('MCP gateway', 'correlated request / result', 'soft')}${arrow}${node('Synthetic bank', 'state isolated per run', 'soft')}</div>
   <div class="cards three thin">
    <div class="card deny"><b>✕ Denied before execution</b><p>Protected calls fail unless identity and confirmation state permit them.</p></div>
    <div class="card"><b>Single-use approvals</b><p>Confirmations and tokens are consumed transactionally; replays are refused.</p></div>
    <div class="card"><b>Results return via the adapter</b><p>The service only ever sees what the gateway returns.</p></div></div>`},
 {tag: 'CONTROL 2 · AUDIO PATH', h: 'Audio is held until policy releases it.',
  body: `<div class="flow">${node('Provider audio', 'enters the buffer', 'soft')}${arrow}${node('Approval buffer', 'immutable units', 'gate')}${arrow}${node('ASR + policy', 'independent of the provider', 'soft')}${arrow}${node('Playback gate', 'release recorded', 'hot')}</div>
   <div class="unit"><div class="uh">One approval unit</div><div class="ur"><span>audio hash</span><span>sample range</span><span>generation epoch</span><span>assessment epoch</span></div>
   <div class="ut">Release token binds unit, hash, client session, epochs and expiry. Single use. A stale or mismatched approval cannot release.</div></div>`},
 {tag: 'EVIDENCE', h: 'Every step is chained, signed and verifiable offline.',
  body: `<div class="ledger2"><div class="hc">${['run start', 'tool call', 'audio released', 'verdict'].map((x, i) => `<div class="hb"><b>${x}</b><small>hash(prev + this)</small></div>${i < 3 ? '<em>🔗</em>' : ''}`).join('')}</div>
   <div class="flow">${node('Canonical manifest', 'RFC 8785 bytes', 'soft')}${arrow}${node('Ed25519 signature', 'key id in the manifest', 'hot')}${arrow}${node('Offline verifier', 'trusted key ring, out of band', 'soft')}</div></div>
   <div class="cards two thin"><div class="card"><b>Fail closed</b><p>Evidence-write failure blocks tool calls and audio release.</p></div><div class="card"><b>Missing is not a pass</b><p>No evidence means untestable. Purges leave tombstones.</p></div></div>`},
 {tag: 'WHO DECIDES WHAT', h: 'Rules score everything. AI judges one criterion.',
  body: `<div class="stack"><div class="layer l1"><b>Deterministic rules + independent speech recognition</b><span>decide every scored criterion</span></div></div>
   <div class="pills">${['IDENTITY-01', 'FACT-01', 'CONFIRM-01', 'AUDIO-01', 'SCENARIO-PROPOSAL-01'].map(x => pill(x, 'solid')).join('')}</div>
   <div class="split2" style="margin-top:34px"><div class="card"><b>Model verdict | Platform verdict</b><p>Behaviour and protection are reported separately. A blocked misstatement is still a model failure.</p></div>
   <div class="card dashedc"><b>CARE-01 · customer care</b><p>The only judge-decided criterion. Outside the verdict and the quality score. Two judges reviewed every run; neither changed a scored number. No calibration approved.</p></div></div>`},
 {tag: 'FAIRNESS · BIAS FOUND AND FIXED', h: 'The harness was biased. We found it and fixed it.',
  body: `<div class="split"><div style="flex:1"><div class="cap">26 October run: first audio as scored (schematic)</div>` +
   [['OpenAI Realtime', 1], ['xAI Grok Voice', 1], ['Google Gemini Live', 1], ['Amazon Nova Sonic', 0], ['ElevenLabs Agents', 0]].map(([n, c]) => `<div class="brow"><div class="name">${n}</div><div class="btrack"><div class="bb"></div>${c ? '<div class="pen">+1.2 s</div>' : ''}</div></div>`).join('') + `<div class="cap" style="margin-top:12px">Trailing silence was charged to the three explicit-commit services. Nova ranked first.</div></div>
   <div class="fixes"><div class="fh">Fixes</div>${['First audio anchored at the recorded end of speech', 'Common 500 ms delay before explicit commits', 'Latency out of the ranking: turn control still differs', 'Quality-only standings: ties, not first place'].map(x => `<div class="fx">✓ ${x}</div>`).join('')}</div></div>`,
  note: 'Bias review of 27 October, remediated 28 October. After the fixes the five services are tied on quality.'},
 {tag: 'PROCESS AND DISCLOSURE', h: 'Rule-based reruns. Asymmetries stay visible.',
  body: `<div class="split"><div style="flex:1"><div class="chain stk">${['Prompts frozen when scoring starts', 'Run: failed attempts retried, max 2', 'Rerun only if a ledger rule shows a fault outside the model', 'Same rule for every provider; replaced sample and cost kept'].map((x, i) => `<div class="blockc big">${x}</div>${i < 3 ? '<em class="dn">▼</em>' : ''}`).join('')}</div></div>
   <div class="fixes" style="width:640px"><div class="fh">Disclosed, not removed</div>${['Voices differ (some have no British option)', 'Nova gets confirmations as an interactive turn', 'ElevenLabs agent runs a Gemini model', 'GPT-Live: earlier prompt, carried over', '10 cells rerun after rate limits and an adapter fault'].map(x => `<div class="fx">${x}</div>`).join('')}</div></div>`,
  note: 'A result a service simply got wrong is never rerun. Every rerun cell is listed in the run notes.'},
 {tag: 'RESULTS', h: 'No prohibited actions. Different outcomes elsewhere.',
  body: `<div class="kpis"><div class="kpi"><strong>0</strong><span>prohibited actions in 600 runs</span></div><div class="kpi"><strong>29–36</strong><span>of 36 eligible requests completed</span></div><div class="kpi"><strong>100%</strong><span>of decided spoken-fact checks passed (17–21 per service)</span></div><div class="kpi"><strong>≈8×</strong><span>median voice cost spread</span></div></div>
   <div class="tie">Quality: read the next slide</div>`,
  note: 'Five services, 40 situations, 3 repetitions, 29 October standard prompt, final report after reruns. Cost: voice usage only, partial pricing coverage.'},
 {tag: 'QUALITY AND STATISTICS', h: 'A tie, not a ranking.',
  body: `<div class="split"><div style="flex:1.5"><div class="cap">Quality score (excludes latency) with 95% interval</div><div class="iv">` +
   [['ElevenLabs Agents', 99.61, 98.68, 100], ['xAI Grok Voice', 99.49, 98.47, 100], ['Google Gemini Live', 99.27, 98.36, 99.95], ['OpenAI GPT-Realtime', 98.53, 97.06, 100], ['Amazon Nova Sonic', 97.55, 94.36, 99.67]]
   .map(([n, v, lo, hi]) => { const p = x => ((x - 94) / 6 * 100).toFixed(2); return `<div class="irow"><div class="name">${n}</div><div class="itrack"><div class="ibar" style="left:${p(lo)}%;width:${(p(hi) - p(lo)).toFixed(2)}%"></div><div class="idot" style="left:${p(v)}%"></div></div><div class="val">${(v + 0.0001).toFixed(1)}%</div></div>`; }).join('') +
   `<div class="irow axis"><div class="name"></div><div class="itrack">${[94, 96, 98, 100].map(x => `<span style="left:${((x - 94) / 6 * 100)}%">${x}%</span>`).join('')}</div><div class="val"></div></div></div></div>
   <div class="fixes" style="width:520px"><div class="fx"><b>120</b> paired runs per pair (40 scenarios × 3)</div><div class="fx"><b>0 of 10</b> pairs separable after Holm correction</div><div class="fx"><b>≈300–500</b> runs needed to confirm a 2-point gap (80% power)</div></div></div>`,
  note: 'Intervals: bootstrap resampling whole scenarios, the same draw for every provider. Power: McNemar on the assurance pass rate.'},
 {tag: 'LATENCY', h: 'Much of the wait is deciding the caller is done.',
  body: `<div class="llegend"><span><i class="k1"></i> Waiting to decide the caller finished</span><span><i class="k2"></i> Everything after: thinking + first audio</span><span><i class="k3"></i> Split not reported</span></div>
   <div class="cap">Median time from the caller finishing to first audio, plain answers</div><div class="latrows">` +
   lat.map(([n, t, e]) => `<div class="lrow"><div class="name">${n}</div><div class="ltrack"><div class="lt1 ${e ? '' : 'nosplit'}" style="width:${(t / 2200 * 100).toFixed(1)}%">${e ? `<div class="lt2" style="width:${(e / t * 100).toFixed(1)}%"><span>wait ${(e / 1000).toFixed(2)} s</span></div><span class="rest">reply</span>` : '<span class="rest">split not reported</span>'}</div></div><div class="val">${(t / 1000).toFixed(2)} s</div></div>`).join('') + `</div>`,
  note: '497 timed turns, each service on its own end-of-speech detection. One location, concurrent providers, small plain-turn samples: no speed ranking. Wait and total are separate medians, so they do not add exactly. The Gemini split comes from a supplementary session.'},
 {tag: 'LIMITS', h: 'Where the evidence stops.',
  body: `<div class="lim">${[['One scripted caller voice', 'no accents or interruptions in the main results'], ['Small samples', '40 scenarios × 3 repetitions; intervals overlap'], ['One transcriber', 'drives fact and safe-speech checks; same vendor as one tested service'], ['Judges not calibrated', 'identities masked, but reply style can still give a service away'], ['Not like for like', 'turn control, voices and Nova confirmations differ; GPT-Live carried over'], ['No baseline yet', 'Amazon Connect and internal platforms unmeasured; live runs host-run']].map(([t, d]) => `<div class="lc"><b>${t}</b><span>${d}</span></div>`).join('')}</div>`},
 {dark: 1, tag: 'NEXT', h: 'Same contract, new baselines.',
  body: `<div class="road4">
    <div class="step"><b>1</b><h4>New adapters</h4><p>Amazon Connect and internal agentic platforms behind the same adapter contract.</p></div><div class="chev">›</div>
    <div class="step"><b>2</b><h4>Same scenarios</h4><p>Baseline them against the prototypes on identical evidence.</p></div><div class="chev">›</div>
    <div class="step"><b>3</b><h4>Trust the judges</h4><p>Calibrate on real transcripts and complete human validation.</p></div><div class="chev">›</div>
    <div class="step"><b>4</b><h4>Gate regressions</h4><p>Wider callers and lines; deterministic core in CI, no credentials.</p></div></div>`},
];
const css = d => `*{box-sizing:border-box}body{margin:0;width:1920px;height:1080px;position:relative;overflow:hidden;background:${d ? INK : PAPER};color:${d ? PAPER : INK};font-family:Arial,Helvetica,sans-serif}
.brand{position:absolute;left:80px;top:54px;font-weight:800;font-size:28px;letter-spacing:2px}.status{position:absolute;right:80px;top:54px;font-size:22px;letter-spacing:3px;color:${d ? MINT : TEAL}}
.wrap{position:absolute;left:80px;right:80px;top:170px}.tag{font-size:24px;letter-spacing:3px;font-weight:bold;color:${d ? MINT : TEAL};margin-bottom:26px}
h1{margin:0 0 34px;font-weight:900;letter-spacing:-3px;line-height:1.04;font-size:${d ? 96 : 72}px;max-width:1700px}.sub{font-size:40px;line-height:1.3;max-width:1400px;color:${d ? '#ADC4C9' : INK}}
.stats{display:flex;gap:90px;margin:20px 0 50px}.stat strong{display:block;font-size:150px;line-height:1;letter-spacing:-5px;color:${TEAL}}.stat span{font-size:30px;color:${MUTED}}.stats.two .stat strong{font-size:130px}
.chips{display:flex;flex-wrap:wrap;gap:16px}.chips span{border:2px solid ${TEAL};border-radius:40px;padding:14px 28px;font-size:28px;font-weight:bold}
.cards{display:flex;gap:36px;margin-top:20px}.card{flex:1;background:#fff;border:1px solid #ADC4C9;border-radius:16px;padding:44px 40px;min-height:380px}.card b{display:block;font-size:40px;line-height:1.15;margin-bottom:22px}.card p{margin:0;font-size:31px;line-height:1.4;color:${MUTED}}
.hero{display:flex;align-items:baseline;gap:40px;margin-top:30px}.hero strong{font-size:380px;line-height:.9;letter-spacing:-12px;color:${TEAL}}.hero span{font-size:52px;font-weight:bold}
.cap{font-size:28px;color:${MUTED};margin-bottom:26px}.bars{display:flex;flex-direction:column;gap:22px}.row{display:flex;align-items:center;gap:28px}.name{width:380px;font-size:32px;font-weight:bold;text-align:right}.track{flex:1;height:58px}.bar{height:58px;background:${TEAL};border-radius:0 6px 6px 0}.val{width:150px;font-size:38px;font-weight:900}
.verdict{display:inline-block;font-size:44px;font-weight:900;border-left:8px solid ${TEAL};padding-left:24px;margin-bottom:34px}.facts{font-size:30px;color:${MUTED};margin:0}
.list{margin:20px 0 0;padding:0;list-style:none}.list li{font-size:46px;line-height:1.25;margin-bottom:30px;padding-left:46px;position:relative;font-weight:bold}.list li:before{content:'';position:absolute;left:0;top:19px;width:20px;height:20px;border-radius:50%;background:${TEAL}}
.steps{margin:10px 0 0;padding:0;list-style:none;counter-reset:s}.steps li{counter-increment:s;font-size:36px;line-height:1.35;margin-bottom:34px;padding-left:96px;position:relative;max-width:1650px;color:#ADC4C9}.steps li b{color:${PAPER}}
.steps li:before{content:counter(s);position:absolute;left:0;top:-4px;width:64px;height:64px;border-radius:50%;background:${MINT};color:${INK};font-weight:900;font-size:36px;text-align:center;line-height:64px}
.note{position:absolute;left:80px;right:80px;bottom:100px;font-size:26px;line-height:1.4;color:${d ? '#ADC4C9' : MUTED}}.foot{position:absolute;left:80px;bottom:44px;font-size:18px;color:${d ? '#ADC4C9' : MUTED}}.progress{position:absolute;left:0;bottom:0;height:9px;background:#20C997}
.wave{position:fixed;right:80px;bottom:130px}
.matrix{display:flex;flex-direction:column;gap:22px;margin-top:6px}.matrix.tight{gap:14px}.blk{display:flex;align-items:center;gap:26px}.bl{width:330px;text-align:right;font-size:28px;font-weight:bold}
.dots{display:grid;grid-template-columns:repeat(40,18px);gap:5px}.dots i{width:18px;height:18px;border-radius:50%;background:${TEAL}}.dots.ok i{background:${TEAL}}.matrix.tight .dots{grid-template-columns:repeat(40,16px)}.matrix.tight .dots i{width:16px;height:16px}
.legend{display:flex;gap:50px;font-size:24px;color:${MUTED};margin:26px 0 0 356px}.legend .k{display:inline-block;width:16px;height:16px;border-radius:50%;background:${TEAL};vertical-align:-2px;margin-right:8px}
.flow{display:flex;align-items:center;gap:6px;margin:30px 0 56px}.node{flex:1;background:#fff;border:2px solid #ADC4C9;border-radius:18px;padding:34px 24px;text-align:center;min-height:170px}.node b{display:block;font-size:34px;margin-bottom:12px}.node span{font-size:24px;color:${MUTED};line-height:1.3;display:block}
.node.hot{border-color:${TEAL};background:${TEAL};color:#fff}.node.hot span{color:#D8F5EC}.node.gate{border:3px dashed ${TEAL};background:#E1F3EA}.arr{width:60px;flex:none}
.ledger{background:#fff;border:1px solid #ADC4C9;border-radius:18px;padding:34px 40px}.chain{display:flex;align-items:center;gap:10px;margin-bottom:24px}.blockc{flex:1;border:2px solid ${TEAL};border-radius:10px;padding:16px 8px;text-align:center;font-size:24px;font-weight:bold;background:#EDF4EE}.chain em{font-style:normal;color:${TEAL};font-size:34px}.lcap{font-size:28px;line-height:1.4;color:${MUTED}}.lcap b{color:${INK}}
.split{display:flex;align-items:center;gap:80px}
.sqrows{display:flex;flex-direction:column;gap:22px}.srow{display:flex;align-items:center;gap:26px}.srow .name{width:340px}.sq{display:flex;gap:5px}.sq i{width:26px;height:44px;border-radius:5px;border:3px solid #ADC4C9;background:transparent}.sq i.on{background:${TEAL};border-color:${TEAL}}.srow .val{width:170px;font-size:44px;font-weight:900}.val small{font-size:24px;color:${MUTED};font-weight:normal}
.venn{position:relative;width:900px;height:600px;flex:none}.c{position:absolute;top:20px;width:480px;height:480px;border-radius:50%;display:flex;flex-direction:column;justify-content:center}.c strong{font-size:70px;letter-spacing:-2px}.c span{font-size:26px;font-weight:bold}.c1{left:0;background:${TEAL}CC;color:#fff;align-items:flex-start;padding-left:60px}.c2{left:420px;background:#20C997AA;color:${INK};align-items:flex-end;padding-right:60px}.vt{position:absolute;left:0;width:900px;text-align:center;top:520px;font-size:36px;font-weight:900;color:${INK}}
.facts{min-width:520px}.fh{font-size:28px;color:${MUTED};margin-bottom:18px}.fp{display:flex;justify-content:space-between;background:#fff;border:1px solid #ADC4C9;border-radius:12px;padding:14px 24px;margin-bottom:12px;font-size:30px}.fp b{color:${TEAL}}
.mult{width:520px}.mult strong{display:block;font-size:200px;line-height:1;letter-spacing:-6px;color:${TEAL}}.mult span{font-size:30px;line-height:1.3;color:${MUTED}}
.bubble{background:#E1F3EA;border-radius:22px 22px 22px 4px;padding:22px 28px;font-size:30px;margin:6px 0 22px;font-weight:bold}.rec{border:2px dashed #C0392B;border-radius:12px;padding:16px 22px;font-size:28px;margin-bottom:22px}.rec .x{color:#C0392B;font-weight:900;margin-right:10px}
.ranks{display:flex;align-items:center;gap:22px;margin:12px 0 24px}.ranks>div{flex:1}.ranks small{display:block;font-size:22px;color:${MUTED};margin-bottom:10px}.rank div{height:20px;background:${TEAL};border-radius:0 4px 4px 0;margin-bottom:8px}.same{flex:none!important;font-size:60px;color:${TEAL};font-weight:900}.pip{color:${TEAL};letter-spacing:4px;font-weight:900}
.card{min-height:560px}.cards.two .card b{font-size:38px}
.two-col{display:flex;gap:40px;margin-top:16px}.col{flex:1;border-radius:18px;padding:40px 44px;min-height:520px}.col.yes{background:#E1F3EA;border:2px solid ${TEAL}}.col.no{background:#fff;border:2px dashed #ADC4C9}.col h3{margin:0 0 28px;font-size:40px}.col ul{margin:0;padding:0;list-style:none}.col li{font-size:36px;line-height:1.3;margin-bottom:24px;padding-left:56px;position:relative;font-weight:bold}.yes li:before{content:'✓';position:absolute;left:0;color:${TEAL};font-weight:900}.no li:before{content:'○';position:absolute;left:0;color:${MUTED};font-weight:900}
.road{display:flex;align-items:stretch;gap:14px;margin-top:20px}.step{flex:1;background:#0C2A38;border:2px solid #1E5060;border-radius:18px;padding:36px 34px}.step b{display:inline-block;width:64px;height:64px;border-radius:50%;background:${MINT};color:${INK};font-size:36px;line-height:64px;text-align:center}.step h4{margin:22px 0 12px;font-size:36px;line-height:1.2;color:${PAPER}}.step p{margin:0;font-size:28px;line-height:1.4;color:#ADC4C9}.chev{align-self:center;font-size:90px;color:${MINT};font-weight:900}
.base{margin-top:34px;border-top:4px solid ${MINT};padding-top:24px}.base span{font-size:28px;color:${MINT};letter-spacing:2px;font-weight:bold}.base div{display:flex;gap:18px;margin-top:18px}.base i{font-style:normal;border:2px solid ${MINT};border-radius:40px;padding:12px 30px;font-size:30px;font-weight:bold}
.sixth{margin:16px 0 0 356px;border:2px dashed #ADC4C9;border-radius:14px;padding:16px 26px;font-size:26px;line-height:1.35;color:${MUTED};max-width:1180px}.sixth b{color:${INK}}
.side{width:470px;flex:none}.side .big2{white-space:nowrap;font-size:96px;font-weight:900;letter-spacing:-4px;color:${TEAL};line-height:1}.side p{font-size:28px;color:${MUTED};margin:8px 0 26px}.warnbox{border:2px dashed #C0392B;border-radius:12px;padding:18px 22px;font-size:26px;line-height:1.35}
.sq.wide{gap:10px}.sq.wide i{width:56px}

.lanes{margin:30px 0 40px}.lane{display:flex;align-items:center;gap:30px;margin-bottom:22px}.ln{width:300px;text-align:right;font-size:30px;font-weight:bold}.lt{flex:1;position:relative;height:52px;background:#E1F3EA;border-radius:10px}.lt i{position:absolute;top:0;height:52px;border-radius:10px;background:${TEAL}}.b1{left:4%;width:52%}.b2{left:30%;width:52%;background:#7FBFB0!important}.mk{left:32%;width:14px;background:#C0392B!important}.gap{margin-left:330px;font-size:30px;font-weight:bold;color:#C0392B}
.pills{display:flex;flex-wrap:wrap;gap:14px;margin-top:20px}.pillx{border:2px solid ${TEAL};border-radius:40px;padding:12px 26px;font-size:26px;font-weight:bold}.lead{font-size:40px;font-weight:900;margin:34px 0 0}
.arch{display:flex;flex-direction:column;gap:20px;margin-top:6px}.arch .r{display:flex;align-items:center;gap:6px}.arch .node{min-height:120px;padding:22px 20px}.arch .node b{font-size:30px;margin-bottom:8px}.arch .node span{font-size:22px}.node.dashed{border-style:dashed}.arch .mid{justify-content:center}.node.wide{max-width:760px}
.arch .two{gap:30px;align-items:stretch}.path{flex:1;background:#fff;border:1px solid #ADC4C9;border-radius:16px;padding:18px 24px}.path h5{margin:0 0 12px;font-size:22px;letter-spacing:3px;color:${TEAL}}.chain.sm .blockc{font-size:22px;padding:14px 6px}.arch .bar{background:${TEAL};color:#fff;border-radius:14px;padding:18px 28px;font-size:30px;font-weight:bold;text-align:center}
.cards.three .card.deny{border:2px solid #C0392B}.card.deny b{color:#C0392B}.cards.thin .card{min-height:200px}
.unit{background:#fff;border:2px solid ${TEAL};border-radius:18px;padding:30px 36px;margin-top:10px}.uh{font-size:28px;color:${TEAL};font-weight:bold;letter-spacing:2px;margin-bottom:16px}.ur{display:flex;gap:16px;margin-bottom:20px}.ur span{flex:1;background:#E1F3EA;border-radius:10px;padding:18px;text-align:center;font-size:28px;font-weight:bold}.ut{font-size:28px;line-height:1.4;color:${MUTED}}
.ledger2{margin-top:10px}.hc{display:flex;align-items:center;gap:8px;margin-bottom:34px}.hb{flex:1;background:#fff;border:2px solid ${TEAL};border-radius:12px;padding:20px 14px;text-align:center}.hb b{display:block;font-size:28px}.hb small{font-size:22px;color:${MUTED};font-family:monospace}.hc em{font-style:normal;font-size:34px}.ledger2 .flow{margin:0 0 34px}
.stack{display:flex;flex-direction:column;gap:14px;margin:10px 0 28px}.layer{border-radius:14px;padding:22px 32px;display:flex;justify-content:space-between;align-items:center;font-size:30px}.layer b{font-size:34px}.layer span{color:${MUTED};font-size:26px}.l1{background:${TEAL};color:#fff}.l1 span{color:#D8F5EC}.l2{background:#E1F3EA;border:2px solid ${TEAL}}.l3{background:#fff;border:2px dashed #ADC4C9}
.split2{display:flex;gap:30px}.split2 .card{min-height:150px;padding:28px 34px}.split2 .card b{font-size:34px;margin-bottom:10px}
.brow{display:flex;align-items:center;gap:24px;margin-bottom:18px}.brow .name{width:330px;font-size:28px;font-weight:bold;text-align:right}.btrack{flex:1;display:flex;align-items:center;height:48px}.bb{width:36%;height:48px;background:${TEAL};border-radius:0 6px 6px 0}.pen{height:48px;background:repeating-linear-gradient(135deg,#C0392B,#C0392B 8px,#E57A6E 8px,#E57A6E 16px);color:#fff;font-weight:900;font-size:26px;line-height:48px;padding:0 18px;width:20%;border-radius:0 6px 6px 0}
.fixes{width:560px}.fx{background:#fff;border:1px solid #ADC4C9;border-left:6px solid ${TEAL};border-radius:10px;padding:18px 22px;font-size:28px;line-height:1.3;margin-bottom:14px}
.kpis{display:flex;gap:24px;margin:16px 0 30px}.kpi{flex:1;background:#fff;border:1px solid #ADC4C9;border-radius:18px;padding:36px 28px}.kpi strong{display:block;font-size:104px;line-height:1;letter-spacing:-4px;color:${TEAL}}.kpi span{display:block;font-size:28px;line-height:1.3;margin-top:14px;color:${MUTED}}.tie{display:inline-block;font-size:44px;font-weight:900;border-left:8px solid ${TEAL};padding-left:24px}
.latrows{display:flex;flex-direction:column;gap:20px}.lrow{display:flex;align-items:center;gap:26px}.lrow .name{width:340px;font-size:30px;font-weight:bold;text-align:right}.ltrack{flex:1;height:54px}.lt1{height:54px;background:#7FBFB0;border-radius:0 6px 6px 0;position:relative}.lt2{position:absolute;left:0;top:0;height:54px;background:${TEAL};border-radius:0 0 0 0}.lrow .val{width:150px;font-size:38px;font-weight:900}
.lim{display:grid;grid-template-columns:repeat(3,1fr);gap:26px;margin-top:20px}.lc{background:#fff;border:1px solid #ADC4C9;border-top:6px solid ${TEAL};border-radius:14px;padding:30px 30px;min-height:220px}.lc b{display:block;font-size:36px;line-height:1.15;margin-bottom:14px}.lc span{font-size:28px;line-height:1.35;color:${MUTED}}
.road4{display:flex;align-items:stretch;gap:10px;margin-top:20px}.road4 .step{padding:30px 26px}.road4 .step h4{font-size:32px}.road4 .step p{font-size:25px}.road4 .chev{font-size:70px}
.pillx.solid{background:${TEAL};color:#fff}.card.dashedc{border:2px dashed #ADC4C9}.chain.stk{flex-direction:column;align-items:stretch;gap:6px}.blockc.big{font-size:28px;padding:22px 24px;text-align:left}.chain em.dn{text-align:center;font-size:26px}.fx b{color:${TEAL};font-size:34px;margin-right:8px}
.iv{display:flex;flex-direction:column;gap:18px;margin-bottom:30px}.irow{display:flex;align-items:center;gap:26px}.irow .name{width:340px;font-size:30px;font-weight:bold;text-align:right}.itrack{flex:1;position:relative;height:46px;background:linear-gradient(#ADC4C9,#ADC4C9) center/100% 2px no-repeat}.ibar{position:absolute;top:13px;height:20px;background:#7FBFB0;border-radius:10px}.idot{position:absolute;top:5px;width:36px;height:36px;margin-left:-18px;border-radius:50%;background:${TEAL};border:4px solid #EDF4EE}.irow .val{width:150px;font-size:36px;font-weight:900}.irow.axis{margin-top:-6px}.irow.axis .itrack{height:30px;background:none}.irow.axis span{position:absolute;transform:translateX(-50%);font-size:24px;color:${MUTED}}

.tl{position:relative;margin-top:6px}.tl-axis{text-align:right;font-size:24px;color:${MUTED};margin:0 0 6px;padding-right:6px}.tl-row{display:flex;align-items:center;gap:24px;margin-bottom:26px}.tl-name{width:430px;font-size:30px;font-weight:bold;text-align:right;line-height:1.15}.tl-name small{display:block;font-size:22px;color:${MUTED};font-weight:normal}
.tl-track{flex:1;position:relative;height:84px;background:#E1F3EA;border-radius:12px}.seg{position:absolute;top:10px;height:64px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:bold;white-space:nowrap}
.seg.s1{background:${TEAL};color:#fff}.seg.s2{background:${TEAL};color:#fff;outline:4px solid #20C997}.seg.tool{background:#C0392B;color:#fff;font-family:monospace;font-size:26px}.seg.tx{background:#7FBFB0;color:${INK}}
.vline{position:absolute;top:34px;bottom:-4px;width:0;border-left:4px dashed ${INK}}.vlab{position:absolute;top:0;font-size:24px;font-weight:bold;transform:translateX(-50%);color:${INK}}
.gapbox{border-left:8px solid #C0392B;background:#fff;border-radius:0 12px 12px 0;padding:20px 28px;font-size:30px;line-height:1.35;font-weight:bold;margin:14px 0 22px}
.difs{font-size:28px;display:flex;flex-wrap:wrap;gap:12px;align-items:center}.difs span{border:2px solid ${TEAL};border-radius:30px;padding:8px 20px;font-weight:bold}.difs em{font-style:normal;font-weight:900;font-size:34px;flex-basis:100%;margin-top:10px}
.llegend{display:flex;flex-wrap:wrap;gap:34px;font-size:26px;margin:4px 0 22px}.llegend span{display:flex;align-items:center;gap:12px}.llegend i{display:inline-block;width:46px;height:26px;border-radius:5px}.k1{background:${TEAL}}.k2{background:#7FBFB0}.k3{background:repeating-linear-gradient(135deg,#ADC4C9,#ADC4C9 6px,#E1EAE6 6px,#E1EAE6 12px)}
.lt1{position:relative;display:flex;align-items:center}.lt2{height:54px;background:${TEAL};display:flex;align-items:center;justify-content:center;color:#fff;font-size:24px;font-weight:bold;white-space:nowrap;overflow:hidden}.lt1 .rest{flex:1;text-align:center;font-size:24px;font-weight:bold;color:${INK}}.lt1.nosplit{background:repeating-linear-gradient(135deg,#ADC4C9,#ADC4C9 8px,#E1EAE6 8px,#E1EAE6 16px)}
.tl-ov{position:absolute;left:454px;right:0;top:0;bottom:0;pointer-events:none}.tl-ov .vline{top:28px;bottom:6px}.tl-ov .vlab{top:-6px}.gapband{position:absolute;top:34px;bottom:10px;background:rgba(192,57,43,.13)}.gapband span{position:absolute;left:0;right:0;bottom:4px;text-align:center;font-size:22px;font-weight:bold;color:#C0392B}
.latrows{gap:12px!important}.ltrack{height:48px!important}.lt1,.lt2{height:48px!important}.llegend{margin:0 0 14px!important;gap:30px!important;font-size:24px!important}
`;
(async () => {
  const b = await chromium.launch({headless: true, executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const p = await b.newPage({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1});
  for (let i = 0; i < scenes.length; i++) {
    const s = scenes[i];
    await p.setContent(`<html><style>${css(s.dark)}</style><body><div class="brand">VOICE HARNESS</div><div class="status">TECHNICAL OVERVIEW</div>
     <div class="wrap"><div class="tag">${s.tag}</div><h1>${esc(s.h)}</h1>${s.sub ? `<div class="sub">${esc(s.sub)}</div>` : ''}${s.body || ''}</div>
     ${s.note ? `<div class="note">${esc(s.note)}</div>` : ''}<div class="foot">Synthetic test data. Results describe the tested prototype configurations, not production performance.</div>
     <div class="progress" style="width:${((i + 1) / scenes.length * 100).toFixed(1)}%"></div></body></html>`);
    await p.screenshot({path: path.join(root, 'frames', String(i).padStart(2, '0') + '.png')});
  }
  await b.close();
})();
