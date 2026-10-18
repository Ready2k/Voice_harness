import { el, runView, renderRun, TERMINAL } from './runs.mjs';

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
function button(text, action, cls='') { const b=el('button',cls,text); b.type='button'; b.addEventListener('click',action); return b; }
function stat(label, value) {const box=el('div','insight-stat');box.append(el('span','muted',label),el('strong','',String(value)));return box;}
export function renderRecent(root, payload, onOpen) {
  root.replaceChildren();
  for (const run of payload.items) {
    const b=button('',()=>onOpen(run.run_id),'recent-run');
    b.append(el('strong','',run.scenario_id??run.run_id),el('span','muted',run.state+' · '+(run.provider_config_id??'Provider unknown')),el('span','muted',run.run_id));
    b.setAttribute('aria-label','Open '+(run.scenario_id??run.run_id)+' '+run.run_id);
    root.append(b);
  }
  if (!payload.items.length) root.append(el('p','muted','No evaluations yet. Open New evaluation to launch a fixture.'));
}
export function renderOverview(root, payload, providers) {
  const c=overviewCounts(payload.items);
  root.replaceChildren(stat('Active in recent runs',c.active),stat('Execution failures',c.failed),stat('Review status',payload.items.length && payload.items.every(r=>r.assurance!=null)?c.review:'Inspect a run'),stat('Live configurations ready',providers.filter(p=>p.available).length+' / '+providers.length));
}

export function createWorkspace(root, onCancel) {
  root.replaceChildren();
  let data=null, follow=true, cursor=null, selected=null, mode='graph';
  const title=el('div','workspace-heading');
  const heading=el('h2','','Evaluation trace');
  const status=el('span','trace-status'); title.append(heading,status);
  const summary=el('div','insight-grid');
  const toolbar=el('div','trace-toolbar');
  const graphButton=button('Execution graph',()=>{mode='graph';draw();});
  const timelineButton=button('Event timeline',()=>{mode='timeline';draw();});
  const followButton=button('Following latest',()=>{follow=!follow;if(follow)cursor=null;draw();});
  const cancel=button('Cancel evaluation',()=>onCancel(data.run.run_id));
  cancel.hidden=true;
  toolbar.append(graphButton,timelineButton,followButton,cancel);
  const note=el('p','muted','Recorded events update every two seconds during an active run. Lines show the system flow; they do not imply event causation.');
  const layout=el('div','trace-layout'); const visual=el('section','trace-panel');
  const graph=el('div','execution-graph'); const timeline=el('div','event-timeline');
  const scrub=el('div','trace-scrub'); const range=el('input'); range.type='range';range.min='0';range.value='0';range.setAttribute('aria-label','Inspect recorded event position');
  const position=el('span','muted');scrub.append(el('span','muted','History'),range,position);
  visual.append(graph,timeline,scrub);
  const inspector=el('aside','trace-panel inspector'); inspector.setAttribute('aria-label','Event inspector');
  layout.append(visual,inspector);
  const story=el('p','trace-insight');story.setAttribute('role','status');
  const transcript=el('section','panel');
  const disclosure=el('details','panel');disclosure.append(el('summary','','Full evidence, configuration & metrics'));
  const detail=el('div');disclosure.append(detail);
  root.append(title,summary,toolbar,note,layout,story,transcript,disclosure);
  range.addEventListener('input',()=>{follow=false;cursor=data.events[Number(range.value)]?.sequence??null;selected=cursor;draw();});
  function inspect(event) { selected=event?.sequence; drawInspector(); }
  function drawInspector(){
    inspector.replaceChildren(el('h3','','Event inspector'));
    const limit=follow?Infinity:cursor??Infinity;
    const event=data.events.find(e=>e.sequence===selected&&e.sequence<=limit)??data.events.filter(e=>e.sequence<=limit).at(-1);
    if(!event){inspector.append(el('p','muted','Select a graph stage or recorded event to inspect its evidence.'));return;}
    inspector.append(el('p','event-type',event.type),el('p','muted','Sequence '+event.sequence+' · '+(event.occurred_at??'Time unavailable')));
    inspector.append(el('p','muted','Event ID: '+(event.event_id??event.id??'Not supplied')));
    const pre=el('pre','event-json',JSON.stringify({content:event.content,causation_event_ids:event.causation_event_ids,causation_unknown_reason:event.causation_unknown_reason},null,2));inspector.append(pre);
  }
  function draw(){
    if(!data)return;
    const events=data.events;
    const trace=traceView(events,follow?Infinity:cursor??Infinity);
    cancel.hidden=!onCancel || TERMINAL.has(data.run.state);
    const latestStage=trace.visible.filter(e=>stageFor(e)).at(-1);
    const view=runView(data.run,trace.visible,data.metrics,data.holds,data.assurance);
    heading.textContent=data.run.scenario?.scenario_id??data.run.run_id;
    status.textContent=data.run.state+' · '+(follow?'Latest evidence':'History inspection');
    graphButton.setAttribute('aria-pressed',String(mode==='graph'));timelineButton.setAttribute('aria-pressed',String(mode==='timeline'));
    followButton.textContent=follow?'Pause live following':'Resume latest';followButton.setAttribute('aria-pressed',String(follow));
    summary.replaceChildren(stat('Model · latest verdict',data.run.assurance?.model_behaviour??'Not assessed'),stat('Platform · latest verdict',data.run.assurance?.platform_enforcement??'Not assessed'),stat('Audio · viewed events',view.tally.blocked+' blocked / '+view.tally.released+' released'));
    graph.hidden=mode!=='graph';timeline.hidden=mode!=='timeline';graph.replaceChildren();
    for(const stage of trace.stages){
      const b=button('',()=>inspect(stage.last),'graph-stage');
      b.classList.toggle('stage-blocked',stage.blocked);b.classList.toggle('stage-current',stage.items.some(e=>e.sequence===latestStage?.sequence));
      b.append(el('span','muted',stage.label),el('strong','',stage.last?.type??'No recorded event'),el('span','muted',stage.items.length+' events'+(stage.blocked?' · Denial recorded':'')));
      b.disabled=!stage.last;graph.append(b);
    }
    timeline.replaceChildren();
    for(const event of trace.visible.slice(-100)){
      const b=button('',()=>inspect(event),'timeline-event');b.append(el('span','muted','#'+event.sequence),el('span','',event.type),el('span','muted',stageFor(event)??'Lifecycle'));timeline.append(b);
    }
    if(!trace.visible.length)timeline.append(el('p','muted','No events recorded yet.'));
    if(trace.visible.length>100)timeline.prepend(el('p','muted','Showing the last 100 events at this history position. Download the evidence bundle for the complete ledger.'));
    range.max=String(Math.max(0,events.length-1));range.disabled=!events.length;range.value=String(follow?Math.max(0,events.length-1):Math.max(0,events.findIndex(e=>e.sequence===cursor)));
    position.textContent=trace.visible.length+' / '+events.length;
    const denied=trace.stages.find(s=>s.id==='tools').items.filter(e=>e.type==='tool.denied').length;
    story.textContent=denied||view.tally.blocked?`${denied} tool denials and ${view.tally.blocked} blocked audio units recorded. Enforcement activity is evidence, not an overall model or platform verdict.`:'No tool denial or blocked audio unit is recorded in this view. Absence of a block is not an assurance pass.';
    transcript.replaceChildren(el('h3','','Conversation & audio disposition'));
    for(const line of view.transcript){const row=el('div','conversation-line');row.append(el('span','speaker',line.speaker),el('p','',line.text));if(line.stateLabel)row.append(el('span','chip chip-'+line.state,line.stateLabel));if(line.generatedButNotReleased)row.append(el('span','muted','Generated, never played'));if(line.note&&!line.generatedButNotReleased)row.append(el('span','muted',line.note));transcript.append(row);}
    if(!view.transcript.length)transcript.append(el('p','muted','Waiting for transcript evidence.'));
    if(disclosure.open)renderRun(detail,view,onCancel);
    drawInspector();
  }
  disclosure.addEventListener('toggle',()=>{if(disclosure.open&&data)renderRun(detail,runView(data.run,traceView(data.events,follow?Infinity:cursor??Infinity).visible,data.metrics,data.holds,data.assurance),onCancel);});
  return {update(run,events,metrics,holds,assurance){data={run,events,metrics,holds,assurance};draw();}};
}
