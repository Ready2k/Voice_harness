# 26 October bake-off, re-scored 27–28 October 2026

This directory re-issues the 26 October report (`../report.json`, 600 cells) with one measurement error corrected and
task completion scored. It does not rerun anything. See `docs/PROVIDER_BIAS_REVIEW_2026-10-27.md` (findings 1–3) and
`docs/SCENARIO_VALIDITY_AUDIT_2026-10-27.md`.

Produced by:

```bash
PYTHONPATH=src .venv/bin/python scripts/rescore_bakeoff_report.py \
    artifacts/full-advanced-bakeoff-2026-10-26/report.json \
    artifacts/full-advanced-bakeoff-2026-10-26/rescored-2026-10-27
```

The script is deterministic: running it again gives byte-identical `report.json`, `report.html` and `summary.json`.

## What was changed

**1. Latency re-anchored (27 October).** These runs used provider-transport-v1. OpenAI Realtime, xAI Grok Voice and Gemini
Live were on **explicit commit**. Each caller turn ended with 1200 ms of trailing silence (`CALLER_TRAIL_MS`), and they
could not answer until the commit that followed it. First audio was timed from the end of speech, so every timed turn for
those three providers over-counts by exactly 1200 ms. Nova Sonic and ElevenLabs used their own turn detection and were
timed correctly.

- For OpenAI Realtime, xAI and Gemini, 1200 ms was subtracted from every timed turn: 162, 159 and 159 turns. The
  same was done for the content-audio detail under "More measures". These turns are marked `anchor: legacy_commit`,
  `turn_commit: explicit`.
- Nova and ElevenLabs turns are unchanged. They are marked `anchor: legacy_speech_end`, `turn_commit: provider_vad`.

**2. Task completion scored (28 October).** The original scorecard's "task success" was the assurance pass rate only, so
a run where the customer's request was never carried out could still pass. Nova never called `transfer_to_human` in
`happy-handoff-001` (it asked "May I ask why you'd like to speak with someone?" and the single-turn script ended; 0/3) and
passed all three. The re-score adds the `task_completion` dimension: completed runs of scenarios that declare expected
tools, where that sequence ran in order with the declared arguments. Scenarios that expect no tool are not counted. It is
weighted with the current defaults: assurance 0.30, task completion 0.15, factual 0.20, safe speech 0.15, responsiveness
0.15, robustness 0.05 (see `docs/BAKEOFF_SAFEGUARDS.md`).

**`happy-transaction-details-001` is excluded from task completion** (all 15 runs, reason
`TRANSACTION_ID_SPOKEN_FORM_ARTEFACT`). The caller said "transaction txn_001". The bank then matched the id exactly, so
xAI and ElevenLabs (`TXN001`, 3/3), Gemini (`TXN001` ×2, `txn001`) were denied `NOT_FOUND` for an id they heard
correctly; only OpenAI sent `txn_001`. The bank now accepts spoken forms (`spoken_ids`), but a denied call cannot be
replayed: the model never received the transaction, so those runs stay as observed and are left out rather than scored
on an artefact. (Nova sent `txn0001`, a different id that is still rejected; it is excluded with everyone else.) Counted
in, the scenario would give OpenAI 3/3 and the other four 0/3. Assurance is unchanged: all 15 runs passed it.

Nothing else was changed. Outcomes, criteria, safety and cost are identical. Scoring is the shipped `bakeoff.report`
through `bakeoff_export`. Before correcting anything the script re-scores the input unchanged (task completion weighted 0)
and checks that it reproduces the stored composites exactly. The run used `group_balanced: true`, so rates are the mean of
the scenario-group rates; the pooled counts are shown beside them.

## Results (default weights)

| Provider | Original composite (rank) | Latency fixed only | **Re-scored composite** | **Without latency** | Task completion (pooled) | Median / p90 first audio |
|---|---|---|---|---|---|---|
| xAI Grok Voice | 0.867 (2nd) | 0.920 | 0.932 | 0.996 | 100% (27/27) | 936 / 1272 ms |
| Gemini Live | 0.867 (3rd) | 0.915 | 0.924 | 0.994 | 98% (26/27) | 1019* / 1310* ms |
| Nova Sonic | **0.918 (1st)** | 0.918 | 0.921 | 0.988 | 94% (24/27) | 843 / 1795 ms |
| ElevenLabs | 0.837 (4th) | 0.837 | 0.858 | 0.967 | 98% (26/27) | 1982 / 3745 ms |
| OpenAI Realtime | 0.823 (5th) | 0.883 | 0.900 | 0.951 | 100% (27/27) | 858 / 1192 ms |

Rows are in the re-scored table order: the composite without latency, because latency is not like for like
(`verdict.ranking_basis: composite_excluding_latency`). On the full composite OpenAI (0.900) would sit above ElevenLabs.
Gemini's first audio is estimated from message size (*). Task completion misses: Nova `happy-handoff-001` ×3, Gemini
`happy-handoff-001` ×1 (also asked for a reason first), ElevenLabs `happy-verify-retry-001` ×1 (no balance lookup after
the retry). OpenAI is still last without latency because of its lower assurance pass rate (a FACT-01 balance mismatch in one
run each of `happy-verify-retry-001` and `identity-fault-caller-interrupts-001`).

**No ranks, and no winner.** No provider leads on safety and correctness: the paired interval for the leader's quality
lead is −0.004 to +0.017, every head-to-head has a Holm-adjusted p of 1.00, and no pair is separated on task completion
(with 27 runs and at most three misses per provider the intervals are wide). All five are "Tied". The random-weights
stability check is withheld. With group balancing, a provider with no misses has a degenerate 100–100% clustered interval
on task completion; the pooled count is the better guide.

## The corrected latency leans the other way

The explicit-commit providers' figures now start at the commit. A provider told exactly when the caller stopped pays no
end-of-speech detection delay, but Nova and ElevenLabs still pay theirs. The original figures were biased against
OpenAI, xAI and Gemini, and these re-scored figures are biased towards them. **The true ordering lies between the two,
and neither table can name a latency leader.** The report says so (`verdict.latency_comparable: false`,
`latency_comparability: mixed_turn_control`), marks responsiveness with †, and orders the table on the composite without
latency.

## Files

- `report.json`: the export data (`title`, `report`, `observations`) with corrected observations; the excluded runs carry
  `task_completion_excluded`.
- `report.html`: the standalone report, drawn by the shared renderer (`apps/web/bakeoff-report.mjs`).
- `summary.json`: turns corrected per provider, the task-completion exclusion, the weights, and three tables
  (`before`: original data under the current code with task completion weighted 0; `latency_only`; `after`) with the key
  verdict fields.
