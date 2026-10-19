"""Add assessment and provenance notes to the freshly generated HTML for this batch."""
import json,re
from pathlib import Path
p=Path('reports/voice-provider-benchmark-5350e4a-2026-10-19.html')
s=p.read_text()
pattern=r'(<script type="application/json" id="report-data">)(.*?)(</script>)'
m=re.search(pattern,s,re.S); data=json.loads(m.group(2))
if data['report'].get('assessment_limitation'):
    raise SystemExit('Report already annotated; regenerate the base HTML before applying notes.')
notice='Independent ASR returned HTTP 429 after its credit balance was exhausted. Nine runs are untestable (Nova 1, Gemini 3, xAI 5). Raw scores and pairwise p-values below include this shared assessment outage and must not be interpreted as provider quality differences. All 132 first attempts are retained; no retries or model fallback were used.'
data['report']['assessment_limitation']=notice
data['report']['analysis_status']='assessment_limited'
s=s[:m.start(2)]+json.dumps(data,separators=(',',':')).replace('</','<\\/')+s[m.end(2):]
s=s.replace('export function verdictText(report, names) {','export function verdictText(report, names) {\n  if (report.assessment_limitation) return {lead: "No provider recommendation: independent ASR credits were exhausted.", sub: report.assessment_limitation};')
s=s.replace('el("p", "bk-eyebrow", "Recommendation")','el("p", "bk-eyebrow", "Assessment limitation")')
s=s.replace('"Head-to-head on the same scenarios"','"Observed paired outcomes (ASR-limited)"')
s=s.replace('Every number is computed from recorded run evidence; run IDs are in the scenario matrix.','Every number is computed from recorded run evidence; run IDs are in the scenario matrix. Source revision 5350e4a0784f857fc90c2dee5c4fea3a24a6413b. Requested models: Gemini gemini-3.8-live; Nova amazon.nova-2-5-sonic; OpenAI gpt-realtime-2.1; xAI grok-voice-3.0. Fresh API process, 33 scenarios per provider, one attempt per cell, up to 8 concurrent runs and 2 per provider. No local test suite or component benchmark was launched during the matrix; other host activity was not controlled. The component benchmark ran afterward. Timeout-injection audit: 5 events within the 4 declared tool-error cells, zero outside their scope. ASR HTTP 429 affected 9 runs; availability check confirmed credit_balance_exhausted. These results do not support a provider recommendation.')
s=s.replace('"Weighted scorecard"','"Raw scorecard (ASR-limited)"')
s=s.replace('Only scenarios where exactly one provider passed can separate a pair. p is the exact two-sided McNemar test; below 0.05 counts as a real difference.','Raw paired outcomes and McNemar p-values are retained for traceability. Missing ASR assessments count as non-passes here; these comparisons cannot establish provider quality differences.')
s=s.replace('p.better ? pill(SHORT[p.better] + " is better", "bk-ok") : pill("No significant difference", "bk-muted")','pill("ASR-limited: no inference", "bk-muted")')
p.write_text(s)
summary_path=Path('artifacts/benchmark-5350e4a/summary.json'); summary=json.loads(summary_path.read_text()); summary['assessment_limitation']={'reason':'credit_balance_exhausted','error_type':'insufficient_quota','http_status':429,'affected_runs':9,'by_provider':{'nova-sonic-live':1,'gemini-live':3,'xai-voice-live':5},'provider_recommendation':None}; summary_path.write_text(json.dumps(summary,indent=2)+'\n')
