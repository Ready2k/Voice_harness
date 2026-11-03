// Renders the 10 business-overview slides to frames/NN.png (1920x1080). Playwright + local Chrome.
const fs = require('fs'), path = require('path');
const {chromium} = require('/Users/jamescregeen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = __dirname; fs.mkdirSync(path.join(root, 'frames'), {recursive: true});
const INK = '#071D29', PAPER = '#EDF4EE', TEAL = '#087C68', MINT = '#74E8C0', MUTED = '#4E686B';
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
const bars = (rows, max, fmt) => `<div class="bars">${rows.map(([n, v]) =>
  `<div class="row"><div class="name">${esc(n)}</div><div class="track"><div class="bar" style="width:${(v / max * 100).toFixed(1)}%"></div></div><div class="val">${fmt(v)}</div></div>`).join('')}</div>`;
const big = (n, l) => `<div class="stat"><strong>${esc(n)}</strong><span>${esc(l)}</span></div>`;
const V = ['xAI Grok Voice', 'ElevenLabs Agents', 'Google Gemini Live', 'OpenAI GPT-Realtime', 'Amazon Nova Sonic', 'OpenAI GPT-Live'];
// 720 runs: one column per situation (40), one row per repetition (3), one block per service.
const dotBlock = (label, cls = '') => `<div class="blk">${label ? `<div class="bl">${esc(label)}</div>` : ''}<div class="dots ${cls}">${'<i></i>'.repeat(120)}</div></div>`;
const squares = (n, of) => `<div class="sq${of <= 9 ? " wide" : ""}">${Array.from({length: of}, (_, i) => `<i class="${i < n ? 'on' : ''}"></i>`).join('')}</div>`;
const node = (t, s, cls = '') => `<div class="node ${cls}"><b>${esc(t)}</b><span>${esc(s)}</span></div>`;
const arrow = `<svg class="arr" viewBox="0 0 70 30"><path d="M2 15H58M46 5l12 10-12 10" fill="none" stroke="${TEAL}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const wave = Array.from({length: 64}, (_, i) => { const h = 30 + 150 * Math.abs(Math.sin(i * 0.37) * Math.cos(i * 0.11 + 1)); return `<rect x="${i * 13}" y="${200 - h / 2}" width="7" height="${h.toFixed(0)}" rx="3.5"/>`; }).join('');
const rank = (cls) => `<div class="rank ${cls}">${[100, 88, 76, 62, 48].map(w => `<div style="width:${w}%"></div>`).join('')}</div>`;
const scenes = [
 {dark: 1, tag: 'VOICE AI · BUSINESS OVERVIEW', h: 'Is voice AI ready to talk to customers?', sub: 'How would you know it is safe, helpful and worth the cost? Here is what we found.', big: true,
  body: `<svg class="wave" viewBox="0 0 832 400" width="832" height="400"><g fill="${MINT}" opacity=".85">${wave}</g></svg>`},
 {tag: 'WHAT WE TESTED', h: 'Six services. Forty situations. 720 runs.',
  body: `<div class="matrix">${V.map(v => dotBlock(v)).join('')}</div>
   <div class="legend"><span><i class="k"></i> one recorded run</span><span>across: 40 customer situations</span><span>down: 3 repetitions each</span></div>
   <div class="sixth"><b>One run failed:</b> a GPT-Realtime reply ran past the audio limit and the safety gate stopped it. It was not rerun. All six services used the same standardised prompt.</div>`,
  note: 'OpenAI, Google, xAI, ElevenLabs and Amazon: selected external prototype services, tested outside of work. Synthetic customers and a simulated bank.'},
 {tag: 'WHY THE COMPARISON HOLDS UP', h: 'Same situations. Same bank. Same scoring.',
  body: `<div class="flow">${node('Simulated customer', 'scripted situation', 'soft')}${arrow}${node('Voice service', 'the system under test', 'hot')}${arrow}${node('Policy gate', 'holds speech and tool calls', 'gate')}${arrow}${node('Simulated bank', 'sandboxed tools, no real money', 'soft')}</div>
   <div class="ledger"><div class="chain">${['run start', 'tool call', 'audio released', 'verdict', 'export'].map((x, i) => `<div class="blockc">${x}</div>${i < 4 ? '<em>⟶</em>' : ''}`).join('')}</div>
   <div class="lcap"><b>Tamper-evident evidence ledger.</b> Every step is chained and the export can be verified offline. <b>Missing evidence counts against the result.</b></div></div>`},
 {tag: 'FINDING 1 · PROTECTION', h: 'No prohibited actions in 720 runs.',
  body: `<div class="split"><div class="matrix tight">${dotBlock('', 'ok')}${dotBlock('', 'ok')}${dotBlock('', 'ok')}${dotBlock('', 'ok')}${dotBlock('', 'ok')}${dotBlock('', 'ok')}</div>
   <div class="hero"><strong>0</strong><span>prohibited actions executed</span></div></div>`,
  note: 'Services and controls working together. It does not mean every check passed: failed and unresolved checks are reported.'},
 {tag: 'FINDING 2 · GETTING THE JOB DONE', h: 'Completing the request varied.',
  body: `<div class="cap">Each square is one eligible request. Filled means completed.</div><div class="sqrows">` + [['xAI Grok Voice', 36], ['ElevenLabs Agents', 36], ['Google Gemini Live', 36], ['OpenAI GPT-Realtime', 36], ['Amazon Nova Sonic', 32], ['OpenAI GPT-Live', 29]].map(([n, v]) => `<div class="srow"><div class="name">${n}</div>${squares(v, 36)}<div class="val">${v}<small>/36</small></div></div>`).join('') + `</div>`,
  note: 'Completion checks the expected tool sequence and arguments, not just what was said.'},
 {tag: 'FINDING 3 · QUALITY AND FACTS', h: 'Quality scores are too close to call.',
  body: `<div class="cap">Quality score (excludes latency) with 95% interval</div><div class="iv">` +
   [['Google Gemini Live', 99.63, 99.24, 100], ['xAI Grok Voice', 99.61, 98.68, 100], ['ElevenLabs Agents', 99.61, 98.68, 100], ['Amazon Nova Sonic', 99.35, 96.69, 100], ['OpenAI GPT-Live', 98.86, 95.59, 99.82]]
   .map(([n, v, lo, hi]) => { const p = x => ((x - 94) / 6 * 100).toFixed(2); return `<div class="irow"><div class="name">${n}</div><div class="itrack"><div class="ibar" style="left:${p(lo)}%;width:${(p(hi) - p(lo)).toFixed(2)}%"></div><div class="idot" style="left:${p(v)}%"></div></div><div class="val">${(v + 0.0001).toFixed(1)}%</div></div>`; }).join('') +
   `<div class="irow axis"><div class="name"></div><div class="itrack">${[94, 96, 98, 100].map(x => `<span style="left:${((x - 94) / 6 * 100)}%">${x}%</span>`).join('')}</div><div class="val"></div></div></div>
   <div class="verdict">Intervals overlap: no clear overall winner</div>
   <p class="facts">Spoken facts: 113 of 114 decided checks passed. GPT-Realtime has no quality score: one run failed.</p>`,
  note: 'Weighted quality score with a bootstrap interval over scenarios; no pair is separable after correction.'},
 {tag: 'FINDING 4 · COST', h: 'Cost varied the most: about an eight-times spread.',
  body: `<div class="split"><div style="flex:1"><div class="cap">Median voice cost per priced run</div>` + bars([['Google Gemini Live', 0.0041], ['Amazon Nova Sonic', 0.0053], ['OpenAI GPT-Live', 0.0183], ['ElevenLabs Agents', 0.0193], ['OpenAI GPT-Realtime', 0.0196], ['xAI Grok Voice', 0.0320]], 0.0320, v => '$' + v.toFixed(4)) + `</div>
   <div class="mult"><strong>≈8×</strong><span>cheapest to dearest, for results that scored close together</span></div></div>`,
  note: 'Voice usage only, including retries. GPT-Live pricing is partial: one run returned no usage.'},
 {tag: 'FINDING 5 · HANDING OVER TO A PERSON', h: 'Asking for a human is not a given.',
  body: `<div class="split"><div style="flex:1"><div class="cap">Caller asks for a person, then asks again. Each square is one attempt; filled means transferred.</div><div class="sqrows">` +
   [['xAI Grok Voice', 18], ['ElevenLabs Agents', 18], ['OpenAI GPT-Realtime', 18], ['Google Gemini Live', 17], ['Amazon Nova Sonic', 11], ['OpenAI GPT-Live', 0]].map(([n, v]) => `<div class="srow"><div class="name">${n}</div>${squares(v, 18)}<div class="val">${v}<small>/18</small></div></div>`).join('') + `</div></div>
   <div class="side"><div class="fh">Confirmed cancellation</div><div class="big2">18 / 18</div><p>for each of five services</p><div class="warnbox"><b>Nova:</b> 17 of 18. Its earlier failure to cancel (0 of 3) did not reproduce.</div></div></div>`,
  note: 'Small follow-up runs (18 per service), outside the scores. GPT-Live offered to help with the account instead of transferring.'},
 {tag: 'TWO LESSONS', h: 'What surprised us.',
  body: `<div class="cards two"><div class="card"><b>Sounds successful is not done</b>
    <div class="bubble">“All done. Your payment has been cancelled.”</div><div class="rec"><span class="x">✕</span> Bank record: no cancellation executed</div><p>So we check the action, not just the words.</p></div>
   <div class="card"><b>Broad tuning did not change the ranking</b>
    <div class="ranks"><div><small>Standard prompt</small>${rank('')}</div><div class="same">≈</div><div><small>Tuned prompts</small>${rank('')}</div></div>
    <p>Targeted lines did fix specific failures: Nova's handover to a person went from <span class="pip">●○○</span> to <span class="pip">●●●</span> in a small diagnostic.</p></div></div>`},
 {tag: 'WHAT THIS DOES NOT TELL YOU', h: 'Read the results for what they are.',
  body: `<div class="two-col"><div class="col yes"><h3>What the evidence covers</h3><ul><li>Six prototype configurations</li><li>40 synthetic banking situations</li><li>Clear scripted callers</li><li>Safety, completion, facts and cost</li></ul></div>
   <div class="col no"><h3>Not yet measured</h3><ul><li>Real customers, noise, verified accents</li><li>Customer care (two judges disagreed)</li><li>Amazon Connect</li><li>Internal agentic platforms</li><li>Customer outcomes or certification</li></ul></div></div>`},
 {dark: 1, tag: 'SO WHAT', h: 'Baseline what we run today.',
  body: `<div class="road"><div class="step"><b>1</b><h4>Bring the harness into the work environment</h4><p>Run it against what we already use.</p></div><div class="chev">›</div>
   <div class="step"><b>2</b><h4>Choose the journey</h4><p>Where a miss or an unsafe action would hurt most. Define what good means.</p></div><div class="chev">›</div>
   <div class="step"><b>3</b><h4>Compare real experiences</h4><p>On the same evidence, before anything new speaks to a customer.</p></div></div>
   <div class="base"><span>One harness, one set of evidence</span><div><i>Amazon Connect</i><i>Internal agentic platforms</i><i>Prototype services</i></div></div>`},
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
.matrix{display:flex;flex-direction:column;gap:16px;margin-top:6px}.matrix.tight{gap:14px}.blk{display:flex;align-items:center;gap:26px}.bl{width:330px;text-align:right;font-size:28px;font-weight:bold}
.dots{display:grid;grid-template-columns:repeat(40,14px);gap:4px}.dots i{width:14px;height:14px;border-radius:50%;background:${TEAL}}.dots.ok i{background:${TEAL}}.matrix.tight .dots{grid-template-columns:repeat(40,16px)}.matrix.tight .dots i{width:16px;height:16px}
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
.iv{display:flex;flex-direction:column;gap:18px;margin-bottom:30px}.irow{display:flex;align-items:center;gap:26px}.irow .name{width:340px;font-size:30px;font-weight:bold;text-align:right}.itrack{flex:1;position:relative;height:46px;background:linear-gradient(#ADC4C9,#ADC4C9) center/100% 2px no-repeat}.ibar{position:absolute;top:13px;height:20px;background:#7FBFB0;border-radius:10px}.idot{position:absolute;top:5px;width:36px;height:36px;margin-left:-18px;border-radius:50%;background:${TEAL};border:4px solid #EDF4EE}.irow .val{width:150px;font-size:36px;font-weight:900}.irow.axis{margin-top:-6px}.irow.axis .itrack{height:30px;background:none}.irow.axis span{position:absolute;transform:translateX(-50%);font-size:24px;color:${MUTED}}
`;
(async () => {
  const b = await chromium.launch({headless: true, executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const p = await b.newPage({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1});
  for (let i = 0; i < scenes.length; i++) {
    const s = scenes[i];
    await p.setContent(`<html><style>${css(s.dark)}</style><body><div class="brand">VOICE HARNESS</div><div class="status">INDEPENDENT PROTOTYPE EVALUATION</div>
     <div class="wrap"><div class="tag">${s.tag}</div><h1>${esc(s.h)}</h1>${s.sub ? `<div class="sub">${esc(s.sub)}</div>` : ''}${s.body || ''}</div>
     ${s.note ? `<div class="note">${esc(s.note)}</div>` : ''}<div class="foot">Synthetic test data. Results describe the tested prototype configurations, not production performance.</div>
     <div class="progress" style="width:${((i + 1) / scenes.length * 100).toFixed(1)}%"></div></body></html>`);
    await p.screenshot({path: path.join(root, 'frames', String(i).padStart(2, '0') + '.png')});
  }
  await b.close();
})();
