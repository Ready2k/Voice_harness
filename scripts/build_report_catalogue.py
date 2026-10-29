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
    ('artifacts/fair-bakeoff-2026-10-28/rescored-2026-10-28/report.html', 'Fair bake-off · final re-score', 'Latest assessment of 720 cells: six providers, 40 scenarios, three repetitions. Re-scored evidence; no calls were rerun.', 'Current', '2026-10-28'),
    ('executive-special.html', 'Explore the executive briefing', 'How we engineered the voice harness, how the tests work, and the evidence behind the business decision.', 'Executive briefing', '2026-10-28'),
    ('artifacts/fair-bakeoff-2026-10-28/report.html', 'Fair bake-off · original assessment', 'Original scoring retained for provenance. Read the final re-score first.', 'Original', '2026-10-28'),
    ('artifacts/full-advanced-bakeoff-2026-10-26/rescored-2026-10-27/report.html', 'Advanced bake-off · corrected assessment', 'Earlier 600-cell experiment, with corrected latency and task-completion scoring. Different setup from the fair bake-off.', 'Re-scored', '2026-10-27'),
    ('reports/gpt-live-1-evaluation-2026-10-27.html', 'GPT-Live 1 · single-model evaluation', 'Forty synthetic scenarios with assurance checks, task completion, latency and scoped costs.', 'Evaluation', '2026-10-27'),
    ('reports/gpt-live-1-listening-2026-10-27.html', 'GPT-Live 1 · listening room', 'Selected assistant speech with audio players and transcripts. Use the evaluation for measured latency.', 'Listening', '2026-10-27'),
    ('artifacts/voice-naturalness-2026-10-25/rate.html', 'Voice naturalness · listening study', 'Listen to and rate the retained voice samples.', 'Listening', '2026-10-25'),
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
        kind = {'html': 'Web report', 'md': 'Notes', 'pdf': 'PDF', 'wav': 'Audio', 'mp3': 'Audio', 'ogg': 'Audio', 'pcm': 'Audio', 'json': 'Data', 'jsonl': 'Data', 'gz': 'Archive', 'zip': 'Archive'}.get(path.suffix[1:].lower(), 'Other')
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
    (ROOT / 'site/voice-library.json').write_text(json.dumps(library, indent=2) + '\n')
    (ROOT / 'site/catalogue.json').write_text(json.dumps(payload, indent=2) + '\n')
    if args.output:
        output = args.output.resolve()
        if output.exists():
            raise SystemExit('Output directory must not already exist')
        if output == ROOT or ROOT in output.parents and output.parts[len(ROOT.parts):len(ROOT.parts)+1] in [('artifacts',), ('reports',), ('docs',), ('site',)]:
            raise SystemExit('Choose a staging directory outside source content')
        output.mkdir(parents=True)
        for name in files + ['favicon.ico', 'site/voice-harness-mark.svg', 'index.html', 'vision.html', 'how-it-works.html', 'technical-architecture.html', 'test-setup.html', 'site/test-setup.js', 'executive-special.html', 'site/executive-special.css', 'site/executive-special.js', 'voice-library.html', 'executive-trust.html', 'site/style.css', 'site/app.js', 'site/catalogue.json', 'site/voice-library.css', 'site/voice-library.js', 'site/voice-library.json', 'site/cx-review-data.json']:
            dest = output / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, dest)
        (output / '.nojekyll').touch()
    print(f'Indexed {len(entries)} files; {len(features)} featured reports.')

if __name__ == '__main__':
    main()
