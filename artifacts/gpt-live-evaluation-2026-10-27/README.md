# GPT-Live 1 single-model evaluation

Forty synthetic catalogue scenarios, one run each, with standardised-v2 instructions, Marin voice,
GPT-Live 1, and a GPT-6 Luna Responses backend. The runtime adapter is revision `11fbaf4`;
the report renderer is bound by SHA-256 in `plan.json`. No other voice provider was tested.

## Results

- 39/40 runs completed on their first attempt; all 39 completed runs passed the declared assurance checks.
- Zero prohibited tool executions, identity failures, confirmation failures or audio-gate failures were recorded.
- The expected tool sequence was observed in 6/10 legitimate-request scenarios. Four were unserved:
  handoff, policy question, recent transactions and transaction details. These requests were declined or
  answered without using the expected tool. This is a task-completion problem in this configuration.
- Five factual checks had assessable claims and passed; 25 no-claim passes are excluded from factual accuracy.
- 13/13 tagged social-engineering/prompt-injection cases resisted the tested attacks.
- First non-silent response audio: median 1.467 s, p90 2.387 s, over 52 measured turns. First audio can be an
  acknowledgement. Tool result to subsequent speech: median 4.500 s over nine eligible turns; twelve
  already-speaking turns were excluded. These figures do not include gated playback assessment waits.
- The caller-interruption scenario timed out (`TURN_TIMEOUT`) with no assessable speech result. It is a
  failed diagnostic, not evidence that the underlying model cannot handle interruptions. The adapter has
  no native history-truncate operation and groups full-duplex speech using heuristics.
- The other three injected-fault scenarios completed with handled dispositions. Failed runs are excluded
  from that metric's denominator; the interruption timeout must be read alongside it.
- Provider-reported voice duration: 929 seconds; duration-only list-price cost $0.774167 at $0.05/minute.
  Rounded ledger lines sum to $0.774170. Backend tokens, assessment, judge calls and preflights are additional;
  their complete cost is not established here. Reported backend usage: 112,204 input and 3,674 output tokens.

The practical result is strong scoped safety handling and correct balance answers, with material gaps in
completing the broader advertised tool workflows. The next experiment should test more explicit delegation
capability instructions and improve full-duplex interruption handling. This run does not isolate prompt,
backend or transport effects, so those gaps should not be attributed to the voice model alone.

## Artifacts

- `matrix-full.json`: the 40 scored cells, including the failure; no scored retry was performed.
- `plan.json`: configuration, code revisions, scope and preserved preflight history.
- `report-data.json`, `summary.json`, `transcripts.json`: report inputs, compact results and independent transcripts.
- `evidence.jsonl.gz`: forty signed metadata evidence bundles, one JSON object per decompressed line.
- `public-keys.json`, `evidence-index.json`: verification keys, bundle hashes and ledger heads. All forty verified.
- `listening-samples.json` and three WAV files: selected assessed assistant speech, joined with half-second gaps.
  Source PCM hashes were checked against the ledger. Waits are excluded; use the report for latency.
- `pricing.json`: the existing pricing table plus GPT-Live duration pricing, read from the official model page
  on 2026-10-27. Backend usage remains unpriced rather than assumed to be free.
- `../../reports/gpt-live-1-evaluation-2026-10-27.html`: self-contained benchmark report.
- `../../reports/gpt-live-1-listening-2026-10-27.html`: self-contained audio players and transcripts.

The three preflight JSON files (`matrix.json`, `preflight-settling.json`, and
`preflight-input-settling.json`) are excluded from the scored 40. The first two exposed an adapter boundary
bug: an acknowledgement was assessed before the useful answer arrived. The final preflight independently
transcribed the full balance after the caller-speech and backend settling fix. Earlier attempts are retained
so this correction is visible. Preflight costs are excluded from the scored duration figure.

## Limits

One sample per scenario does not establish repeatability. The callers, account and payments are synthetic.
Speech activity is estimated from PCM RMS above 100; reply grouping waits for 900 ms of low-energy PCM,
no pending backend work, and three seconds after caller speech or backend completion. Runs remain
`diagnostic_unverified`. The judge (GPT-6 Sol) differs from both tested models, but judge and ASR are
OpenAI services. There is no cross-vendor judge or judge calibration report; one care check is untestable.
No subjective naturalness score was assigned. The listening clips let a human assess delivery.

Verify the compressed evidence locally:

```python
import gzip, json
from voice_harness.evidence import verify_bundle
keys = json.load(open('artifacts/gpt-live-evaluation-2026-10-27/public-keys.json'))
with gzip.open('artifacts/gpt-live-evaluation-2026-10-27/evidence.jsonl.gz', 'rt') as stream:
    assert all(verify_bundle(json.loads(line), keyring=keys) for line in stream)
```

Official protocol and price sources are linked in `docs/OPENAI_LIVE.md` and `pricing.json`.
