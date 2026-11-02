#!/usr/bin/env python3
"""Validate authored transcript assessments and build an auditable static report.

This script aggregates the in-session AI review; it does not infer or generate scores.
"""
import csv
import hashlib
import html
import json
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'artifacts/voice-conversation-v2'
OUT = FOLDER / 'assessment'
AXES = {'clarity': 'Wording clarity', 'professionalism': 'Professionalism',
        'helpfulness': 'Helpfulness', 'accuracy': 'Scenario accuracy',
        'empathy': 'Appropriate empathy', 'continuity': 'Continuity'}
NAMES = {'openai-live': 'OpenAI GPT-Live', 'openai-realtime-live': 'OpenAI Realtime',
         'nova-sonic-live': 'Amazon Nova Sonic', 'xai-voice-live': 'xAI Grok Voice',
         'gemini-live': 'Google Gemini Live', 'elevenlabs-live': 'ElevenLabs Agents'}
FLAGS = {
    'capability': ('high', 'Unsupported action capability'),
    'false_reassurance': ('high', 'Unsupported broad safety assurance'),
    'false_reassurance_moderate': ('moderate', 'Overbroad reassurance'),
    'unsupported_fact': ('moderate', 'Unsupported balance or affordability inference'),
    'operational_detail': ('moderate', 'Unverified timing, channel or policy'),
    'unsupported_process': ('moderate', 'Unverified service process or scope'),
    'unsupported_contact': ('moderate', 'Unavailable contact details offered'),
    'unsupported_investigation': ('moderate', 'Unqualified investigation offer'),
    'replacement_details': ('minor', 'Unverified replacement details'),
    'channel_assumption': ('minor', 'Unverified app availability'),
    'ambiguous_action': ('minor', 'Action wording could mislead'),
    'generalisation': ('minor', 'Known outcome generalised'),
    'boundary': ('verification', 'Possible transcript turn-boundary issue'),
}
RUBRIC = {
    'clarity': 'Plain, precise, easy-to-follow wording and unambiguous amounts; no score for pronunciation or acoustic intelligibility.',
    'professionalism': 'Respectful, composed, suitably businesslike and proportionate wording; avoid excessive ceremony, casual shorthand and irrelevant material.',
    'helpfulness': 'Answers the current question with a useful amount of detail and a feasible next step when needed; respects requests for simplicity or no action.',
    'accuracy': 'Preserves fixture facts and tool limits; distinguishes supplied facts from uncertain policy or process; no invented actions, guarantees or account information.',
    'empathy': 'Acknowledgement fits the expressed emotion without exaggeration or patronising language. Neutral, patient language can earn 5 in a neutral scenario.',
    'continuity': 'Remembers corrections, name and preferences; responds to the current turn without stale details, unnecessary repetition or irrelevant facts.',
}
LIMITS = [
    'AI-authored transcript review of all 144 conversations / 432 assistant turns. One in-session Codex assessor; not a human panel, independent judge or fully blinded study. Scoring views used sample IDs, but provider context had already been seen.',
    'Scores concern provider-reported text only. Audio was not listened to or independently transcribed. Voice clarity, naturalness, pace, prosody, warmth of delivery, pronunciation, interruption handling and latency are unscored.',
    'Bracketed tags such as [happy], [sad] and [slow] are ignored for scoring; they do not prove how speech sounded or that tags were spoken.',
    'A possible turn-boundary flag lowers confidence in apparent coherence issues. Listen before attributing these to model behaviour; source transcripts are preserved unchanged.',
    'Scores are holistic ordinal judgements per three-turn conversation. Equal-weight averages are descriptive navigation aids, not probabilities, customer-specific utility or statistically established rankings. Dimensions overlap and are not independent.',
    'Both repeats are included, even when transcripts match. Identical transcripts have identical scores. Two repeats are not enough to establish reliability or generalise beyond these six synthetic situations.',
    'Accuracy concerns are assessed against the frozen fixture, not a real bank policy or legal standard. Generic conceptual explanations are allowed; definite account, timing, fees, channels, contact details and process guarantees need support. Conditional generic suggestions are not automatically errors.',
    'A high-risk flag means a potentially misleading claim in this test, not an executed banking action. No reviewed transcript is labelled as having actually completed a transfer or freeze.',
]

def esc(value):
    return html.escape(str(value), quote=True)


def aggregate(rows):
    return dict(n=len(rows), mean=round(mean(r['mean'] for r in rows), 3),
                min=round(min(r['mean'] for r in rows), 3), max=round(max(r['mean'] for r in rows), 3),
                axes={a: round(mean(r['scores'][a] for r in rows), 3) for a in AXES},
                high_risk=sum(any(f['severity'] == 'high' for f in r['flags']) for r in rows),
                moderate_risk=sum(any(f['severity'] == 'moderate' for f in r['flags']) for r in rows),
                needs_audio_check=sum(any(f['code'] == 'boundary' for f in r['flags']) for r in rows))


def build():
    plan = json.loads((FOLDER / 'plan.json').read_text())
    results = json.loads((FOLDER / 'results.json').read_text())
    jobs = {j['id']: j for j in plan['jobs']}
    with (OUT / 'assessments.psv').open() as f:
        authored = list(csv.DictReader(f, delimiter='|'))
    if len(authored) != len(jobs) or {r['id'] for r in authored} != set(jobs):
        raise ValueError('Assessment coverage must match every planned recording exactly')
    reviews = []
    for row in authored:
        job = jobs[row['id']]
        result = results['jobs'][row['id']]
        values = row['scores_CPHAEC']
        if len(values) != len(AXES) or any(v not in '12345' for v in values):
            raise ValueError(f'Invalid scores: {row}')
        turn = int(row['turn'])
        if result['state'] != 'completed' or row['quote'] not in result['turns'][turn - 1]['transcript']:
            raise ValueError(f'Unsupported evidence: {row}')
        flags = [dict(code=code, severity=FLAGS[code][0], label=FLAGS[code][1])
                 for code in row['flag'].split(';') if code]
        profile = plan['profiles'][job['provider']][job['condition']]
        scores = dict(zip(AXES, map(int, values), strict=False))
        reviews.append(dict(**job, provider_name=NAMES[job['provider']],
                            model=result.get('returned_model_id') or profile['model_id'],
                            model_source='returned' if result.get('returned_model_id') else 'requested',
                            voice=profile['voice'], scores=scores, mean=mean(scores.values()),
                            assessment=row['assessment'], evidence=[dict(turn=turn, quote=row['quote'])],
                            flags=flags, confidence='low: verify turn alignment' if 'boundary' in row['flag'] else 'moderate: transcript only',
                            audio_assessed=False, clip=f'clips/{job["id"]}.flac'))
    # Identical transcript evidence must never acquire different scores from identity or condition.
    seen = {}
    for r in reviews:
        key = ' '.join(' '.join(t['transcript'].split()) for t in results['jobs'][r['id']]['turns'])
        if key in seen and seen[key] != r['scores']:
            raise ValueError(f'Identical transcripts scored differently: {r["id"]}')
        seen[key] = r['scores']
    summary = []
    for s in plan['scenarios']:
        for condition in ['common', 'tuned']:
            for provider in NAMES:
                subset = [r for r in reviews if (r['scenario'], r['condition'], r['provider']) == (s['id'], condition, provider)]
                if len(subset) != plan['repetitions']:
                    raise ValueError('Missing matched repeats')
                summary.append(dict(scenario=s['id'], condition=condition, provider=provider, **aggregate(subset)))
    overview = [dict(provider=p, condition=c, **aggregate([r for r in reviews if r['provider'] == p and r['condition'] == c]))
                for p in NAMES for c in ['common', 'tuned']]
    shortlists = []
    for s in plan['scenarios']:
        for c in ['common', 'tuned']:
            eligible = [x for x in summary if x['scenario'] == s['id'] and x['condition'] == c and x['high_risk'] == 0]
            top = max(x['mean'] for x in eligible)
            chosen = [x['provider'] for x in eligible if x['mean'] == top]
            shortlists.append(dict(scenario=s['id'], condition=c, providers=chosen, mean=top,
                                   rule='Highest mean among configurations with no high-risk flag in either repeat. Exact ties retained. Moderate/minor flags and audio verification still require review.'))
    payload = dict(version='conversation-transcript-assessment-v1', assessed_on='2026-11-01',
                   assessor='Codex, single in-session qualitative review', plan_hash=results['plan_hash'],
                   source_sha256={f: hashlib.sha256((FOLDER / f).read_bytes()).hexdigest() for f in ['plan.json', 'results.json', 'assessment/assessments.psv']},
                   rubric=RUBRIC, anchors={'1': 'Fails the dimension', '2': 'Significant problem', '3': 'Adequate with a notable weakness', '4': 'Good with a minor weakness', '5': 'Excellent in this scenario; no material weakness observed'},
                   dimensions=list(AXES), aggregation='Unweighted mean of six ordinal dimensions, then equal mean across conversations. Common and tuned remain separate.',
                   limitations=LIMITS, summary=summary, overview=overview, shortlists=shortlists, reviews=reviews)
    (OUT / 'scores.json').write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n')
    with (OUT / 'scores.csv').open('w') as f:
        fields = ['id', 'provider', 'model', 'scenario', 'condition', 'repetition', *AXES, 'mean', 'risk_flags', 'evidence_turn', 'evidence_quote', 'assessment', 'confidence']
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for r in reviews:
            writer.writerow({**{k:r[k] for k in fields if k in r}, **r['scores'], 'mean':round(r['mean'], 3),
                             'risk_flags':'; '.join(f['code'] for f in r['flags']), 'evidence_turn':r['evidence'][0]['turn'], 'evidence_quote':r['evidence'][0]['quote']})
    make_report(payload, plan, results)
    print(f'Validated and reported {len(reviews)} assessments, {len(summary)} matched comparisons.')
    for s in shortlists:
        print(s['scenario'], s['condition'], ', '.join(NAMES[p] for p in s['providers']), f'{s["mean"]:.2f}')
    print('High-risk recordings:', sum(any(f['severity']=='high' for f in r['flags']) for r in reviews))


def make_report(data, plan, results):
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Conversation assessment · 144 recordings</title><style>body{font:16px/1.6 system-ui;color:#18333b;background:#f4f7f5;margin:0}main{max-width:1250px;margin:auto;padding:28px}h1{font-size:clamp(30px,5vw,48px);line-height:1.1}h2{margin-top:36px}a{color:#09685e}table{border-collapse:collapse;width:100%;font-size:14px;background:white}th,td{text-align:left;padding:10px;border-bottom:1px solid #ccd9d8;vertical-align:top}th{background:#e5efea}.scroll{overflow:auto}.notice{padding:18px;background:#e5efea;border-radius:12px}.high{color:#982d1e;font-weight:700}.muted{color:#536970;font-size:14px}.sample{background:white;border:1px solid #ccd9d8;padding:18px;border-radius:12px;margin:14px 0;scroll-margin-top:12px}summary{cursor:pointer;font-weight:650}blockquote{border-left:3px solid #96b6a8;margin:12px 0;padding-left:14px}audio{width:100%;max-width:550px}code{overflow-wrap:anywhere}li{margin:7px 0}.tags{display:flex;gap:12px;flex-wrap:wrap}section{scroll-margin-top:12px}@media(max-width:600px){main{padding:16px}th,td{padding:8px}}</style></head><body><main>']
    out += ['<p><a href="../review.html">← Quick business review</a> · <a href="scores.csv" download>Download scores (CSV)</a> · <a href="scores.json" download>Full assessment (JSON)</a></p>',
            '<h1>Which responses worked best?</h1><p>144 conversations · 432 assistant turns · 6 provider/model configurations · assessed 1 November 2026</p>',
            '<div class="notice"><strong>Transcript assessment, not a listening verdict.</strong> These AI scores assess wording and behaviour in the saved transcripts. Audio quality is unscored. Use the scenario shortlists as leads for customer listening, not established product winners.</div>']
    high = sum(any(f['severity'] == 'high' for f in r['flags']) for r in data['reviews'])
    boundary = sum(any(f['code'] == 'boundary' for f in r['flags']) for r in data['reviews'])
    out.append(f'<p><strong>{high} recordings have high-risk wording flags</strong>; {boundary} need an audio check for possible turn-boundary issues. All 144 have evidence-backed notes. A high score elsewhere does not cancel a risk flag.</p>')
    out.append('<p class="notice">Turn-boundary concerns can come from recording/transcript alignment rather than the model. Scores affected by these concerns are provisional and could change after listening. This particularly affects the Nova Sonic saved transcripts; do not interpret the table as a confirmed spoken-quality ranking.</p>')
    out.append('<p class="notice"><a href="../../regional-caller-2026-11-01/index.html">Regional caller understanding ↗</a> adds a separate synthetic accent panel, default-voice baselines and recovered assistant response excerpts. These tool-task results are not part of the conversation-quality scores.</p><h2>Customer-facing interpretation</h2><ul><li><strong>OpenAI Realtime is the strongest broad starting point in this transcript review:</strong> its tuned configuration leads the aggregate mean and the anxious-caller and bereavement shortlists. It still has some unsupported available-balance claims and can be verbose.</li><li><strong>Google Gemini is particularly strong on direct factual replies:</strong> it leads failed-payment reassurance in both conditions and performs well on balance and continuity. Its bereavement replies contain unsupported connection/callback offers, so those strengths do not make it a blanket recommendation.</li><li><strong>Correction handling benefits from checking the exact sample:</strong> many providers remember fifty pounds and Alice, but stale details, unnecessary proceeding prompts and available-funds assumptions weaken some exchanges.</li><li><strong>Use common and tuned results separately:</strong> tuning is associated with a higher aggregate text score for every configuration here, but these are just two repeats of six known situations, not evidence of generalised improvement.</li></ul>')
    out.append('<p class="tags">' + ' '.join(f'<a href="#{esc(s["id"])}">{esc(s["title"])}</a>' for s in plan['scenarios']) + ' <a href="#method">Scoring method</a></p>')
    out.append('<h2>Where to start listening</h2><p>Highest descriptive mean in each scenario and condition, excluding any configuration with a high-risk flag in either repeat. Exact ties are retained. Small gaps are not established differences.</p><div class="scroll"><table><thead><tr><th>Scenario</th><th>Common prompt shortlist</th><th>Tuned prompt shortlist</th></tr></thead><tbody>')
    for s in plan['scenarios']:
        out.append(f'<tr><th><a href="#{s["id"]}">{esc(s["title"])}</a></th>')
        for c in ['common', 'tuned']:
            x = next(x for x in data['shortlists'] if x['scenario']==s['id'] and x['condition']==c)
            out.append('<td>' + '<br>'.join(esc(NAMES[p]) for p in x['providers']) + f'<br><strong>{x["mean"]:.2f} / 5</strong></td>')
        out.append('</tr>')
    out.append('</tbody></table></div>')
    out.append('<h2>Across the six scenarios</h2><p>Each condition averages 12 conversations per configuration: six scenarios × two repeats. The delta is tuned minus common on these same scenarios. It is descriptive, not proof of generalised improvement. H/M counts are recordings with high/moderate flags; one recording can have both.</p><div class="scroll"><table><thead><tr><th>Provider / recorded model</th><th>Common mean</th><th>Tuned mean</th><th>Δ</th><th>Common H / M</th><th>Tuned H / M</th></tr></thead><tbody>')
    for p in NAMES:
        a,b = [next(x for x in data['overview'] if x['provider']==p and x['condition']==c) for c in ['common','tuned']]
        models = sorted({r['model'] for r in data['reviews'] if r['provider']==p})
        out.append(f'<tr><th>{esc(NAMES[p])}<br><span class="muted">{esc(", ".join(models))}</span></th><td>{a["mean"]:.2f}</td><td>{b["mean"]:.2f}</td><td>{b["mean"]-a["mean"]:+.2f}</td><td>{a["high_risk"]} / {a["moderate_risk"]}</td><td>{b["high_risk"]} / {b["moderate_risk"]}</td></tr>')
    out.append('</tbody></table></div><p class="muted">Model identifiers are retained recording metadata, not a current model catalogue. ElevenLabs agent metadata does not identify its underlying language model.</p>')
    for s in plan['scenarios']:
        out.append(f'<section id="{s["id"]}"><h2>{esc(s["title"])}</h2><p>{esc(s["review"])}</p>')
        for c in ['common','tuned']:
            out.append(f'<h3>{c.title()} prompt</h3><p class="muted">Each row covers both repeats. C = wording clarity; P = professionalism; H = helpfulness; A = accuracy; E = empathy; F = continuity. Range is the two conversation means.</p><div class="scroll"><table><thead><tr><th>Provider</th><th>Mean / range</th>' + ''.join(f'<th title="{esc(v)}">{letter}</th>' for letter,v in zip('CPHAEF',AXES.values(), strict=False)) + '<th>Risk / verification</th><th>Evidence</th></tr></thead><tbody>')
            group = sorted([x for x in data['summary'] if x['scenario']==s['id'] and x['condition']==c], key=lambda x:(-x['mean'],x['provider']))
            for x in group:
                subset = sorted([r for r in data['reviews'] if r['scenario']==s['id'] and r['condition']==c and r['provider']==x['provider']],key=lambda r:r['repetition'])
                out.append(f'<tr><th>{esc(NAMES[x["provider"]])}</th><td><strong>{x["mean"]:.2f}</strong><br>{x["min"]:.2f}–{x["max"]:.2f}</td>' + ''.join(f'<td>{x["axes"][a]:.1f}</td>' for a in AXES) + f'<td><span class="{"high" if x["high_risk"] else ""}">{x["high_risk"]} high</span>, {x["moderate_risk"]} moderate<br>{x["needs_audio_check"]} audio checks</td><td>' + '<br>'.join(f'<a href="#sample-{r["id"]}">Repeat {r["repetition"]}</a>' for r in subset) + '</td></tr>')
            out.append('</tbody></table></div>')
        out.append('<h3>Individual assessments & evidence</h3>')
        for r in sorted([r for r in data['reviews'] if r['scenario']==s['id']],key=lambda r:(list(NAMES).index(r['provider']),r['condition'],r['repetition'])):
            out.append(f'<article class="sample" id="sample-{r["id"]}"><h3>{esc(r["provider_name"])} · {r["condition"]} · repeat {r["repetition"]} · {r["mean"]:.2f}/5</h3><p class="muted">{esc(r["model"])} ({r["model_source"]}) · sample {r["id"]} · confidence: {esc(r["confidence"])}</p><p>' + ' · '.join(f'{esc(AXES[a])}: <strong>{v}</strong>' for a,v in r['scores'].items()) + '</p>')
            if r['flags']:
                out.append('<ul>' + ''.join(f'<li class="{"high" if f["severity"]=="high" else ""}">{esc(f["severity"].title())}: {esc(f["label"])}</li>' for f in r['flags']) + '</ul>')
            out.append(f'<p>{esc(r["assessment"])}</p><blockquote>Turn {r["evidence"][0]["turn"]}: “{esc(r["evidence"][0]["quote"])}”</blockquote><details><summary>Check the full transcript and recording</summary>')
            for i,t in enumerate(results['jobs'][r['id']]['turns']):
                out.append(f'<p><strong>Caller {i+1}:</strong> {esc(s["turns"][i])}</p><p><strong>Assistant {i+1}:</strong> {esc(t["transcript"])}</p>')
            out.append(f'<audio controls preload="none" src="../{r["clip"]}" aria-label="{esc(r["provider_name"])} {esc(s["title"])} {r["condition"]} repeat {r["repetition"]}"></audio><p><a href="../{r["clip"]}">Open recording</a></p></details></article>')
        out.append('</section>')
    out.append('<section id="method"><h2>Scoring method and limits</h2><p>Frozen facts: current balance £1,250.40; the failed £42 payment took no money; freezing is temporary and reversible; replacement is separate; bereavement support is available. The caller is verified and the assistant has no action tools.</p><ul>' + ''.join(f'<li><strong>{esc(AXES[a])}:</strong> {esc(v)}</li>' for a,v in RUBRIC.items()) + '</ul><p>' + ' · '.join(f'{k} = {esc(v)}' for k,v in data['anchors'].items()) + '</p><ul>' + ''.join(f'<li>{esc(x)}</li>' for x in LIMITS) + '</ul><p>Coverage, exact quote provenance, identical-transcript consistency and matched repeat counts are checked by the report builder. Numeric scores are authored judgements, not generated by keyword counting. The PSV file preserves those judgements for audit.</p><p><a href="assessments.psv">Authored assessment records</a> · <a href="../plan.json">Frozen plan</a> · <a href="../results.json">Source transcripts</a></p></section>')
    out.append('<script>document.addEventListener("play",e=>{if(e.target.tagName==="AUDIO")document.querySelectorAll("audio").forEach(a=>{if(a!==e.target)a.pause()})},true)</script></main></body></html>')
    (OUT / 'index.html').write_text('\n'.join(out))


if __name__ == '__main__':
    build()
