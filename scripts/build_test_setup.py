"""Build the public setup explorer from dated retained artifacts; never import live defaults."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = [
    ('bakeoff-2026-10-29-standard-5', '29 October 2026 · Standard-prompt bake-off', '.'),  # report.json sits at the root: no re-score revision
    ('fair-bakeoff-2026-10-28', '28 October 2026 · Fair bake-off', 'rescored-2026-10-28'),
    ('full-advanced-bakeoff-2026-10-26', '26 October 2026 · Advanced bake-off', 'rescored-2026-10-27'),
]

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()

def variants(values):
    counted = {}
    for value in values:
        key = json.dumps(value, sort_keys=True, ensure_ascii=False)
        counted[key] = counted.get(key, 0) + 1
    return [{'value': json.loads(key), 'observations': n} for key, n in sorted(counted.items())]

def main():
    experiments = []
    for folder, label, revision in EXPERIMENTS:
        base = ROOT / 'artifacts' / folder
        sources = {}
        loaded = {}
        for key, relative in [('requested', 'prompts.json'), ('bindings', 'verified-prompt-bindings.json'), ('report', revision + '/report.json')]:
            path = base / relative
            raw = path.read_bytes()
            sources[key] = {'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(raw).hexdigest()}
            loaded[key] = json.loads(raw)
        report, bindings, requested = loaded['report'], loaded['bindings'], loaded['requested']
        disclosures = report['report'].get('limits', {}).get('provider_disclosures', {})
        configs = {c['provider']: c for c in disclosures.get('providers', [])}
        common = set.intersection(*(set(b['instructions'].splitlines()) - {''} for b in bindings.values()))
        first_lines = next(iter(bindings.values()))['instructions'].splitlines()
        common_lines = list(dict.fromkeys(line for line in first_lines if line in common))
        providers = []
        for provider in report['providers']:
            binding = bindings[provider]
            tools_json = json.dumps(binding['tools'], sort_keys=True, separators=(',', ':'), ensure_ascii=False)
            checks = {'instructions_hash_matches': digest(binding['instructions']) == binding['instructions_sha256'],
                      'tools_hash_matches': digest(tools_json) == binding['tools_sha256'],
                      'requested_instructions_match_binding': requested[provider]['instructions'] == binding['instructions'],
                      'requested_tools_match_binding': requested[provider]['tools'] == binding['tools']}
            delivered = binding.get('delivered', {}).get('models', [])
            checks['delivered_hashes_match'] = all(digest(m['instructions']) == m['instructions_sha256'] for m in delivered) if delivered else None
            rows = [o for o in report['observations'] if o['provider'] == provider]
            providers.append({'id': provider, 'name': report['names'].get(provider, provider), 'binding': binding,
                'checks': checks, 'config': configs.get(provider), 'observations': len(rows),
                'differences': [line for line in binding['instructions'].splitlines() if line and line not in common],
                'settings': {key: variants([(o.get('parity') or {}).get(key) for o in rows]) for key in
                    ['voice', 'voice_source', 'sampling_policy', 'sampling_overrides', 'output_limits', 'stored_agent_temperature',
                     'provider_input_rate', 'client_truncation', 'commit_delay_ms', 'confirmation_delivery', 'context_channel', 'agent_llm']},
                'turn_control': variants([o.get('turn_commit') for o in rows]),
                'audio_profiles': variants([o.get('audio_profile') for o in rows]),
                'asr': variants([[{k: a.get(k) for k in ['backend','model','vendor','live']} for a in (o.get('assessors') or {}).get('asr', [])] for o in rows]),
                'judges': variants([(o.get('assessors') or {}).get('judges') for o in rows]),
                'assessment_disclosures': variants([{k:(o.get('assessors') or {}).get(k) for k in ['asr_disclosures','judge_coverage','judge_disclosure','required_judges']} for o in rows])})
        experiments.append({'id': folder, 'label': label, 'generated_at': report.get('generated_at'), 'sources': sources,
            'results': str((base / revision / 'report.html').relative_to(ROOT)), 'notes': str((base/'run-notes.md').relative_to(ROOT)),
            'common_lines': common_lines, 'providers': providers, 'limitations': disclosures.get('limitations', [])})
    data = json.dumps(experiments, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('&', '\\u0026')
    template = (ROOT/'site/test-setup.template.html').read_text()
    (ROOT/'test-setup.html').write_text(template.replace('<!-- SETUP_DATA -->', data))
    print(f'Built test-setup.html: {len(experiments)} experiments, {sum(len(e["providers"]) for e in experiments)} provider setups')

if __name__ == '__main__':
    main()
