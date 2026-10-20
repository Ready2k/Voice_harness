import {runExplanation,causationLinks,viewChanged} from './ux-insights.mjs';
import { el, runView, renderRun, TERMINAL, summaryCounts, relativeTime, durationLabel, shortId } from './runs.mjs?v=ux-final-20261023';
import { providerEl, providerName } from './providers.mjs?v=ux-final-20261023';
import { copyButton, humanise, stateLabel } from './format.mjs?v=ux-final-20261023';
import { words } from './workflow.mjs?v=ux-final-20261023';

export const STAGES = [
  ['input', 'Customer input'], ['model', 'Model generation'], ['tools', 'Tool lifecycle'],
  ['policy', 'Policy decisions'], ['audio', 'Audio gate'], ['playback', 'Playback'],
];
export function stageFor(e) {
  if (/^(simulator\.|transcript\.input|audio\.input|input\.)/.test(e.type)) return 'input';
  if (e.type === 'decision') return e.content?.target === 'audio_chunk' ? 'audio' : 'policy';
  if (e.type.startsWith('tool.')) return 'tools';
  if (e.type.startsWith('playback.')) return 'playback';
  if (e.type === 'audio.output' || e.type.startsWith('provider.')) return 'model';
  if (/^(audio\.|transcript\.)/.test(e.type)) return 'audio';
  return null;
}
export function traceView(events, sequence = Infinity) {
  const visible = events.filter(e => e.sequence <= sequence);
  const stages = STAGES.map(([id, label]) => {
    const items = visible.filter(e => stageFor(e) === id);
    const last = items.at(-1);
    return { id, label, items, last, blocked: items.some(e => e.type === 'tool.denied' || (e.type === 'decision' && ['deny','redact','replace','escalate'].includes(e.content?.action))) };
  });
  return { visible, stages, latest: visible.at(-1) };
}
export function overviewCounts(items) {
  return { shown: items.length, active: items.filter(r => !TERMINAL.has(r.state)).length,
    failed: items.filter(r => r.state === 'failed').length,
    review: items.filter(r => (r.assurance?.overall_assurance ?? r.assurance?.overall ?? r.assurance) === 'review_required').length };
}
export function eventPresentation(event){
 const names={'audio.output':'Response generated','audio.approval_replaced':'Transcript revised','playback.ack':'Playback acknowledged','playback.released':'Audio released','playback.hold':'Audio held for assessment','tool.requested':'Tool proposed','tool.denied':'Tool denied','tool.executing':'Tool executing','tool.completed':'Tool completed','tool.result_sent':'Result sent to model','simulator.utterance_generated':'Customer turn generated','input.caller_audio':'Caller audio received','run.failed':'Run failed','run.completed':'Run completed'};
 return event?.type==='decision' ? words(event.content?.action)+' · '+(event.content?.target==='audio_chunk'?'audio policy':'tool policy') : names[event?.type]??words(event?.type??'No recorded event').replaceAll('.',' · ');
}
function button(text, action, cls='') { const b=el('button',cls,text); b.type='button'; b.addEventListener('click',action); return b; }
function stat(label, value) {const box=el('div','insight-stat');box.append(el('span','muted',label),el('strong','',String(value)));return box;}
const CARD_DEFS = [
  ['active', 'Active', (c) => c.active],
  ['failed', 'Execution failures', (c) => c.failed],
  ['review', 'Needs review', (c) => c.review],
  ['pass', 'Passed', (c) => c.assessed ? c.pass + ' of ' + c.assessed + ' assessed' : 'None assessed'],
];
/** Summary cards double as filters: pressing one narrows the runs table, pressing it again clears it. */
export function renderOverview(root, rows, activeCard, onSelect) {
  const c = summaryCounts(rows);
  const cards = CARD_DEFS.map(([id, label, value]) => {
    const b = button('', () => onSelect(activeCard === id ? null : id), 'insight-stat insight-filter');
    b.setAttribute('aria-pressed', String(activeCard === id));
    b.dataset.card = id;
    b.append(el('span', 'muted', label), el('strong', '', String(value(c))));
    return b;
  });
  if (viewChanged(root, [c, activeCard])) root.replaceChildren(...cards);
}

function providerCell(id) { const td = el('td'); td.append(providerEl(id)); return td; }

const HEADINGS = ['Scenario', 'Provider', 'Result', 'Status', 'Started', 'Duration', 'Run'];
/** The runs table: result first, lifecycle second, failed rows marked. `titles` maps scenario id to display title. */
export function renderRunsTable(root, rows, { onOpen, selectedId = null, titles = new Map(), now = Date.now() }) {
  if (!viewChanged(root, [rows.map((r) => [r.runId, r.state, r.result.key, r.endedAt]), selectedId, Math.floor(now / 30000)])) return;
  const scroll = root.scrollTop;
  const focused = root.contains(document.activeElement) ? document.activeElement?.dataset.runId : null;
  root.replaceChildren();
  if (!rows.length) { root.append(el('p', 'muted', 'No runs match. Clear a filter or open New evaluation to launch one.')); return; }
  const table = el('table', 'runs-table');
  table.setAttribute('aria-label', 'Evaluation runs');
  const head = el('tr');
  for (const h of HEADINGS) { const th = el('th', '', h); th.scope = 'col'; head.append(th); }
  const thead = el('thead'); thead.append(head); table.append(thead);
  const body = el('tbody');
  for (const r of rows) {
    const tr = el('tr', 'run-row result-' + r.result.key + (r.runId === selectedId ? ' selected' : ''));
    const title = titles.get(r.scenarioId) ?? r.scenarioId ?? r.runId;
    const open = button(title, () => onOpen(r.runId), 'link scenario-link');
    open.dataset.runId = r.runId;
    open.setAttribute('aria-label', 'Open ' + title + ' ' + r.runId);
    if (r.runId === selectedId) open.setAttribute('aria-current', 'true');
    const name = el('td'); name.append(open);
    if (titles.has(r.scenarioId)) name.append(el('div', 'muted', r.scenarioId));
    const result = el('td'); result.append(el('span', 'verdict verdict-' + r.result.key, r.result.label));
    const started = el('td', 'nowrap', relativeTime(r.startedAt, now)); if (r.startedAt) started.title = r.startedAt;
    const id = el('td', 'nowrap'); id.append(el('code', '', shortId(r.runId)), ' ', copyButton(r.runId, 'Copy run ID ' + r.runId));
    tr.append(name, providerCell(r.provider), result, el('td', 'secondary state-' + r.state, stateLabel(r.state)),
      started, el('td', 'nowrap', r.terminal ? durationLabel(r.startedAt, r.endedAt) : ''), id);
    tr.addEventListener('click', (event) => { if (!event.target.closest('button')) onOpen(r.runId); });
    [...tr.children].forEach((td, i) => { td.dataset.label = HEADINGS[i]; });
    body.append(tr);
  }
  table.append(body);
  root.append(table);
  root.scrollTop = scroll;
  if (focused) root.querySelector('[data-run-id="' + focused + '"]')?.focus({ preventScroll: true });
}

export function createWorkspace(root, onCancel) {
  root.replaceChildren();
  let data=null, follow=true, cursor=null, selected=null, mode='graph';
  const title=el('div','workspace-heading');
  const heading=el('h2','','Evaluation trace');
  const status=el('span','trace-status'); title.append(heading,status);
  const context=el('p','muted');context.className='run-context muted';
  const summary=el('div','insight-grid');
  const toolbar=el('div','trace-toolbar');
  const graphButton=button('System activity',()=>{mode='graph';draw();});
  const timelineButton=button('Event timeline',()=>{mode='timeline';draw();});
  const followButton=button('Following latest',()=>{follow=!follow;cursor=follow?null:data?.events.at(-1)?.sequence??null;draw();});
  const cancel=button('Cancel evaluation',()=>onCancel(data.run.run_id));
  cancel.hidden=true;
  toolbar.append(graphButton,timelineButton,followButton,cancel);
  const note=el('p','muted','Follow recorded activity or pause to inspect a point in history. Connections show system stages, not proof of causation.');
  const layout=el('div','trace-layout'); const visual=el('section','trace-panel');
  const graph=el('div','execution-graph');
  const graphButtons=new Map(); const timeline=el('div','event-timeline');
  const scrub=el('div','trace-scrub'); const range=el('input'); range.type='range';range.min='0';range.value='0';range.setAttribute('aria-label','Inspect recorded event position');
  const position=el('span','muted');scrub.append(el('span','muted','History'),range,position);
  visual.append(graph,timeline,scrub);
  const inspector=el('aside','trace-panel inspector'); inspector.setAttribute('aria-label','Event inspector');
  layout.append(visual,inspector);
  const story=el('section','trace-insight');story.setAttribute('aria-label','Run explanation');
  const transcript=el('section','panel');
  const disclosure=el('details','panel');disclosure.append(el('summary','','Full evidence, configuration & metrics'));
  const detail=el('div');disclosure.append(detail);
  root.append(title,context,summary,story,toolbar,note,layout,transcript,disclosure);
  range.addEventListener('input',()=>{follow=false;cursor=data.events[Number(range.value)]?.sequence??null;selected=cursor;draw();});
  function inspect(event) { selected=event?.sequence; drawInspector(); }
  function drawInspector(){

    const limit=follow?Infinity:cursor??Infinity;
    const event=data.events.find(e=>e.sequence===selected&&e.sequence<=limit)??data.events.filter(e=>e.sequence<=limit).at(-1);
    if(!viewChanged(inspector,event??null))return;
    inspector.replaceChildren(el('h3','','Event inspector'));
    if(!event){inspector.append(el('p','muted','Select a graph stage or recorded event to inspect its evidence.'));return;}
    inspector.append(el('p','event-type',eventPresentation(event)),el('p','muted','Sequence '+event.sequence+' · '+(event.occurred_at??'Time unavailable')));
    inspector.append(el('p','muted','Event ID: '+(event.event_id??event.id??'Not supplied')));
    const causes=causationLinks(event,data.events.filter(e=>e.sequence<=limit));
    if(causes.length){inspector.append(el('p','muted','Recorded causes'));for(const cause of causes)inspector.append(button('#'+cause.sequence+' '+eventPresentation(cause),()=>inspect(cause),'evidence-link'));}
    else inspector.append(el('p','muted','No inspectable causal link is recorded for this event.'));
    const raw=el('details');raw.addEventListener('toggle',()=>{if(raw.open)selected=event.sequence;});raw.append(el('summary','','Raw event record'));
    const pre=el('pre','event-json',JSON.stringify({content:event.content,causation_event_ids:event.causation_event_ids,causation_unknown_reason:event.causation_unknown_reason},null,2));raw.append(pre);inspector.append(raw);
  }
  function draw(){
    if(!data)return;
    const events=data.events;
    const trace=traceView(events,follow?Infinity:cursor??Infinity);
    cancel.hidden=!onCancel || TERMINAL.has(data.run.state);
    const latestStage=trace.visible.filter(e=>stageFor(e)).at(-1);
    const view=runView(data.run,trace.visible,data.metrics,data.holds,data.assurance);
    heading.textContent=data.displayTitle??data.run.scenario?.scenario_id??data.run.run_id;
    const provider=events.find(e=>e.type==='run.live_binding')?.content?.provider;
    const caller = data.run.config?.caller;
    context.title=[data.run.config?.provider_config_id,data.run.config?.policy_profile&&'policy: '+data.run.config.policy_profile].filter(Boolean).join(' · ');context.textContent=providerName(data.run.config?.provider_config_id)+' · seed '+(data.run.config?.fixture_seed??'unknown')+' · '+(data.run.config?.policy_profile?'policy '+humanise(data.run.config.policy_profile):'Unknown policy')+(provider?' · requested model: '+(provider.requested_model_id??'unknown')+' · returned: '+(provider.returned_model_id??'unresolved'):'') + (data.run.config?.input_mode === 'manual' ? ' · Human caller' : caller?.mode === 'live_ai' ? ' · Live AI caller · '+caller.persona+' · '+caller.voice+' · caller fidelity requires review' : ' · Fixed caller');
    status.replaceChildren(...(TERMINAL.has(data.run.state)?[]:[el('span','live-dot'),el('strong','live-label','Live · updating every 2s'),' · ']),stateLabel(data.run.state)+' · '+(follow?'Latest evidence':'History inspection'));
    graphButton.setAttribute('aria-pressed',String(mode==='graph'));timelineButton.setAttribute('aria-pressed',String(mode==='timeline'));
    const finished=TERMINAL.has(data.run.state);followButton.textContent=follow?'Pause live following':(finished?'Show latest':'Resume latest');followButton.setAttribute('aria-pressed',String(follow));followButton.hidden=finished&&follow;
    summary.replaceChildren(stat('Model · latest verdict',(data.assurance?.status==='assessed'?data.assurance.model_behaviour:data.run.assurance?.model_behaviour)??'Not assessed'),stat('Platform · latest verdict',(data.assurance?.status==='assessed'?data.assurance.platform_enforcement:data.run.assurance?.platform_enforcement)??'Not assessed'),stat('Audio · viewed events',view.tally.blocked+' blocked / '+view.tally.released+' released'));
    graph.hidden=mode!=='graph';timeline.hidden=mode!=='timeline';
    for(const stage of trace.stages){
      let b=graphButtons.get(stage.id);
      if(!b){b=button('',()=>inspect(traceView(data.events,follow?Infinity:cursor??Infinity).stages.find(s=>s.id===stage.id)?.last),'graph-stage');graphButtons.set(stage.id,b);graph.append(b);} b.replaceChildren();
      b.classList.toggle('stage-blocked',stage.blocked);b.classList.toggle('stage-current',stage.items.some(e=>e.sequence===latestStage?.sequence));
      b.append(el('span','muted',stage.label),el('strong','',eventPresentation(stage.last)),el('span','muted',stage.items.length+' events'+(stage.blocked?' · Denial recorded':'')));
      b.disabled=!stage.last;
    }
    const timelineFocus=document.activeElement?.closest('.timeline-event')?.dataset.sequence;
    const timelineScroll=timeline.scrollTop;
    if(viewChanged(timeline,trace.visible)){
    timeline.replaceChildren();
    for(const event of trace.visible.slice(-100)){
      const b=button('',()=>inspect(event),'timeline-event');b.dataset.sequence=String(event.sequence);b.append(el('span','muted','#'+event.sequence),el('span','',eventPresentation(event)),el('span','muted',stageFor(event)??'Lifecycle'));timeline.append(b);
    }
    if(!trace.visible.length)timeline.append(el('p','muted','No events recorded yet.'));
    if(trace.visible.length>100)timeline.prepend(el('p','muted','Showing the last 100 events at this history position. Download the evidence bundle for the complete ledger.'));
    timeline.scrollTop=timelineScroll;
    if(timelineFocus)timeline.querySelector('[data-sequence="'+timelineFocus+'"]')?.focus({preventScroll:true});
    }
    range.max=String(Math.max(0,events.length-1));range.disabled=!events.length;range.value=String(follow?Math.max(0,events.length-1):Math.max(0,events.findIndex(e=>e.sequence===cursor)));
    position.textContent=trace.visible.length+' / '+events.length;
    const explanation=runExplanation(data.run,trace.visible,data.assurance);
    if(viewChanged(story,explanation)){
      story.replaceChildren(el('h3','','What this run tells us'),el('p','',explanation.text),el('p','muted','Next: '+explanation.next));
      for(const evidence of explanation.evidence.slice(-8))story.append(button('Inspect #'+evidence.sequence+' · '+eventPresentation({type:evidence.type}),()=>inspect(data.events.find(e=>e.event_id===evidence.id)),'evidence-link'));
    }
    if(viewChanged(transcript,view.transcript)){
    transcript.replaceChildren(el('h3','','Conversation & audio disposition'));
    for(const line of view.transcript){const row=el('div','conversation-line');row.append(el('span','speaker',line.speaker),el('p','',line.text));if(line.stateLabel)row.append(el('span','chip chip-'+line.state,line.stateLabel));if(line.playbackLabel&&!["played","released"].includes(line.state))row.append(el('span','playback-label',line.playbackLabel));
    if(line.generatedButNotReleased)row.append(el('span','muted','Not released in recorded events'));if(line.note&&!line.generatedButNotReleased)row.append(el('span','muted',line.note));transcript.append(row);}
    if(!view.transcript.length)transcript.append(el('p','muted','Waiting for transcript evidence.'));
    }
    if(disclosure.open&&viewChanged(detail,view))renderRun(detail,view,onCancel);
    drawInspector();
  }
  disclosure.addEventListener('toggle',()=>{if(disclosure.open&&data)renderRun(detail,runView(data.run,traceView(data.events,follow?Infinity:cursor??Infinity).visible,data.metrics,data.holds,data.assurance),onCancel);});
  return {update(run,events,metrics,holds,assurance,displayTitle){data={run,events,metrics,holds,assurance,displayTitle};draw();}};
}
