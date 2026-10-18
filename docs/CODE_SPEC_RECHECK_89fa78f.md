# Review of 89fa78f, CI, PostgreSQL and matched latency — 18 October 2026

Reviewed `89fa78f9fdf6e68673c409b1080a9a5fbb06403f` together with its prerequisite `b0dae06`. No implementation changes were made. Finding 8 (live API/UI/CLI execution) remains deferred as requested; the previously documented shared cross-component byte budget is also explicitly deferred in the project notes. Neither is presented here as a new regression.

The immutable-job design materially improves the assessment/control path: workers return analysis, the orchestration path commits it, in-flight identities survive buffer reset, and fact contexts are frozen. The previous timeout, second-end/interruption, mutable-facts and pagination fixes now have direct regression coverage. The new verified-account filter, unkeyed quarantine and abandonment event work for the cases tested, but the three additional cases below still permit incorrect strict approval.

## Validation and evidence

- CI for this exact SHA **passed**: [Phase 1 deterministic checks, run 39138072745](https://github.com/Ready2k/Voice_harness/actions/runs/39138072745), completed 18 October 2026 at 15:59 BST. Its `make lint`, `make audit` and `make test` steps succeeded.
- Local `make test` against the existing PostgreSQL volume: **455 Python tests, successful, 5 skipped; 28 browser tests passed**. The previous pagination-test failure did not recur after its cursor fix.
- Local `make lint`: successful (ruff and bandit; existing comment/suppression warnings remain).
- Temporary credential-free probes reproduced the remaining cases below. These are additional adversarial cases outside the passing suite.
- The two-turn tool-latency scenario and fact construction from tool results are present in the reviewed source. The reported OpenAI/Nova runs are single-sample observations supplied by the implementer; this review did not repeat paid live calls or independently obtain their complete ledgers. They show that a tool/fault path was exercised, not a provider failure rate or matched speech-latency result.

## Findings

### 1. P1 — Amounts are not bound to the individual spoken account/field claim

**Code:** `src/voice_harness/semantic_checks.py:244–266` (`facts`, `mentioned`, and `matching`). **Spec:** §§6–7, FACT-01.

Filtering fact rows by `verified_account_id` is useful authorisation filtering, but the checker still extracts a bag of amounts and a set of field keywords for the whole response. It does not connect each amount to its clause, field or explicitly named account. Therefore it can accept two individually false statements if both numbers occur somewhere in the authorised fact set. It also accepts the verified account's correct value when the utterance attributes it to another account.

**Reproduced with verified account `acct_1001`, balance 123,456 minor units and payment 1,000 minor units:**

- `Your balance is ten pounds and the payment is one thousand two hundred and thirty four pounds fifty six pence.` -> `safe / CLAIMS_MATCH_EXPECTED`, although the two amounts are swapped.
- `The balance on acct_2002 is one thousand two hundred and thirty four pounds fifty six pence.` -> `safe / CLAIMS_MATCH_EXPECTED`, although the fact belongs to `acct_1001`.

**Fix:** extract individual claims with their entity/account, field, amount and currency, and match each claim to one eligible source fact. If association cannot be resolved reliably, abstain and hold the whole response. A response-wide keyword set cannot provide this binding. Keep the verified-account filter as an additional requirement, not a substitute for claim attribution.

**Regression checks:** swapped balance/payment and balance/available clauses; correct amount attributed to a different named account; multiple claims with one unsupported association. All must fail or abstain, never approve.

### 2. P1 — An expired verification still enables private balance speech

**Code:** `src/voice_harness/live_assessment.py` (`begin`, construction of `verified_account_id`); `src/voice_harness/engine.py:1114` (content-layer identity flag). Compare the tool authoriser's `identity_expires` check at `engine.py:438–442`.

The audio job treats `state["identity"] == "verified"` as sufficient. Expiration does not automatically change that string, and neither this context construction nor the content-layer flag checks `identity_expires`. Consequently the new fact filter can consider an expired session verified even though the bank authoriser would deny a new protected call.

**Reproduced through the driver using a real synthetic tool result:** verify at controlled time 1000 (expiry 1900); successfully fetch `get_account_summary` at 1899.5; begin the speech unit at 1901 with that result as its fact source. `Your balance is ...` receives `approved` and a gate release despite verification already being expired when the job starts.

**Fix:** derive verified-account eligibility from identity status **and** unexpired verification. Freeze identity version/expiry in the job and revalidate the relevant policy before issuing/releasing sensitive approval. Define explicitly how an identity change or expiration during ASR is handled. A current-identity helper shared with tool authorisation would avoid divergent checks.

**Regression checks:** expired before job creation; expires during ASR; verification revoked/locked while the job runs. Assert the declared policy and zero unauthorised balance release.

### 3. P1 — An unkeyed interruption before the first audio frame does not quarantine anything

**Code:** `src/voice_harness/live_assessment.py:322–330` (`_mark_dead`). **Spec:** §§5–6, interruption/stale-generation rejection; AC-03/14.

Quarantine is enabled only when there are already buffered unkeyed bytes or an unkeyed assessment job. If the interruption/abandon arrives before the first frame, there are no items/bytes/jobs to inspect and the flag remains false. Unkeyed late frames from the cancelled generation can then create a new unit in the incremented engine epoch. The current test starts with an unkeyed audio frame already buffered, so it misses this ordering.

**Reproduced:** fresh driver -> `Interrupted()` with no item ID -> late unkeyed `AudioOut(old-response)` -> `TurnEnd(old-response)`. Quarantine is false, the frame buffers 960 bytes, scripted independent assessment returns `approved`, and one `playback.released` is written.

**Fix:** when an interruption cannot be associated with a reliable generation/item boundary, quarantine unkeyed audio regardless of whether earlier bytes have arrived. Track receipt of unkeyed frames independently of whether a response also has keyed frames. Resume only at a validated new-response boundary; keep independently identified new items eligible as intended.

**Regression checks:** interruption/abandon before first frame; mixed keyed/unkeyed response; keyed assessment followed by late unkeyed frames; stale/duplicate end events; verified new response boundary. None of the retired generation's unkeyed frames may be approved.

## Confirmed improvements and limits

- `AssessmentJob` freezes the context and retains item identities across assessment. The second-end and timeout regressions from the preceding review are now explicitly exercised by runner tests.
- `assessment.abandoned` is a closed, generated event recording reason, retired jobs/items, epoch and `released:false`; timeout/fatal paths supply typed reasons. It is not a claim that nothing earlier in the session was released.
- Facts are built from structured tool results rather than model arguments. Negative transaction amounts are matched by magnitude, while signed speech still abstains. This is an accounting convention for debit narration, not a complete interpretation of credit/debit wording.
- The tool-latency scenario now verifies on one turn and asks for the balance on the second, so a model has a reason to call the delayed tool. Acceptance should still assert that the configured fault actually fired; an approved conversation alone is insufficient.
- Native records remain hash-only; exact manifest/provider binding and caller-authored comparison provenance remain known limitations. Deferred finding 8 is not reopened by this review.

## Matched component latency benchmark

This study compares the previous reviewed commit `31ccddd` with `89fa78f`, using immutable Git-archive build contexts, pinned runtime images, the same benchmark code/dependencies, the same Docker/PostgreSQL host, and a fresh database per arm. Runs were sequential to avoid mutual load. Seed 20261018 selected order **after, then before**. Each arm used 12 profiles × 1,000 observations (1/5/10 sessions, 1×/4× payloads, instrumentation off/on), plus three 1,000-observation TCP WebSocket profiles, 20 ms pacing, 8-vCPU quota and 1 GiB memory cap. Both temporary databases were removed after artifact capture.

Artifacts:

- `artifacts/matched-latency-31ccddd-2026-10-18.json`: rerun control, full observations and distributions.
- `artifacts/matched-latency-89fa78f-2026-10-18.json`: current revision, full observations and distributions.
- `artifacts/matched-latency-31ccddd-vs-89fa78f-2026-10-18.json`: profile-matched differences, source/image/file hashes, exact commands, CI and local validation. Runtime files in both images were checked against their source snapshots.

All component profiles recorded 1,000 samples and zero reported failures/timeouts/discontinuities. Both WebSocket sets also recorded 1,000 samples/profile and zero reported failures/discontinuities. Component `total_path` p95 spans **9.29–13.17 ms before** and **9.50–13.70 ms after**. Maximum instrumented durable-write p95 is **12.77 / 12.63 ms** before/after; deterministic-policy p95 is **0.0301 / 0.0303 ms**. These component proxies remain below the provisional 50/100/25 ms thresholds; they do not measure the complete conversational path.

| Sessions | Payload | Instrumentation | Before p95 ms | After p95 ms | Change |
|---:|---:|---|---:|---:|---:|
| 1 | 1× | off | 11.868 | 12.495 | +5.3% |
| 1 | 1× | on | 12.543 | 13.699 | +9.2% |
| 1 | 4× | off | 11.316 | 11.216 | −0.9% |
| 1 | 4× | on | 10.595 | 12.080 | +14.0% |
| 5 | 1× | off | 10.974 | 12.218 | +11.3% |
| 5 | 1× | on | 13.166 | 12.814 | −2.7% |
| 5 | 4× | off | 12.594 | 13.182 | +4.7% |
| 5 | 4× | on | 12.281 | 12.686 | +3.3% |
| 10 | 1× | off | 9.506 | 10.241 | +7.7% |
| 10 | 1× | on | 9.345 | 9.501 | +1.7% |
| 10 | 4× | off | 9.289 | 9.855 | +6.1% |
| 10 | 4× | on | 9.595 | 10.011 | +4.3% |

Ten of twelve component point estimates are higher on the current revision; the largest paired p95 increase is about **1.49 ms**. This is descriptive, not a statistically established regression or an isolated effect of the live-assessment changes. The measured benchmark/transport code and dependencies are identical across arms; runtime source is different and host/background/shared PostgreSQL cache conditions are not reset identically.

In the main pair, TCP WebSocket client-send-to-server-accepted p95 before/after was **12.36/10.94 ms** (1 session), **3.94/6.14 ms** (5), and **4.94/14.21 ms** (10). The ten-session increase is **9.27 ms**. The injected 25 ms storage delay measured **30.31/29.55 ms**, and both stalled-consumer fixtures rejected overflow at **96,000 bytes**. These faults exercise component fixtures, not the deferred shared live queue budget.

A second, WebSocket-only pair used reversed order (**before, then after**), the same images, fresh databases and 1,000 samples/profile. It omitted the preceding component workload in both arms. Results are in `artifacts/matched-websocket-check-31ccddd-vs-89fa78f-2026-10-18.json`, hash-linked from the matched-study summary:

| Sessions | Before p95 ms | After p95 ms | Difference ms |
|---:|---:|---:|---:|
| 1 | 11.551 | 11.571 | +0.020 |
| 5 | 9.076 | 4.930 | −4.146 |
| 10 | 5.205 | 13.028 | +7.823 |

Every check profile recorded 1,000 samples and zero reported failures/discontinuities. The ten-session increase appears in **both pairs**, so it should remain a performance follow-up rather than be dismissed as an isolated historical-baseline difference. One- and five-session results vary across the pairs. These runs do not isolate a causal code change: the measured WebSocket function and dependencies are identical, and host activity/PostgreSQL cache/checkpoint behaviour were not instrumented or experimentally controlled. Profile durable writes, locking/projections and event-loop scheduling under sustained ten-session replay before accepting a no-regression claim. No WebSocket-specific SLO is declared by these measurements.

This is a matched **component** benchmark, not OpenAI-versus-Nova latency or a live ASR/guardrail study. Matching configuration reduces confounding; one pair of runs cannot establish statistical significance or isolate a causal code effect. Full orchestration/reference-machine SLOs remain provisional. The earlier historical baseline is not substituted for this newly rerun control arm.

## Recommendation

Keep the concurrency/job refactor. Finish per-claim attribution, unify the verification-state check, and close the pre-first-frame quarantine case before declaring strict financial playback assurance. Preserve finding 8's deferral and the single-sample limitation of the live provider observations. Add these exact cases to the runner/driver assessment tests rather than treating the passing suite or component latency as their acceptance oracle.
