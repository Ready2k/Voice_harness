"""Inventory tracked assessment exports; revisions never become new experiments.

Reads retained exports only. No live calls, re-scoring or inferred model versions.
"""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LATEST = 'artifacts/bakeoff-2026-10-29-standard-5/report.html'
PATTERN = re.compile(r'<script[^>]*id="report-data"[^>]*>(.*?)</script>', re.S)


def build(root=ROOT, paths=None):
    if paths is None:
        git = shutil.which('git')
        paths = subprocess.check_output([git, 'ls-files', 'artifacts', 'reports'], cwd=root).decode().splitlines()  # noqa: S603  # nosec B603
    groups = {}
    for name in sorted(paths):
        if not name.endswith('.html'):
            continue
        path = root / name
        match = PATTERN.search(path.read_text())
        if not match:
            continue
        data = json.loads(match[1])
        if not isinstance(data.get('observations'), list) or 'report' not in data:
            continue
        source = data['source'].split(' · ')[0]
        report = data['report']
        candidate = path.with_suffix('.json')
        if candidate.is_file() and json.loads(candidate.read_text()) == data:
            payload = candidate.relative_to(root).as_posix()
        else:
            payload = name
        revised = 'rescored-' in name
        version = dict(path=name, data=payload, title=data['title'], generated=data['generated_at'],
                       sha256=hashlib.sha256((root / payload).read_bytes()).hexdigest(),
                       label='Re-scored assessment' if revised else 'Cost assessment' if name.endswith('-cost.html') else 'Original assessment',
                       priority=2 if revised else 1 if name.endswith('-cost.html') else 0,
                       runs=len(data['observations']), planned=report['progress']['cells'],
                       state=(report.get('readiness') or {}).get('state') or ('completed' if not report['progress']['pending'] else 'incomplete'))
        groups.setdefault(source, []).append(version)
    studies = []
    for source, versions in groups.items():
        versions.sort(key=lambda v: (v['priority'], v['generated'], v['path']), reverse=True)
        dates = re.findall(r'20\d{2}-\d{2}-\d{2}', versions[0]['path'])
        studies.append(dict(id=hashlib.sha256(source.encode()).hexdigest()[:12], source=source,
                            date=dates[0] if dates else '', versions=versions))
    studies.sort(key=lambda s: (s['versions'][0]['path'] == LATEST, s['date'], s['source']), reverse=True)
    return dict(studies=studies, scope='Tracked HTML assessment exports with structured report-data. Listening studies, raw probes and local ledgers are not scored assessments and are not counted here.')


if __name__ == '__main__':
    result = build()
    (ROOT / 'site/business-dashboard.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f'Indexed {len(result["studies"])} experiments and {sum(len(s["versions"]) for s in result["studies"])} assessment versions.')
