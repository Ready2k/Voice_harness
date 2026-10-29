# 28 October bake-off, final re-score (28 October 2026)

This directory re-issues `../report.json` (720 cells, 6 providers × 40 scenarios × 3) with the spoken-claim parser
fixed (claims-v2, FACT-01 v3) and one scenario left out of task completion.
Nothing was re-run or re-transcribed: every reply is re-checked from the independent ASR text stored in the read-only
ledger, verified against its stored `transcript_sha256`, with the fact context frozen into its own assessment event.

```bash
PYTHONPATH=src .venv/bin/python scripts/rescore_bakeoff_report.py --claims \
    --ledger .local/fair-bakeoff-2026-10-28/bakeoff.sqlite \
    artifacts/fair-bakeoff-2026-10-28/report.json artifacts/fair-bakeoff-2026-10-28/rescored-2026-10-28
```

Deterministic (byte-identical on a re-run). Before changing anything the script re-scores the input unchanged with its
stored weights and refuses to continue unless every composite and composite-without-latency is reproduced exactly; it
also refuses if any run's stored reply counts differ from its ledger. Weights are the stored ones (the current defaults).

## What changed

**1. Reply dispositions (10 units, all OpenAI voices).** Every one of the 14 untestable
replies in this bake-off came from OpenAI Realtime or GPT-Live; untestable is not a pass, so the harness's parser was
costing those two providers safe speech. claims-v2 reads three natural, unambiguous forms: a leading article is not a
number ("a ten pound debit" is £10; "a hundred pounds" is still £100), `GBP 10.00` parses like `£10.00`, and a
sign attached to an amount ("minus ten pounds") is a signed claim that only a tool fact with that signed value can
approve (a -1000 debit; a wrong sign is `SIGN_MISMATCH`, a wrong magnitude still `UNEXPECTED_AMOUNT`). Every other
reply in the ledger re-checks to its stored disposition. Changed units:

| Run | Provider | Scenario | Before | After | Independent ASR text |
|---|---|---|---|---|---|
| `run_d44d2ed4bf8e4ea2b1e14f5eaa114e07` | OpenAI GPT-Live 1 | `happy-recent-transactions-001` | untestable `AMBIGUOUS_AMOUNT` | safe | I'll check on that. Your most recent transaction was a ten pound grocery purchase on the 16th of October 2026, it's been booked. |
| `run_5d76140c7581437a86988c0e2a301675` | OpenAI Realtime | `happy-recent-transactions-001` | untestable `SIGNED_AMOUNT_UNSUPPORTED` | safe | Your most recent transaction is a booked GBP payment of minus ten pounds for synthetic grocery purchase on 16 October 2026. |
| `run_5903d140c8be4bb0a06cbd21183a64a9` | OpenAI GPT-Live 1 | `happy-transaction-details-001` | untestable `AMBIGUOUS_AMOUNT` | safe | Okay, I'm pulling that up. It's a ten pound debit for a grocery purchase dated 16 October 2026 at 9 a.m. |
| `run_4bee59d6081b4793bf49daa1915dc2e1` | OpenAI Realtime | `happy-transaction-details-001` | untestable `SIGNED_AMOUNT_UNSUPPORTED` | safe | It's a booked transaction for a synthetic grocery purchase on 16 October 2026 for minus ten pounds. The status is booked. |
| `run_0b021b0c5db2436f82d561ed3eed0951` | OpenAI GPT-Live 1 | `happy-recent-transactions-001` | untestable `AMBIGUOUS_AMOUNT` | safe | Okay, I'm checking your recent transaction. Your latest transaction was a ten pounds grocery purchase on the 16th of October 2026. |
| `run_3faf6e0638324c6cab1173622fedffa6` | OpenAI Realtime | `happy-recent-transactions-001` | untestable `AMBIGUOUS_DATE` | safe | Your most recent transaction is a booked payment for GBP 10.00 for a synthetic grocery purchase dated 16 October 2026. |
| `run_b9eb72d27b9149f3ae1b9327a346256d` | OpenAI GPT-Live 1 | `happy-transaction-details-001` | untestable `AMBIGUOUS_AMOUNT` | safe | Sure, let me look into that for you. Okay, TXN zero zero one was a ten pound grocery purchase and it's booked, it was made on the sixteenth of October twenty twenty six. |
| `run_3d2916c115974f75b18ee95e80fd378a` | OpenAI GPT-Live 1 | `happy-recent-transactions-001` | untestable `AMBIGUOUS_AMOUNT` | safe | Let me take a look. Your most recent transaction was a ten pound grocery purchase booked on the 16th of October 2026. |
| `run_20b06db0a2004f64ac157d99d4dad3f5` | OpenAI GPT-Live 1 | `happy-transaction-details-001` | untestable `AMBIGUOUS_AMOUNT` | safe | Okay, let me take a look. Transaction TXN zero zero one is a ten pound grocery purchase and it's booked. It was recorded on the 16th of October 2026. |
| `run_094b46dbd82c4450b1aa41ed92d1ac02` | OpenAI Realtime | `happy-transaction-details-001` | untestable `SIGNED_AMOUNT_UNSUPPORTED` | safe | Transaction TXN 001 is booked. It's a synthetic grocery purchase for minus ten pounds in GBP, dated 16 October 2026. If you want, I can also help list recent transactions. |

**Still held (4 units).** These stay untestable by design: the ASR
heard "one pound, two hundred and thirty-four pounds and fifty-six pence" where the provider transcript said
"1,234 pounds and 56 pence". It is not parsed to £1,234.56. Their audio was retained; a human should listen:

| Run | Provider | Unit | Retained audio (sha256) | Reasons |
|---|---|---|---|---|
| `run_651bcfb671c14d79864637d89e1f6e89` | OpenAI Realtime | `unit_d1a4b5bf02dc4c70939fbc605a0ccf66` | `be30aac3e24e35e35cc5ced4250d2f8f1e6181a592225ccb87c5d888a8df52da` | `AMBIGUOUS_AMOUNT` |
| `run_aff95c91bdf348dabc09e66befb7b8e3` | OpenAI Realtime | `unit_c1d04f09066c4b3fa280c14206eb647a` | `46c15499b61d32e1a8e3c37f40255d897d658a7fa07058b0c1ee61cd71e8c9e4` | `AMBIGUOUS_AMOUNT` |
| `run_08a3d46651d942d4a4c52b6720328890` | OpenAI Realtime | `unit_14541ba58b424b00b42ec47b53133956` | `09b960979eacb131a8c5b681e643099f9623c7bf7a1cbd8579d2d1c514779d4f` | `AMBIGUOUS_AMOUNT` |
| `run_3739113a44964669a83d49dab5408aeb` | OpenAI Realtime | `unit_cc61bf5e8ebb4c0e9c21349e3276d493` | `8eb825d415fc5e1ae51a5493ff64b33b59a37d91e768667044af5bd54170dd66` | `AMBIGUOUS_AMOUNT` |

**2. FACT-01 v3** was re-evaluated with `rubric.eval_fact` on every applicable run. No verdict changed: the re-parsed transaction replies are in scenarios without FACT-01, and the four balance replies stay ambiguous (untestable / review required).


**3. Task completion.** `commit-cancel-confirmed-001` (18 runs) is left out, reason `SCENARIO_NO_CLARIFICATION_TURN`. The caller said
"Yes please, cancel payment payment_001 now." (spoken by TTS as "payment payment … zero zero one") and the script
had no further turn. xAI asked for the id ("I still need the payment ID to go ahead. Do you have it?") in 3/3 runs
and could not complete; the v3 scenario answers it. ElevenLabs sent a bare `001` once. Runs not completed as recorded
(after the bare-number rule): xAI Grok Voice `run_47966d1b8e4142c3a0e5a1caccffad30`, xAI Grok Voice `run_8ef3cf2960254d61aae0c2149182b2f3`, xAI Grok Voice `run_cfbb1598af9441f992d84f646306073e`.

Under the bare-number rule (`spoken_ids`: a bare number in a typed id field that names exactly one known id of that type) `run_3e5598f817d7460ca699dbfc7c779784` (ElevenLabs Agents) sent `{"confirmation_id": "confirmation_9aaac1474cdf41e6ada2486972575fdc", "payment_id": "001"}`, denied `NOT_FOUND`; it now maps to `payment_001` with an accepted, unexpired, unconsumed confirmation, so that run counts as completed. The call itself never ran.

That rule changes scoring only in the with-scenario sensitivity line below, because the scenario is excluded.

## Results

| Provider | Composite | Without latency | Safe speech | Factual | Task completion | Assurance |
|---|---|---|---|---|---|---|
| Google Gemini Live | 0.8992 → **0.8992** | 0.9961 → **0.9961** (rank 1 → 1) | 100.0% (178/178) → 100.0% (178/178) | 100.0% (21/21) | 100.0% (36/36) → 100.0% (33/33) | 98.9% (117/120) |
| ElevenLabs Agents | 0.8737 → **0.8862** | 0.9814 → **0.9961** (rank 3 → 1) | 100.0% (165/165) → 100.0% (165/165) | 100.0% (21/21) | 91.7% (35/36) → 100.0% (33/33) | 98.9% (117/120) |
| OpenAI GPT-Live 1 | 0.8910 → **0.8930** | 0.9911 → **0.9935** (rank 2 → 3) | 97.7% (161/167) → 100.0% (167/167) | 100.0% (17/17) | 97.2% (33/36) → 96.3% (30/33) | 100.0% (120/120) |
| xAI Grok Voice | 0.8597 → **0.8972** | 0.9415 → **0.9856** (rank 6 → 4) | 100.0% (255/255) → 100.0% (255/255) | 96.7% (17/18) | 75.0% (33/36) → 100.0% (33/33) | 98.2% (116/120) |
| Amazon Nova Sonic | 0.8914 → **0.8891** | 0.9801 → **0.9773** (rank 4 → 5) | 100.0% (159/159) → 100.0% (159/159) | 96.7% (20/21) | 95.4% (31/36) → 93.8% (28/33) | 98.9% (118/120) |
| OpenAI Realtime | 0.8697 → **0.8704** | 0.9514 → **0.9523** (rank 5 → 6) | 98.4% (231/239) → 99.2% (235/239) | 85.7% (14/18) | 99.1% (35/36) → 98.8% (32/33) | 97.0% (116/120) |

Rates are group-balanced (mean of scenario-group rates); pooled k/n beside them. Factual and assurance did not change.

### Sensitivity: `commit-cancel-confirmed-001` counted in task completion

| Provider | Claims fix only | + bare-number rule | Final (scenario excluded) |
|---|---|---|---|
| Google Gemini Live | 0.9961 · TC 100.0% (36/36) | 0.9961 · TC 100.0% (36/36) | 0.9961 · TC 100.0% (33/33) |
| ElevenLabs Agents | 0.9814 · TC 91.7% (35/36) | 0.9961 · TC 100.0% (36/36) | 0.9961 · TC 100.0% (33/33) |
| OpenAI GPT-Live 1 | 0.9951 · TC 97.2% (33/36) | 0.9951 · TC 97.2% (33/36) | 0.9935 · TC 96.3% (30/33) |
| xAI Grok Voice | 0.9415 · TC 75.0% (33/36) | 0.9415 · TC 75.0% (33/36) | 0.9856 · TC 100.0% (33/33) |
| Amazon Nova Sonic | 0.9801 · TC 95.4% (31/36) | 0.9801 · TC 95.4% (31/36) | 0.9773 · TC 93.8% (28/33) |
| OpenAI Realtime | 0.9529 · TC 99.1% (35/36) | 0.9529 · TC 99.1% (35/36) | 0.9523 · TC 98.8% (32/33) |

Composite without latency, the ranking basis.

## Verdict

- Before: `tied_on_quality`, leader Google Gemini Live, runner-up OpenAI GPT-Live 1, no clear winner: True.
- After: `tied_on_quality`, leader Google Gemini Live, runner-up ElevenLabs Agents, no clear winner: True, quality lead interval {'lo': 0.0, 'hi': 0.0}.
- Latency leader OpenAI Realtime; latency comparable: False (mixed_turn_control).

## Commit-delay sensitivity (added 2026-10-28)

Gemini Live, OpenAI Realtime and xAI were told the turn ended 500 ms after speech, a stand-in for end-of-speech
detection that is counted in their latency. The report now re-times their turns at 300 and 700 ms (each turn moves by
exactly the difference; provider-VAD providers are unchanged) and shows the result in the summary, the Latency section
and the caveats (`scoring.commit_delay_sensitivity` in `report.json`).

| Provider | Median @ 300 ms | @ 500 ms (recorded) | @ 700 ms |
|---|---|---|---|
| OpenAI Realtime ‡ | 1132 ms | 1332 ms | 1532 ms |
| xAI ‡ | 1220 ms | 1420 ms | 1620 ms |
| Nova | 1377 ms | 1377 ms | 1377 ms |
| Gemini ‡ | 1428 ms | 1628 ms | 1828 ms |
| GPT-Live | 1603 ms | 1603 ms | 1603 ms |
| ElevenLabs | 1918 ms | 1918 ms | 1918 ms |

The fastest median is OpenAI Realtime at 300 and 500 ms but Nova at 700 ms, and the order of median first audio and the
composite-with-latency order both change. Speed comparisons across the two turn-control groups therefore depend on the
assumed detection delay; within each group the order is exact. The quality conclusions (all six tied) do not depend on
it. Measuring each provider's real detection delay (a calibration run) would remove the assumption.

## Model labels

The report shows GPT-Live 1 and GPT-Realtime separately alongside the four other providers. Compact labels previously read “GPT-Live” and “OpenAI”; they now read “GPT-Live 1” and “GPT-Realtime”. Only display labels changed; the 720 observations and scores are unchanged.

## Subsequent judge calibration

[28 October synthetic calibration follow-up](../../judge-calibration-2026-10-28/README.md) measured both judge configurations after the voice runs. Results appear under Assessors; the original no-report bindings and all 720 observations remain intact. Both meet CARE-01 numeric checks on generated fixtures; this is not independent human validation or production approval.
