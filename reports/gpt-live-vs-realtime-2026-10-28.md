# GPT-Live 1 versus GPT-Realtime 2.1 — existing evidence

Uses the completed 28 October 2026 fair-harness bake-off, with the final claims-v2 / FACT-01 v3 re-score. No new model, transcription or judge calls were made for this comparison.

## Comparison basis

Both providers have the exact same 120 scenario/repetition keys: 40 scenarios × 3 repetitions, advanced-v1 provider-specific prompts, provider-transport-v3, default sampling and Marin voice. Both were assessed with independent ASR and the same two judges (GPT-6 Sol and Claude Haiku 4.5). This is a comparison of the configured systems, including GPT-Live's delegated reasoning backend. It excludes the older 27 October single-model diagnostic run.

## Results

Rates below are scenario-group balanced; the counts in parentheses are pooled and therefore may yield a different percentage. Assurance measures the scoped checks, separately from completing a customer's task.

| Metric | GPT-Live 1 | GPT-Realtime 2.1 |
|---|---:|---:|
| Assurance pass | 100.0% (120/120) | 97.0% (116/120) |
| Safe speech | 100.0% (167/167) | 99.2% (235/239) |
| Factual checks | 100.0% (17/17) | 85.7% (14/18) |
| Task completion | 96.3% (30/33) | 98.8% (32/33) |
| First audio median | 1,603 ms | 1,332 ms |
| First audio p90 | 2,307 ms | 1,707 ms |

Neither provider executed a prohibited tool action (0 across 120 runs each). The recorded composite excluding latency is 0.9935 for Live and 0.9523 for Realtime; the source report does not establish a statistically clear quality winner. On matched assurance outcomes, both passed 116 pairs, Live alone passed four, and Realtime alone passed none (two-sided p=0.125; original six-provider adjusted p=1.0).

## Interpretation and limitations

Live has better observed assurance and factual results; Realtime has better observed task completion. Realtime's four balance replies with ambiguous independently transcribed amounts remain untestable, not factual passes. Its safe-speech denominator also includes untestable replies. The confirmed-payment-cancellation scenario is excluded from task completion for both models because it lacked a clarification turn; the remaining denominator is 33 per provider.

Recorded first audio favours Realtime, but speed is not like for like: Realtime receives an explicit commit 500 ms after speech ends, while Live uses provider turn detection. Realtime's median becomes 1,132 / 1,332 / 1,532 ms under 300 / 500 / 700 ms commit assumptions, versus Live's estimated 1,603 ms. Live uses RMS speech activity and heuristic reply boundaries. First audio can be an acknowledgement rather than the useful answer.

Three repetitions provide limited evidence. The original run paused for credit top-ups and retried failed attempts. Judges lack calibration reports; OpenAI ASR and one judge share the tested models' vendor. No new subjective voice-naturalness or cost winner is claimed.

## Sources

- [Final full report](../artifacts/fair-bakeoff-2026-10-28/rescored-2026-10-28/report.html)
- [Scoring changes and limitations](../artifacts/fair-bakeoff-2026-10-28/rescored-2026-10-28/README.md)
- [Run conditions](../artifacts/fair-bakeoff-2026-10-28/run-notes.md)

Source report SHA-256: `aec19b0c3e0f27365965ef94676686ebe0e19f2481622cbf75d8a198fa481f66`.
