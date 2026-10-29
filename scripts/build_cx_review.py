"""Refresh the executive CX page from explicit, retained bake-off reports.

Use repeated --report paths to select assessments; separate studies stay separate.
No provider, transcription or judge calls are made.
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = [
    'artifacts/fair-bakeoff-2026-10-28/rescored-2026-10-28/report.json',
    'artifacts/full-advanced-bakeoff-2026-10-26/rescored-2026-10-27/report.json',
]


def build(paths):
    studies = []
    for name in paths:
        path = (ROOT / name).resolve()
        relative = path.relative_to(ROOT).as_posix()
        data = json.loads(path.read_text())
        report = data['report']
        observations = data['observations']
        if not observations or not report['scores']:
            raise ValueError(f'No completed assessment data: {relative}')
        groups = sorted({o['group'] for o in observations})
        providers = []
        for provider in sorted(data['providers'], key=lambda p: data['names'][p]):
            score = next(s for s in report['scores'] if s['provider'] == provider)
            rows = [o for o in observations if o['provider'] == provider]
            latency = next((s for s in report['latency'] if s['provider'] == provider), {})
            group_data = {}
            for group in groups:
                selected = [o for o in rows if o['group'] == group]
                group_data[group] = dict(total=len(selected), outcomes=dict(Counter(o['outcome'] for o in selected)))
            providers.append(dict(id=provider, name=data['names'][provider],
                                  metrics={k: score.get(k) for k in ['task_completion', 'task_success', 'factual', 'safe_speech']},
                                  outcomes=dict(Counter(o['outcome'] for o in rows)),
                                  runs=len(rows), groups=group_data, latency=latency,
                                  prohibited=sum(o.get('prohibited_executed', 0) for o in rows)))
        studies.append(dict(title=data['title'], generated=data['generated_at'], source=data['source'],
                            path=relative, report=str(Path(relative).with_suffix('.html')),
                            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            runs=len(observations), scenarios=len({o['scenario_id'] for o in observations}),
                            repetitions=len({o['repetition'] for o in observations}),
                            groups=groups, providers=providers,
                            no_clear_winner=report['verdict'].get('no_clear_winner'),
                            latency_comparable=report['verdict'].get('latency_comparable'),
                            limits=report.get('limits')))
    return dict(studies=studies)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', action='append', help='Repository-relative report JSON; first is the opening study')
    args = parser.parse_args()
    result = build(args.report or DEFAULTS)
    target = ROOT / 'site/cx-review-data.json'
    target.write_text(json.dumps(result, indent=2) + '\n')
    print(f'Refreshed {len(result["studies"])} assessments → {target}')
