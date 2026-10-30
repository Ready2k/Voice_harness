"""Index tracked reports; optionally stage a self-contained GitHub Pages site."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = 'https://github.com/Ready2k/Voice_harness/blob/main/'
FEATURES = [
    ('artifacts/bakeoff-2026-10-29-standard-5/report.html', 'Standard-prompt bake-off · five providers', 'Completed assessment of 600 runs: five providers, 40 scenarios, three repetitions, using standardised prompts. No clear quality winner.', 'Current', '2026-10-29'),
    ('executive-special.html', 'Explore the executive briefing', 'How we engineered the voice harness, how the tests work, and the evidence behind the business decision.', 'Executive briefing', '2026-10-29'),
    ('artifacts/voice-conversation-v2/rate.html', 'Voice conversation · blind listening study', 'Common against provider-tuned prompts in 144 three-turn conversations, with British female assistant and British male caller voices. Ratings pending.', 'Listening', '2026-10-29'),
    ('artifacts/fair-bakeoff-2026-10-28/rescored-2026-10-28/report.html', 'Fair bake-off · final re-score', 'Earlier 720-cell assessment of six providers. Re-scored evidence; no calls were rerun. Different setup from the latest standard-prompt bake-off.', 'Re-scored', '2026-10-28'),
    ('artifacts/fair-bakeoff-2026-10-28/report.html', 'Fair bake-off · original assessment', 'Original scoring retained for provenance. Use the final re-score when reviewing this earlier experiment.', 'Original', '2026-10-28'),
    ('artifacts/full-advanced-bakeoff-2026-10-26/rescored-2026-10-27/report.html', 'Advanced bake-off · corrected assessment', 'Earlier 600-cell experiment, with corrected latency and task-completion scoring. Different setup from the latest bake-off.', 'Re-scored', '2026-10-27'),
    ('reports/gpt-live-1-evaluation-2026-10-27.html', 'GPT-Live 1 · single-model evaluation', 'Forty synthetic scenarios with assurance checks, task completion, latency and scoped costs.', 'Evaluation', '2026-10-27'),
    ('reports/gpt-live-1-listening-2026-10-27.html', 'GPT-Live 1 · listening room', 'Selected assistant speech with audio players and transcripts. Use the evaluation for measured latency.', 'Listening', '2026-10-27'),
]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Stage the Pages site in this new directory')
    args = parser.parse_args()
    tracked = subprocess.check_output([shutil.which('git') or 'git', 'ls-files', '-z', 'artifacts', 'reports', 'docs'], cwd=ROOT).decode().split('\0')  # noqa: S603
    files = [p for p in tracked if p and (ROOT / p).is_file()]
    entries = []
    for name in files:
        path = ROOT / name
        dates = re.findall(r'20\d{2}-\d{2}-\d{2}', name)
        kind = {'html': 'Web report', 'md': 'Notes', 'pdf': 'PDF', 'wav': 'Audio', 'flac': 'Audio', 'mp3': 'Audio', 'ogg': 'Audio', 'pcm': 'Audio', 'json': 'Data', 'jsonl': 'Data', 'gz': 'Archive', 'zip': 'Archive'}.get(path.suffix[1:].lower(), 'Other')
        # Markdown is best read in GitHub's renderer; all other files are served directly.
        entries.append(dict(path=name, title=path.stem.replace('-', ' ').replace('_', ' '), date=max(dates) if dates else '', kind=kind, size=path.stat().st_size, url=REPO + name if kind == 'Notes' else name, group='/'.join(path.parts[:2])))
    features = [dict(path=p, title=t, description=d, status=s, date=dt) for p,t,d,s,dt in FEATURES if p in files or p == 'executive-special.html' and (ROOT / p).is_file()]
    payload = dict(latest=max((x['date'] for x in entries), default=''), entries=sorted(entries, key=lambda x:(x['date'], x['path']), reverse=True), features=features)
    sources = ['artifacts/voice-naturalness-2026-10-25/key.json']
    gpt_source = 'artifacts/gpt-live-voice-library-2026-10-28/key.json'
    if gpt_source in files:
        sources.append(gpt_source)
    clips = []
    lines = None
    for source in sources:
        key = json.loads((ROOT / source).read_text())
        if lines is not None and key['lines'] != lines:
            raise SystemExit(f'Mismatched library prompts: {source}')
        lines = key['lines']
        for clip_id, clip in key['clips'].items():
            path = str(Path(source).parent / 'clips' / f'{clip_id}.wav')
            if path not in files:
                raise SystemExit(f'Untracked or missing library clip: {path}')
            recorded = re.search(r'20\d{2}-\d{2}-\d{2}', source).group()
            clips.append(dict(id=clip_id, path=path, source=source, recorded=recorded,
                              sha256=hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), **clip))
    library = dict(sources=sources, lines=lines, providers=list(dict.fromkeys(c['provider'] for c in clips)), clips=clips)
    conversations = ROOT / 'artifacts/voice-conversation-v2'
    if (conversations / 'results.json').is_file():
        plan = json.loads((conversations / 'plan.json').read_text())
        results = json.loads((conversations / 'results.json').read_text())['jobs']
        names = {'openai-live': 'OpenAI GPT-Live 1', 'openai-realtime-live': 'OpenAI Realtime', 'nova-sonic-live': 'Amazon Nova Sonic',
                 'xai-voice-live': 'xAI Grok Voice', 'gemini-live': 'Google Gemini Live', 'elevenlabs-live': 'ElevenLabs Agents'}
        voices = {'ZF6FPAbjXT4488VcRRnw': 'Amelia'}
        flac = json.loads((conversations / 'clips/flac-manifest.json').read_text())

        def flac_seconds(path):
            header = path.read_bytes()[:42]  # fLaC marker, block header, then STREAMINFO
            rate = (header[18] << 12) | (header[19] << 4) | (header[20] >> 4)
            samples = ((header[21] & 0x0F) << 32) | int.from_bytes(header[22:26], 'big')
            return round(samples / rate, 1)

        cells = []
        for job in plan['jobs']:
            result = results.get(job['id'])
            if job['repetition'] != 1 or not result or result['state'] != 'completed':
                continue
            profile = plan['profiles'][job['provider']][job['condition']]
            clip = f"artifacts/voice-conversation-v2/clips/{job['id']}.flac"
            cells.append(dict(id=job['id'], provider=names[job['provider']], scenario=job['scenario'], condition=job['condition'], path=clip,
                              seconds=flac_seconds(ROOT / clip), model=result.get('returned_model_id') or profile['model_id'],
                              voice=voices.get(profile['voice'], profile['voice'] or 'provider default'),
                              prompt_sha256=result['configuration']['prompt_sha256'], wav_sha256=flac[job['id']]['wav_sha256'],
                              flac_sha256=flac[job['id']]['flac_sha256']))
        order = list(names.values())
        cells.sort(key=lambda c: (order.index(c['provider']), c['condition'] != 'common'))
        scenarios = [dict(id=x['id'], title=x['title'], turns=x['turns'], review=x['review']) for x in plan['scenarios']]
        (ROOT / 'site/voice-conversations.json').write_text(json.dumps(dict(
            source='artifacts/voice-conversation-v2/plan.json', recorded=plan['created_at'][:10], providers=order, scenarios=scenarios, clips=cells), indent=2) + '\n')
    (ROOT / 'site/voice-library.json').write_text(json.dumps(library, indent=2) + '\n')
    (ROOT / 'site/catalogue.json').write_text(json.dumps(payload, indent=2) + '\n')
    if args.output:
        output = args.output.resolve()
        if output.exists():
            raise SystemExit('Output directory must not already exist')
        if output == ROOT or ROOT in output.parents and output.parts[len(ROOT.parts):len(ROOT.parts)+1] in [('artifacts',), ('reports',), ('docs',), ('site',)]:
            raise SystemExit('Choose a staging directory outside source content')
        output.mkdir(parents=True)
        for name in files + ['favicon.ico', 'site/voice-harness-mark.svg', 'index.html', 'vision.html', 'how-it-works.html', 'technical-architecture.html', 'test-setup.html', 'site/test-setup.js', 'business-dashboard.html', 'site/business-dashboard.css', 'site/business-dashboard.mjs', 'site/business-metrics.mjs', 'site/business-dashboard.json', 'executive-special.html', 'site/executive-special.css', 'site/executive-special.js', 'voice-library.html', 'executive-trust.html', 'site/theme.css', 'site/style.css', 'site/app.js', 'site/catalogue.json', 'site/voice-library.css', 'site/voice-library.js', 'site/voice-library.json', 'site/voice-conversations.json', 'site/cx-review-data.json']:
            dest = output / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, dest)
        (output / '.nojekyll').touch()
    print(f'Indexed {len(entries)} files; {len(features)} featured reports.')

if __name__ == '__main__':
    main()
