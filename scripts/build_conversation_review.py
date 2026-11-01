#!/usr/bin/env python3
"""Build the offline-capable, provider-visible business review from frozen evidence."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'artifacts/voice-conversation-v2'
NAMES = {
    'openai-live': 'OpenAI GPT-Live', 'openai-realtime-live': 'OpenAI Realtime',
    'nova-sonic-live': 'Amazon Nova Sonic', 'xai-voice-live': 'xAI Grok Voice',
    'gemini-live': 'Google Gemini Live', 'elevenlabs-live': 'ElevenLabs Agents',
}


def main():
    plan = json.loads((FOLDER / 'plan.json').read_text())
    results = json.loads((FOLDER / 'results.json').read_text())
    clips = []
    for job in plan['jobs']:
        result = results['jobs'].get(job['id'], {})
        if result.get('state') != 'completed':
            continue
        path = f"clips/{job['id']}.flac"
        if not (FOLDER / path).is_file():
            raise ValueError(f'Missing recording: {path}')
        profile = plan['profiles'][job['provider']][job['condition']]
        clips.append(dict(**job, provider_name=NAMES[job['provider']], path=path,
                          model=result.get('returned_model_id') or profile['model_id'],
                          model_source='Returned' if result.get('returned_model_id') else 'Requested',
                          voice=profile['voice'], turns=result['turns']))
    data = dict(plan_hash=results['plan_hash'], recorded=plan['created_at'][:10],
                scenarios=plan['scenarios'], providers=NAMES, clips=clips)
    assessment_path = FOLDER / 'assessment/scores.json'
    if assessment_path.is_file():
        assessment = json.loads(assessment_path.read_text())
        if assessment['plan_hash'] != results['plan_hash'] or any(
            hashlib.sha256((FOLDER / name).read_bytes()).hexdigest() != digest
            for name, digest in assessment['source_sha256'].items()
        ):
            raise ValueError('Assessment is stale; rebuild and validate it before publishing')
        assessed = {r['id']: r for r in assessment['reviews']}
        if set(assessed) != {c['id'] for c in clips}:
            raise ValueError('Assessment coverage does not match recordings')
        for clip in clips:
            clip['assessment'] = assessed[clip['id']]
        data['assessment'] = dict(path='assessment/index.html', date=assessment['assessed_on'])
    template = (ROOT / 'scripts/templates/conversation-review.html').read_text()
    payload = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
    (FOLDER / 'review.html').write_text(template.replace('__REVIEW_DATA__', payload))
    print(f'Built review.html with {len(clips)} conversations.')


if __name__ == '__main__':
    main()
