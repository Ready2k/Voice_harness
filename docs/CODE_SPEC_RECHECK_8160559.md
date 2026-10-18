# Review of 8160559 — 18 October 2026

Reviewed `8160559f7fb7b8c7056932c49081f136279fdf4d` and the intervening changes since `89fa78f`. Scope includes the earlier approval fixes, live API launch/cancellation, automatic judging, FACT-01 and evidence export. No implementation changes were made; prior artifacts and user files were preserved.

The earlier exact claim-attribution and pre-first-frame quarantine cases now have fixes and regression coverage. Live scenarios are available through the API/Runs page, so the previously deferred finding 8 is no longer simply deferred: its new execution, judging and evidence paths need review. The expanded suite passes, but remaining cases below prevent a clean assurance sign-off.

## Validation

- `make test` against PostgreSQL: **477 Python tests, successful, 5 skipped; 31 browser tests passed**.
- `make lint`: successful (ruff and bandit, with existing suppression/comment warnings).
- Latest-commit CI **passed**: [Phase 1 deterministic checks, run 39147039612](https://github.com/Ready2k/Voice_harness/actions/runs/39147039612). The preceding commit `90e9e27` also had successful CI when inspected.
- Additional credential-free probes exercised real synthetic MCP results, scripted ASR and injected API worker summaries. Their results are described explicitly below. No paid provider/judge calls, calibration rerun, physical playback check or performance rebaseline was performed.

## Findings

### 1. P1 — FACT-01 can pass a live run with unavailable independent audio assessment

**Code:** `src/voice_harness/judges/rubric.py:281–304`, particularly provider-transcript fallback at line 294. **Spec:** §§6–7; unassessable speech maps to mandatory criterion `untestable`, and a provider transcript is supporting evidence rather than proof of spoken content.

The new evaluator prefers an independent-ASR revision when present, but falls back to the provider transcript when there is none. It does not check `assessment.asr_unavailable` or distinguish live and controlled fixture evidence. Consequently withholding unassessable audio can pass platform enforcement while FACT-01 incorrectly passes speech correctness, yielding an overall pass.

**Reproduced:** create a live-configured tool-latency scenario, verify and fetch the account summary through the real local stdio MCP gateway, hold PCM with provider text `Your balance is £1,234.56.`, assess with no ASR backend, then complete and evaluate the run. The gate reports `unavailable`; FACT-01, AUDIO-01 and overall assurance all report `pass`. Audio was correctly withheld; its factual correctness was not independently established.

There is a second false-pass path in `spoken_amounts`: `Your balance is eighteen pounds.` and `Your balance is eleven pounds.` each return no amounts and `ambiguous=False`. FACT-01 then returns `pass / NO_AMOUNT_CLAIM`, including with an authoritative independent-ASR revision containing that text. The word heuristic omits these number words. Conversely, units with an empty provider transcript are filtered out before a valid independent-ASR revision can be used.

**Fix:** build live factual assessment from actual-audio assessment records and all generated units, independent of whether provider text is present. Missing/unavailable ASR must leave factual correctness unresolved; a blocked unit may still pass enforcement separately. Reuse a single versioned amount normaliser or abstain on financial claims it cannot interpret. Preserve explicitly labelled controlled-fixture behaviour only in that scope.

**Regression checks:** missing/failed ASR with a correct provider transcript; missing provider transcript with valid ASR; every supported number-word family, including eleven–nineteen; contradictory amount supplied only by ASR. Assert overall `untestable` or `fail` as appropriate, never a missing-evidence pass.

### 2. P1 — Verification expiring during a short ASR wait still permits release

**Code:** `src/voice_harness/live_assessment.py:406–446`; `src/voice_harness/engine.py` (`approve_audio` content check). **Spec:** §§6–8, sensitive output and identity-state policy binding.

Job creation now checks identity expiry correctly, but the frozen verified-account context is not revalidated at commit/release. The content guardrail receives the current expired state, yet its balance-disclosure pattern only catches digits; an ASR transcript with spoken number words can still pass.

**Reproduced:** verify at controlled time 1000 (expiry 1900), fetch the actual authorised summary and begin the unit at 1899, advance the clock by only two seconds inside ASR, then commit `Your balance is one thousand two hundred and thirty four pounds fifty six pence.`. The result is `approved` at 1901, after verification expired but within the five-second decision deadline.

The new `test_expires_while_the_job_is_in_flight` does not isolate this condition: its job starts at time 1000 and commits at 1901, exceeding the five-second deadline by about 896 seconds. It passes because of decision timeout, even without identity revalidation.

**Fix:** bind identity version/expiry to the job and revalidate the current authorisation policy transactionally before sensitive approval/release. Do not rely on transcript regexes for an authorisation invariant. Change the regression to start at 1899 and commit at 1901, and assert the identity-specific reason rather than merely any non-approval.

### 3. P1 — Live evidence manifests still assert a mock provider and scripted audio

**Code:** `src/voice_harness/evidence.py:469–478,495–517`; `src/voice_harness/api.py:97–109`; `src/voice_harness/live_providers.py:117–119`. **Spec:** §§2–5, 10–12; reproducible provider/version/capability provenance.

The new API can run OpenAI and Nova, but bundle construction unconditionally writes `adapter=mock`, `model_id=scripted-fixture-v1`, `capability_report_id=mock-fixture-v1` and `capability_status=mock_only`. Limitations still assert silence/scripted transcripts and no live ASR/judge/provider behaviour; pricing says `NO_PROVIDER_BILLING_IN_MOCK`. The launch worker also emits a mock-only probing event for every provider. The session-open result, which could contain returned model/session metadata, is discarded.

**Reproduced:** export a run configured as `openai-realtime-live`; its manifest contains that config ID alongside all the mock values above. A hash/signature can bind these incorrect descriptions without making them accurate.

**Fix:** persist immutable requested/returned provider-session metadata and the exact capability-report/config binding at session setup. Derive manifest fields from that run record. Retain unknown/unresolved values and missing-usage reasons when evidence is absent. Apply current profile/probe eligibility, with explicitly labelled diagnostic runs if verification is unavailable; environment credentials alone are not capability verification. Preserve mock-specific metadata only for mock runs.

**Regression checks:** scripted adapter fixtures exercising each live config through launch, session-open receipt, signed export and offline verification. No live-configured export may identify itself as a mock fixture or imply that provider charges are absent.

### 4. P2 — A timed-out provider turn is classified as a completed run

**Code:** `src/voice_harness/api.py:159–169`. **Spec:** §§11, 13, 15; timeout/failure dispositions and reliability denominators.

The live worker only treats `provider_error` and `stream_ended` turn outcomes as failures. `turn_timeout` therefore falls through to `run.completed` if there is no separate adapter error. The run has no explicit timeout terminal disposition for downstream reliability reporting.

**Reproduced through the API:** inject a live-run summary containing one `turn_timeout` and no adapter errors. The stored run ends `completed` (assurance is `untestable`). This is not a false assurance pass in that probe, but it is a misleading operational/reliability outcome.

**Fix:** define typed run dispositions for timeout, explicit scenario-injected faults and successful completion, and preserve timed-out runs in failed/timeout reliability denominators. Use `evaluating` while automatic judging runs, then write the terminal state once: current code emits `run.completed` before judging, so an assessment exception can later produce `completed -> failed`.

### 5. P2 — The live confirmed-cancellation scenario still authorises before spoken consent

**Code:** `src/voice_harness/live_providers.py:122–137`; `src/voice_harness/live_scenario.py` (`before_turn` runs before caller audio streaming). **Spec:** §8, scored voice confirmation must assess actual spoken confirmation; simulator-only out-of-band confirmation is a separately labelled harness-mechanics test.

Before the second caller turn is streamed, the trusted hook calls `engine.confirm(..., "affirm")` and tells the provider that the customer has confirmed. It does not observe or assess the actual spoken confirmation. This can test synthetic tool/confirmation mechanics, but a CONFIRM-01 pass here does not establish live voice-consent correctness.

**Fix:** explicitly label this scenario/result/export as out-of-band harness mechanics and exclude it from voice-confirmation assurance, or authorise only after a trusted observation derived from the actual presented proposal and spoken confirmation. Include denied/ambiguous/revised speech paths before claiming voice AC-15 coverage.

## Improvements confirmed and calibration limits

- The exact swapped-clause and different-account examples from the prior review have new per-clause checks and tests. Unsupported associations are held. This remains a rule-based subset of claim understanding.
- Verification expired before job creation is now refused. The narrower in-flight expiry window in finding 2 is still open.
- Unkeyed interruption before any frame and mixed keyed/unkeyed response cases now quarantine late frames; keyed new items remain separately identifiable.
- The live API/Runs page, provider availability checks and task cancellation are implemented, with scripted integration tests. The Linux image refuses launch without the caller speech synthesiser; it does not execute the macOS caller path.
- Automatic judging records calibration bindings and keeps AI blocking advisory. The committed calibration report transparently shows poor FACT-01/AUDIO-01 detection and no reviewer approval. Those results should not be used to enable blocking. CARE-01's synthetic-holdout result does not establish live conversational accuracy.
- Single-sample provider observations and the confirmed-cancellation mechanics remain distinct from repeated, independently assessed voice studies. The previous matched benchmark is tied to older revisions and was not rerun in this review.

## Recommended order

1. Make live FACT-01 fail closed on missing actual-audio assessment and fix the numeric-word false passes; re-run the assurance truth-table tests with live-style evidence.
2. Revalidate identity at commit/release and replace the timeout-confounded expiry test with a short expiry window.
3. Bind actual provider/session/capability/cost metadata into live exports before distributing them as evidence.
4. Correct timeout/evaluating lifecycle reporting and label out-of-band consent mechanics explicitly.

These are assurance/evidence fixes, not a recommendation to expand provider or judge coverage first.
