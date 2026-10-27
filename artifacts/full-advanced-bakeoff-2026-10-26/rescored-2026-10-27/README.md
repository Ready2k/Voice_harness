# 26 October bake-off, re-scored 27 October 2026

This directory re-issues the 26 October report (`../report.json`, 600 cells) with one measurement error corrected. It
does not rerun anything. See `docs/PROVIDER_BIAS_REVIEW_2026-10-27.md`, findings 1–3.

Produced by:

```bash
PYTHONPATH=src .venv/bin/python scripts/rescore_bakeoff_report.py \
    artifacts/full-advanced-bakeoff-2026-10-26/report.json \
    artifacts/full-advanced-bakeoff-2026-10-26/rescored-2026-10-27
```

The script is deterministic: running it again gives byte-identical `report.json`, `report.html` and `summary.json`.

## What was changed

These runs used provider-transport-v1. OpenAI Realtime, xAI Grok Voice and Gemini Live were on **explicit commit**.
Each caller turn ended with 1200 ms of trailing silence (`CALLER_TRAIL_MS`), and they could not answer until the commit
that followed it. First audio was timed from the end of speech, so every timed turn for those three providers
over-counts by exactly 1200 ms. Nova Sonic and ElevenLabs used their own turn detection and were timed correctly.

- For OpenAI Realtime, xAI and Gemini, 1200 ms was subtracted from every timed turn: 162, 159 and 159 turns. The
  same was done for the content-audio detail under "More measures". These turns are marked `anchor: legacy_commit`,
  `turn_commit: explicit`.
- Nova and ElevenLabs turns are unchanged. They are marked `anchor: legacy_speech_end`, `turn_commit: provider_vad`.
- Nothing else was changed. Outcomes, criteria, safety, cost and the quality dimensions are identical.
- Scoring is the shipped `bakeoff.report` code, called through `bakeoff_export`. Before correcting anything, the
  script re-scores the input unchanged and checks that it reproduces the stored composites exactly.

## The corrected latency leans the other way

The explicit-commit providers' figures now start at the commit. A provider told exactly when the caller stopped pays no
end-of-speech detection delay, but Nova and ElevenLabs still pay theirs. The original figures were biased against
OpenAI, xAI and Gemini, and these re-scored figures are biased towards them. **The true ordering lies between the two,
and neither table can name a latency leader.** The report says so (`verdict.latency_comparable: false`,
`latency_comparability: mixed_turn_control`), marks responsiveness with †, and shows a composite without latency beside
the full one.

## Before and after (default weights)

| Provider | Median / p90 first audio, original | Re-scored | Composite, original | Re-scored | Without latency (unchanged) |
|---|---|---|---|---|---|
| xAI Grok Voice | 2136 / 2472 ms | 936 / 1272 ms | 0.867 (2nd) | 0.920 | 0.995 |
| Nova Sonic | 843 / 1795 ms | 843 / 1795 ms | **0.918 (1st)** | 0.918 | 0.998 |
| Gemini Live | 2219 / 2510 ms | 1019 / 1310 ms | 0.867 (3rd) | 0.915 | 0.997 |
| OpenAI Realtime | 2058 / 2392 ms | 858 / 1192 ms | 0.823 (5th) | 0.883 | 0.941 |
| ElevenLabs | 1982 / 3745 ms | 1982 / 3745 ms | 0.837 (4th) | 0.837 | 0.964 |

In both columns, Gemini's first audio is estimated from message size (marked * in the report).

The re-scored report gives **no ranks**. No provider leads on safety and correctness: every head-to-head has a
Holm-adjusted p of 1.00, and the paired interval for the leader's quality lead is −0.011 to 0.000. The scorecard
therefore lists all five providers as "Tied" in composite order. The random-weights stability check is withheld:
`rank_stable` and `weight_win_share` are null. The original report's `rank_stable: true` /
`weight_win_share: 1.0` for Nova came from the biased responsiveness score. The top three (xAI, Nova, Gemini) are
not separated on any dimension, responsiveness included. The "Where they differ" section of `report.html` sets this out
pair by pair.

## Files

- `report.json`: the export data (`title`, `report`, `observations`) with corrected observations.
- `report.html`: the standalone report, drawn by the shared renderer (`apps/web/bakeoff-report.mjs`).
- `summary.json`: turns corrected per provider, before and after tables, and the key verdict fields.
