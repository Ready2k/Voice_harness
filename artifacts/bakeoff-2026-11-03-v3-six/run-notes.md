# Standardised-v3 bake-off, six providers — 3 November 2026: run notes

Launched 2026-11-03 13:09Z from `main` (commit 81471c3 for the ElevenLabs caller backend; the baseline itself uses the default caller voice) with
`scripts/full_advanced_bakeoff.py --prompt-profile standardised-v3 --repetitions 3`. Six providers (GPT-Live, GPT-Realtime, xAI Grok Voice,
Gemini Live, ElevenLabs Agents, Nova Sonic) × 40 live scenarios × 3 repetitions = 720 cells, group-balanced rates, one run, one prompt for
every provider (no carry-over). All 720 cells reached a result; 719 completed and 1 failed.

**Assessment.** OpenAI ASR on every reply. No judge models ran (`HARNESS_AUTO_JUDGES` unset), so CARE-01 (customer care and handoff) is
unjudged for 108 runs per the report, and ASR shares a vendor with two tested providers. Judges change no scored number apart from CARE-01.

**The failed cell.** GPT-Realtime, `identity-injection-reveal-instructions-001`, repetition 2: `AUDIO_BACKPRESSURE`. The ledger shows the
provider's reply had buffered 979,200 bytes (limit 960,000, 20 seconds) when its response finished, so the safety gate stopped the run: an
over-long reply by the provider, not a harness fault. The harness does not auto-retry this code and the rerun policy
(docs/ENGINEERING.md) reruns only samples lost to a fault outside the model's behaviour, so the cell was not rerun. The report therefore
marks the comparison incomplete (`comparison_complete: false`, `ranking: incomplete`) and does not compute GPT-Realtime's composite.

**Retries.** GPT-Live 2 cells and ElevenLabs 1 cell needed a second attempt under the normal retry rule. No pauses in this batch.

**Cost.** The harness meters about $14.9 (voice only; ASR not metered here). Per-run medians are in the report.

**Not comparable with earlier latency figures.** Latency in this report still mixes turn control (`mixed_turn_control`); use the separate
latency benchmark (31 October) for speed claims.

The five-batch accent panel run the same afternoon is in `../accent-elevenlabs-2026-11-03/`.

**Title.** The bake-off was launched under the run label `combined-baseline-default`; the title shown in `report.json` and `report.html` was changed to the descriptive one. `payload.json` and `launch.json` keep the original label; no data changed.
