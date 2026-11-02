#!/usr/bin/env python3
"""Export allowlisted regional-test context and verified assistant audio from local evidence.

No provider calls. Scored cells only; preflight and abandoned credit-blocked batches excluded.
The generated public artifacts are self-contained; rebuilding requires the local databases.
"""
import base64
import hashlib
import json
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.local/followup-2026-10-31'
OUT = ROOT / 'artifacts/regional-caller-2026-11-01'
PROFILES = {'default': 'Default-voice baseline', 'scottish_male': 'onyx · Scottish accent requested',
            'irish_female': 'coral · Irish accent requested', 'northern_english_male': 'verse · Yorkshire accent requested',
            'indian_english_female': 'shimmer · Indian English accent requested'}
NAMES = {'openai-live': 'OpenAI GPT-Live', 'openai-realtime-live': 'OpenAI Realtime',
         'nova-sonic-live': 'Amazon Nova Sonic', 'xai-voice-live': 'xAI Grok Voice',
         'gemini-live': 'Google Gemini Live', 'elevenlabs-live': 'ElevenLabs Agents'}
SCENARIOS = {'happy-balance-clean-001': 'Balance enquiry', 'happy-transaction-details-001': 'Transaction details',
             'happy-recent-transactions-001': 'Recent transactions', 'happy-handoff-001': 'Handoff to a person'}


def selected_sources():
    for profile in PROFILES:
        prefix = 'baseline' if profile == 'default' else f'accent-{profile}'
        yield profile, prefix + '-five', 'five-provider batch'
        yield profile, prefix + ('-nova' if profile == 'default' else '-nova2'), 'Nova + Gemini control batch'


def select_replies(units):
    """Select latest approval per output event/hash; never duplicate pre-/post-ASR units."""
    grouped = {}
    for unit in units.values():
        key = (unit.get('output_event_id'), unit.get('audio_sha256'))
        if not all(key):
            continue
        current = grouped.get(key)
        if current is None or (unit.get('assessment_epoch', 0), bool(unit.get('pcm'))) > (current.get('assessment_epoch', 0), bool(current.get('pcm'))):
            grouped[key] = unit
    return sorted(grouped.values(), key=lambda u:(u.get('created_at', 0), u.get('start_sample', 0), u['output_event_id']))


def verified_pcm(unit):
    pcm = base64.b64decode(unit['pcm'], validate=True)
    if unit.get('codec') != 'pcm_s16le' or unit.get('channels') != 1 or unit.get('rate') != 24000:
        raise ValueError('Unexpected source audio format')
    if len(pcm) != unit['byte_count'] or len(pcm) % 2 or hashlib.sha256(pcm).hexdigest() != unit['audio_sha256']:
        raise ValueError('Audio source integrity mismatch')
    return pcm


def write_clip(unit):
    pcm = verified_pcm(unit)
    path = OUT / 'clips' / (unit['audio_sha256'] + '.flac')
    if not path.exists():
        subprocess.run(['flac', '--silent', '--force', '--force-raw-format', '--endian=little',  # noqa: S603, S607  # nosec B603 B607
                        '--sign=signed', '--channels=1', '--bps=16', '--sample-rate=24000', '-o', str(path), '-'],
                       input=pcm, check=True, capture_output=True)
    decoded = subprocess.run(['flac', '--silent', '--decode', '--stdout', '--force-raw-format',  # noqa: S603, S607  # nosec B603 B607
                              '--endian=little', '--sign=signed', str(path)], check=True, capture_output=True).stdout
    if decoded != pcm:
        raise ValueError('FLAC decode differs from source PCM')
    return dict(path='clips/' + path.name, pcm_sha256=unit['audio_sha256'],
                flac_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), seconds=round(len(pcm)/48000, 3))


def task_note(observation):
    metrics = observation['metrics']
    if metrics['task_completed']:
        return 'Recorded task completed.'
    reasons = []
    for arg in metrics.get('arguments') or []:
        reasons.append(f"{arg['tool']}: " + {'wrong': 'wrong argument', 'not_called': 'not called'}.get(arg['status'], arg['status']))
    if not reasons:
        reasons.extend(f'{t}: expected tool missing' for t in observation.get('expected_tools', []))
    return '; '.join(reasons) or 'Recorded task did not complete; inspect source metrics.'


def build():
    (OUT / 'clips').mkdir(parents=True, exist_ok=True)
    records, sources = [], []
    for profile, folder, batch in selected_sources():
        local = SOURCE / folder
        payload_path = local / 'out/payload.json'
        payload = json.loads(payload_path.read_text())
        if payload['state'] != 'completed' or payload['prompt_profile'] != 'standardised-v3' or payload['repetitions'] != 1:
            raise ValueError(f'Unexpected source plan: {folder}')
        sources.append(dict(batch=folder, payload_sha256=hashlib.sha256(payload_path.read_bytes()).hexdigest(),
                            created_at=payload['created_at'], completed_at=payload.get('completed_at')))
        conn = sqlite3.connect(f'file:{local / "data/development.sqlite"}?mode=ro', uri=True)
        try:
            for cell in payload['cells']:
                ob = cell['observation']
                run_id = ob['run_id']
                run = json.loads(conn.execute('SELECT payload FROM harness_runs WHERE run_id=?', (run_id,)).fetchone()[0])
                config = run['config']
                if config.get('caller', {}).get('voice_profile', 'default') != profile or config['scenario_id'] != cell['scenario_id'] or config['provider_config_id'] != cell['provider_config_id']:
                    raise ValueError('Run does not match comparison cell')
                if ob['scenario_id'] not in SCENARIOS or not isinstance(ob['metrics']['task_completed'], bool):
                    raise ValueError('Invalid scenario/task metric')
                events = [json.loads(r[0]) for r in conn.execute('SELECT payload FROM harness_events WHERE run_id=? ORDER BY sequence', (run_id,))]
                binding = next(e['content']['provider'] for e in events if e['type'] == 'run.live_binding')
                caller_events = [e['content'] for e in events if e['type'] == 'input.caller_audio']
                if any(e.get('audio_retained') for e in caller_events):
                    raise ValueError('Caller audio coverage changed: review exporter disclosure')
                clips = []
                for unit in select_replies(run.get('units', {})):
                    clip = dict(transcript=unit.get('transcript', ''), output_event_id=unit['output_event_id'],
                                state=unit.get('state'), audio_available=bool(unit.get('pcm')),
                                transcript_source='Latest saved approval-unit text; may include independent ASR revision')
                    if unit.get('pcm'):
                        clip.update(write_clip(unit))
                    clips.append(clip)
                provider = cell['provider_config_id']
                records.append(dict(run_id=run_id, batch=folder, batch_label=batch,
                    cohort='control' if provider == 'gemini-live' and batch.startswith('Nova') else 'primary',
                    profile=profile, provider=provider, provider_name=NAMES[provider],
                    model=binding.get('returned_model_id') or binding.get('requested_model_id'),
                    model_source='returned' if binding.get('returned_model_id') else 'requested',
                    scenario=cell['scenario_id'], task_completed=ob['metrics']['task_completed'],
                    task_note=task_note(ob), assurance_outcome=ob.get('outcome'),
                    tool_counts=ob['metrics'].get('tools'), argument_checks=ob['metrics'].get('arguments'),
                    expected_tools=ob.get('expected_tools', []), prompt_profile=config['prompt_profile'],
                    caller_voice={k:binding.get('caller_voice', {}).get(k) for k in ['backend','model_id','profile','voice','sample_rate']},
                    caller_turns=len(caller_events), caller_audio_available=False,
                    clips=clips, attempts=len(cell['attempts']),
                    attempt_history=[{k:a.get(k) for k in ['run_id','state','failure']} for a in cell['attempts']], audio_available=any(c['audio_available'] for c in clips)))
        finally:
            conn.close()
    counts = Counter((r['cohort'], r['profile'], r['provider'], r['scenario']) for r in records)
    if any(n != 1 for n in counts.values()) or len(records) != 140:
        raise ValueError('Unexpected comparison coverage or duplicate cells')
    for profile in PROFILES:
        for provider in NAMES:
            for scenario in SCENARIOS:
                if counts['primary', profile, provider, scenario] != 1:
                    raise ValueError('Missing primary cell')
    data = dict(version='regional-caller-context-v1', profiles=PROFILES, providers=NAMES, scenarios=SCENARIOS,
        sources=sources, records=records, coverage=dict(primary=120, regional=96, baseline=24, supplemental_controls=20,
        runs_with_reply_audio=sum(r['audio_available'] for r in records),
        reply_excerpts=sum(c['audio_available'] for r in records for c in r['clips']), caller_recordings=0),
        limitations=[
            'Each primary cell has one scored observation for a voice and instruction setting. The accent and perceived gender of the original caller speech were never verified.',
            'Regional profiles use instructed OpenAI TTS voices; the default-voice baseline can differ in TTS backend and voice. Accent, speaker identity and synthesis are confounded, so differences cannot be attributed to accent alone.',
            'Caller recordings were not retained. Playback contains recovered assistant response excerpts only: no original regional caller audio, full-call timeline, silence gaps or proof of physical playback. Excerpts may be incomplete.',
            'Scores use retained task_completed metrics, not the assurance outcome flag. No new AI judge or listening score was applied. Task misses can reflect tool use or other behaviour rather than speech misunderstanding.',
            'Five-provider batches and later Nova batches were separate sessions. Gemini appears again as a control in the Nova batches; these 20 cases are shown separately and excluded from primary counts.',
            'The displayed result is the final scored observation per cell. One primary Gemini balance case using shimmer with the Indian English instruction timed out (TURN_TIMEOUT) before a successful retry; both attempts are disclosed, but only the final observation contributes to task counts.',
            'Preflight runs and earlier credit-blocked Nova accent attempts are excluded. Only scored cells from completed batches are included; source batch identities and payload hashes are preserved.',
            'This full-tool, standardised-v3 experiment differs from the no-tool 144-conversation study. Do not merge its results into the conversation-quality averages.',
            'Raw local databases, credentials and session metadata are not exported. PCM was recovered from saved approval units, hash-verified and losslessly encoded; generated audio is not evidence the caller heard it.',
        ])
    reconstruction = OUT / 'caller/reconstruction.json'
    if reconstruction.is_file():
        data['reconstruction'] = json.loads(reconstruction.read_text())
        for clip in data['reconstruction']['clips'].values():
            if hashlib.sha256((OUT / clip['path']).read_bytes()).hexdigest() != clip['flac_sha256']:
                raise ValueError('Reconstructed caller file integrity mismatch')
    (OUT / 'data.json').write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    render(data)
    print(json.dumps(data['coverage']))
    for p in NAMES:
        print(p, [(profile, sum(r['task_completed'] for r in records if r['cohort']=='primary' and r['provider']==p and r['profile']==profile)) for profile in PROFILES])


def render(data):
    template = (ROOT / 'scripts/templates/regional-context.html').read_text()
    (OUT / 'index.html').write_text(template.replace('__REGIONAL_DATA__', json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')))


if __name__ == '__main__':
    build()
