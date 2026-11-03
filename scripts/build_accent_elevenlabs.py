#!/usr/bin/env python3
"""Build artifacts/accent-elevenlabs-2026-11-03/index.html from the five committed batch payloads.

No provider calls. Counts use ``observation.metrics.task_completed``. Each cell is one scored attempt of a six-task panel
(3 repeats); the control uses the default caller voice (OpenAI speech), the four panel voices are ElevenLabs library voices."""
import html
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/accent-elevenlabs-2026-11-03'
BATCHES = [('control-default', 'Default voice', 'OpenAI speech, British male instruction'),
           ('accent-el_scottish_male', 'Scottish', 'Graeme · library tag scottish'),
           ('accent-el_irish_female', 'Irish', 'Jordan · library tag irish'),
           ('accent-el_yorkshire_male', 'Yorkshire', 'Ashley · library tag yorkshire'),
           ('accent-el_indian_english_female', 'Indian English', 'Vanishree · library tag indian')]
NAMES = {'openai-live': 'OpenAI GPT-Live', 'openai-realtime-live': 'OpenAI GPT-Realtime', 'xai-voice-live': 'xAI Grok Voice',
         'gemini-live': 'Google Gemini Live', 'elevenlabs-live': 'ElevenLabs Agents', 'nova-sonic-live': 'Amazon Nova Sonic'}
TASKS = {'happy-balance-clean-001': 'Balance enquiry', 'happy-transaction-details-001': 'Transaction details by code',
         'happy-recent-transactions-001': 'Recent transactions', 'happy-handoff-001': 'Handoff to a person',
         'selfcorrect-list-limit-001': 'Spoken correction (twenty, no, three)', 'commit-cancel-confirmed-001': 'Confirmed cancellation'}


def load():
    table = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # provider -> batch -> [done, total]
    tasks = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # (provider, task) -> batch -> [done, total]
    for batch, *_ in BATCHES:
        payload = json.loads((OUT / batch / 'payload.json').read_text())
        if payload['state'] != 'completed':
            raise ValueError(f'Batch not completed: {batch}')
        for cell in payload['cells']:
            ok = bool(((cell.get('observation') or {}).get('metrics') or {}).get('task_completed'))
            for row in (table[cell['provider_config_id']][batch], tasks[(cell['provider_config_id'], cell['scenario_id'])][batch]):
                row[0] += ok
                row[1] += 1
    return table, tasks


def main():
    table, tasks = load()
    head = ''.join(f'<th>{html.escape(label)}<br><span class="muted">{html.escape(note)}</span></th>' for _, label, note in BATCHES)
    rows = ''.join(
        f'<tr><th>{NAMES[p]}</th>' + ''.join(f'<td>{table[p][b][0]}/{table[p][b][1]}</td>' for b, *_ in BATCHES) + '</tr>'
        for p in NAMES)
    detail = ''
    for (p, t), per in sorted(tasks.items(), key=lambda kv: (list(NAMES).index(kv[0][0]), list(TASKS).index(kv[0][1]))):
        if any(v[0] != v[1] for v in per.values()):
            detail += (f'<tr><th>{NAMES[p]}</th><td>{TASKS[t]}</td>'
                       + ''.join(f'<td>{per[b][0]}/{per[b][1]}</td>' for b, *_ in BATCHES) + '</tr>')
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Accent panel with ElevenLabs caller voices · Voice Harness</title>
<style>:root{{--ink:#18333b;--muted:#536970;--line:#ccd9d8;--green:#09685e}}*{{box-sizing:border-box}}body{{margin:0;background:#f4f7f5;color:var(--ink);font:16px/1.6 system-ui}}main{{max-width:1180px;margin:auto;padding:24px 30px}}a{{color:var(--green)}}h1{{font-size:clamp(30px,5vw,48px);line-height:1.1;letter-spacing:-.03em}}.lede{{max-width:820px;font-size:18px;color:var(--muted)}}.notice{{padding:16px 18px;border-radius:12px;background:#e5efea;margin:20px 0;border-left:4px solid #b66a24}}.muted{{font-size:12px;color:var(--muted);font-weight:400}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;background:white;font-size:14px}}th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}}thead th{{background:#e5efea}}</style></head><body><main>
<p class="muted"><a href="../../executive-special.html">Executive briefing</a> · 3 November 2026</p>
<h1>Accent panel with ElevenLabs caller voices</h1>
<p class="lede">Six customer tasks, six services, three repeats each, in a default caller voice and in four ElevenLabs library voices (540 conversations). Exploratory, and outside the bake-off scores.</p>
<div class="notice"><strong>Read this first.</strong> The accent tags are the ElevenLabs library’s own labels; we have not independently verified the accents. The default voice comes from a different speech engine (OpenAI speech), so a gap between the default voice and a library voice may come from the engine, not the accent. Only comparisons between the four ElevenLabs voices are like for like. Each cell is three repeats of one task, standardised-v3 prompt, judges off. Task completion means the expected tool sequence ran with the declared arguments; it is not a safety result.</div>
<h2>Tasks completed, of 18 per cell (6 tasks × 3 repeats)</h2>
<div class="scroll"><table><thead><tr><th>Service</th>{head}</tr></thead><tbody>{rows}</tbody></table></div>
<h2>Where the misses were</h2>
<p class="muted" style="font-size:14px">Only task and service pairs with at least one miss, completed of 3 repeats.</p>
<div class="scroll"><table><thead><tr><th>Service</th><th>Task</th>{head}</tr></thead><tbody>{detail}</tbody></table></div>
<h2>What this does and does not show</h2>
<ul><li>xAI missed the transaction-by-code task in every ElevenLabs voice and none of the default-voice repeats, so for xAI the speech engine or voice may matter more than the accent.</li>
<li>GPT-Live missed the handoff and the spoken correction in every voice including the default, so those are not accent effects.</li>
<li>Nova’s transaction-code and handoff misses also occur in the default voice.</li>
<li>Three repeats per cell is small: treat single misses as signals to repeat, not rankings.</li></ul>
<p class="muted">Source: <code>artifacts/accent-elevenlabs-2026-11-03/*/payload.json</code>; rebuild with <code>scripts/build_accent_elevenlabs.py</code>. Caller audio was cached per voice and line, so every service heard the same recording.</p>
</main></body></html>'''
    (OUT / 'index.html').write_text(page)
    print(len(page), 'bytes')


if __name__ == '__main__':
    main()
