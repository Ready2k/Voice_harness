#!/usr/bin/env python3
"""Regenerate illustrative caller clips; never claim to restore the original audio.

Uses saved scenario text, validates it against original caller event text hashes,
then uses the retained TTS model/voice and repository accent instructions. Paid TTS
calls are deduplicated and checkpointed. Historical results and audio hashes stay unchanged.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from voice_conversation import load_environment  # noqa: E402

from voice_harness import caller_voice  # noqa: E402

OUT = ROOT / 'artifacts/regional-caller-2026-11-01'
LOCAL = ROOT / '.local/followup-2026-10-31'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build_plan(records):
    specs, bindings = {}, {}
    for record in records:
        if record['profile'] == 'default':
            continue
        profile = record['profile']
        voice, instructions = caller_voice.PANEL[profile]
        retained = record['caller_voice']
        if retained['backend'] != 'openai_tts' or retained['voice'] != voice:
            raise ValueError('Retained caller profile no longer matches repository voice panel')
        db = LOCAL / record['batch'] / 'data/development.sqlite'
        with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
            run = json.loads(conn.execute('SELECT payload FROM harness_runs WHERE run_id=?', (record['run_id'],)).fetchone()[0])
            config = run['config']
            definition = json.loads(conn.execute('SELECT definition FROM harness_scenario_definitions WHERE scenario_id=? AND version=?',
                (record['scenario'], config['scenario_version'])).fetchone()[0])
            events = [json.loads(x[0]) for x in conn.execute('SELECT payload FROM harness_events WHERE run_id=? ORDER BY sequence', (record['run_id'],))]
        bindings[record['run_id']] = []
        for event in events:
            if event['type'] != 'input.caller_audio':
                continue
            original = event['content']
            text = definition['turns'][original['turn']]['customer']
            if digest(text.encode()) != original['source_text_sha256']:
                raise ValueError('Saved caller words do not match original text hash')
            spec = dict(profile=profile, voice=voice, model=retained['model_id'], text=text,
                        instructions=instructions, sample_rate=24000, format='pcm_s16le_mono')
            key = digest(json.dumps(spec, sort_keys=True).encode())
            specs[key] = spec
            bindings[record['run_id']].append(dict(clip_id=key, turn=original['turn'],
                original_text_sha256=original['source_text_sha256'], original_pcm_sha256=original['pcm_sha256']))
    return specs, bindings


def main():
    load_environment()
    data = json.loads((OUT / 'data.json').read_text())
    specs, bindings = build_plan(data['records'])
    folder = OUT / 'caller'
    folder.mkdir(exist_ok=True)
    manifest_path = folder / 'reconstruction.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else dict(
        version='regional-caller-reconstruction-v1', original_audio=False,
        disclosure='New illustrative TTS audio. Words match saved caller-event text hashes. Model/voice come from retained bindings; accent instructions come from the repository panel, not a retained request body. Not the original waveform, timing or test input; do not use it to validate historical recognition outcomes.',
        clips={})
    manifest['bindings'] = bindings
    manifest['expected_clips'] = len(specs)
    print(f'{len(specs)} unique caller clips for {len(bindings)} regional/control runs.', flush=True)
    for key, spec in specs.items():
        path = folder / f'{key}.flac'
        existing = manifest['clips'].get(key)
        if existing and path.exists() and digest(path.read_bytes()) == existing['flac_sha256']:
            continue
        env = {**os.environ, caller_voice.ENV: 'openai_tts', caller_voice.PROFILE_ENV: spec['profile'],
               caller_voice.LIVE_FLAG: 'openai', 'VOICE_HARNESS_CALLER_TTS_MODEL': spec['model']}
        pcm = caller_voice.openai_tts_pcm(spec['text'], environ=env)
        subprocess.run(['flac', '--silent', '--force', '--force-raw-format', '--endian=little', '--sign=signed',  # noqa: S603, S607  # nosec B603 B607
                        '--channels=1', '--bps=16', '--sample-rate=24000', '-o', str(path), '-'], input=pcm, check=True, capture_output=True)
        decoded = subprocess.run(['flac','--silent','--decode','--stdout','--force-raw-format','--endian=little','--sign=signed',str(path)],capture_output=True,check=True).stdout  # noqa: S603, S607  # nosec B603 B607
        if decoded != pcm:
            raise ValueError('Reconstructed clip failed lossless verification')
        manifest['clips'][key] = dict(**spec, path='caller/'+path.name, pcm_sha256=digest(pcm),
            flac_sha256=digest(path.read_bytes()), seconds=round(len(pcm)/48000,3),
            generated_at=datetime.now(UTC).isoformat(), reconstructed=True)
        manifest_path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
        print(f'Generated {len(manifest["clips"])}/{len(specs)}: {spec["profile"]}', flush=True)
    manifest_path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
    print('Caller reconstruction complete.',flush=True)


if __name__ == '__main__':
    main()
