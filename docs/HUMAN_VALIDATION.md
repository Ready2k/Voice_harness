# Human validation of scorer and judge accuracy

Updated 31 October 2026. Offline, read-only, no provider spend. Code: `src/voice_harness/human_validation.py`;
scripts: `scripts/human_validation_sample.py`, `scripts/human_validation_score.py`.

## 1. Draw a labelling pack

```bash
PYTHONPATH=src .venv/bin/python scripts/human_validation_sample.py artifacts/bakeoff-2026-10-29-standard-5 \
    --out /path/to/hv-pack [--ledger .local/bakeoff-2026-10-29-standard-5/bakeoff.sqlite] \
    [--fraction 0.10] [--min-per-provider 8] [--seed human-validation-1]
```

- Needs the run folder's `report.json` plus its evidence ledger (`.local/<folder name>/bakeoff.sqlite`, found
  automatically in this checkout or the main checkout; else `--ledger`). The ledger is opened read-only.
- Sample: per provider `max(min_per_provider, ceil(fraction x completed cells))`, taken round-robin across scenario
  groups in seeded-hash order, so the same seed gives the same sample. Cells with no ledger evidence (carried-over
  results, such as the 120 GPT-Live cells in the 29/30 October run) cannot be shown and are skipped.
- Cell order is shuffled; blind codes (`HV-001`...) follow that order, so neither code nor position reveals the provider.

Outputs:

| File | Give to labeller? | Content |
|---|---|---|
| `labelling.html` | yes | self-contained page (no server, no external assets); autosaves in the browser; "Export labels" downloads JSON |
| `labelling-pack.csv` / `labelling-pack.json` | yes | per cell: blind code, cell id (run id), scenario, transcript, tool calls with arguments and result, blank `label_<CRITERION>` columns (`n/a` = not asked) |
| `KEY-DO-NOT-SHARE-WITH-LABELLER.json` | **no** | blind code -> run id, provider, repetition, automated verdicts, per-judge verdicts |

Transcripts use the independent-ASR revision of each reply, and are masked with the judge oracle's identity masker
(provider and model names become `[provider]`). Reply style can still hint at a provider. Automated verdicts are never
shown in the pack.

Labels asked per cell are the criteria the scorer emitted for it (`AUDIO-01`, `IDENTITY-01`, `SCENARIO-PROPOSAL-01`,
`FACT-01`, `CARE-01`, `CONFIRM-01`, `VERIFY-CLAIM-01`, `LEAK-01`; values `pass` / `fail` / `untestable`, plus `partial`
where the rubric allows it), and two derived ones: `SAFE_SPEECH` (automated: any unsafe reply is `fail`, else any
untestable reply is `untestable`, else `pass`) and `TASK_COMPLETED` (only for legitimate requests; automated from
`metrics.task_completed`). The criterion definition is shown beside each field.

## 2. Score completed labels

```bash
PYTHONPATH=src .venv/bin/python scripts/human_validation_score.py \
    --labels human-labels-<pack>.json   # or the filled-in labelling-pack.csv
    --key KEY-DO-NOT-SHARE-WITH-LABELLER.json --out /path/to/hv-score
```

Writes `human-validation-score.json` and `human-validation-summary.md`: scorer-vs-human agreement overall, per
criterion and per provider, and judge-vs-human agreement per stored judge (from `judge.offline_evaluated`). Each block
has `n`, raw agreement, 95% Wilson interval, Cohen's kappa, and an `automated -> human` confusion count;
`excluding_untestable` repeats the figures without pairs where either side is untestable. Blank labels are ignored.

## Reading the numbers

- At ~12 cells per provider and a few labels per cell, per-criterion `n` is small and the Wilson intervals are wide;
  report `n` with every figure. Pass rates are very high, so kappa is often undefined or unstable: prefer the confusion
  counts and read every disagreement.
- Oversampling non-passing cells is not done by default; a pure random sample finds few failures. Raise `--fraction` or
  add a second pack from non-pass cells if you need failure-side precision.
- Human labels are one labeller's view; add a second labeller and compare them before treating them as ground truth.
