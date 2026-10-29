# Phase 4 status (in progress)

## Current position (2026-10-28)

- Six live provider configurations run through the API, Runs page, manual sessions and bake-offs: `openai-live`
  (`gpt-live-1`, backend `gpt-6-luna`), `openai-realtime-live` (`gpt-realtime-2.1`), `nova-sonic-live`
  (`amazon.nova-2-5-sonic`), `xai-voice-live` (`grok-voice-3.0`), `gemini-live` (`gemini-3.8-live`) and
  `elevenlabs-live` (stored agent, LLM recorded per run). Coverage has six families. On 2026-10-29 the OpenAI Realtime,
  OpenAI Live (`openai_live`, new), xAI, Gemini and ElevenLabs reports were re-probed (reports dated 20261028/29) and AC-09
  re-run: all five are `implemented_and_verified` until their reports expire on 2026-11-04/05. Nova's report expired
  2026-10-24 and was not refreshed (the AWS session needs a fresh MFA: `./scripts/aws-session.sh <MFA>`, then `nova_probe.mjs`
  and `from-nova-observations`, then `make nova-live-container`), so `nova-sonic-live` is `diagnostic_unverified`.
  Interruption is excluded from AC-09 for Gemini, ElevenLabs and OpenAI Live (no client truncate). The OpenAI Live and
  ElevenLabs probes cite no documentation and carry no operator terms attestation. The OpenAI Live probe drives the
  harness adapter rather than a raw socket.
- The catalogue has 40 live scenarios in five groups: 9 happy path, 18 identity protection, 6 vulnerable customers,
  4 fault tolerance, 3 payment actions. 31 declare FACT-01.
- Latest comparison: the fair-harness bake-off of 2026-10-28 (`artifacts/fair-bakeoff-2026-10-28/`; 6 providers × 40
  scenarios × 3 repetitions = 720 cells, all completed after two credit-related pauses and resumes; claims-v2 re-score
  in `rescored-2026-10-28/`). Judged by `gpt-6-sol` and Claude Haiku 4.5, with independent OpenAI ASR. Bias remediation
  is in `PROVIDER_BIAS_REVIEW_2026-10-27.md`; latency is disclosed as not like for like. Judge calibration follow-up:
  `artifacts/judge-calibration-2026-10-28/` (synthetic holdout; numeric checks met for some criteria per judge, no
  policy approved).
- Earlier matrix benchmark (2026-10-19, four providers, `dbbe37a`, now superseded by the bake-off above):
  `BENCHMARK_5350E4A_2026-10-19.md` is its latest rerun (132 runs, one per scenario per provider, fault
  isolation verified). It makes no provider recommendation because nine assessments were untestable after
  independent-ASR credits ran out. The earlier `GEMINI_3_8_BENCHMARK_2026-10-19.md` batch is historical (undeclared
  timeout injections).
- Test counts below (642 Python, 71 browser at `dbbe37a`) are from 2026-10-19 and have not been refreshed.
- Bake-off reports show voice cost per run (see "Voice cost in the bake-off"); Nova runs need a re-run to be priced.
- Still open: variance beyond 3 repetitions, a fresh Nova report and AC-09, operator terms attestation for the newer providers,
  locale-aware caller voice, judge
  calibration policy approval (blocking stays off), live runs inside the container (no credentials or `say` there),
  and the per-section "Still open" items below.

The sections below are a chronological log; earlier counts and open items are superseded where later sections say so.

## Delivered

- **Scenario catalogue** (`scenario_catalogue.py`): initially 18 persona/locale/profile live-adapter scenarios built as ordinary
  `ScenarioDefinition` records (personas: impatient, bereaved/vulnerable, social engineer; locales
  en-GB/en-US; audio profiles clean, street noise 10 dB, telephone narrowband) plus 4 fault-injected
  identity variants; now 33 with happy-path and payment scenarios (see Current position). `FaultInjection` is a separate closed model kept outside the definition, so the
  three mock built-ins keep their content hashes. `GET /api/v1/scenarios` lists them with
  `execution: "live_adapter"`, `runnable: true` (live runs launch through the API since finding 8) and their faults.
- **Live scenario runner** (`live_scenario.py`): synthetic caller speech -> 20 ms chunks -> adapter ->
  `AdapterTurnDriver` (independent ASR, strict speech approval) with tool calls through the MCP gateway.
  Faults: `tool_error` (engine `timeout` fault for the session, `fault.injected` recorded; set before the
  gateway starts because the MCP process reads the profile once), `tool_latency`, `caller_interrupts`,
  `background_noise`/audio profile (deterministic noise at a declared SNR; narrowband by zero-order hold).
  Turn timeouts and adapter errors are reported, not hidden. `LIVE_LIMITS` raises the pending-audio cap
  for live engines (2 s mock default cannot hold a full reply; the limits are recorded in the manifest).

## Evidence

- Offline: `tests/test_phase4_scenarios.py` (catalogue validity/coverage/closed fault model, API listing,
  deterministic degradation, scripted-adapter runner through the real gateway, injected tool fault,
  turn timeout).
- Live, once, synthetic data: OpenAI Realtime on `identity-impatient-en-gb-clean-001` — one turn,
  assistant reply passed independent ASR and was approved; the model did not call a tool, so the
  tool/fault paths are not yet exercised against a live provider.

- Live, synthetic data (OpenAI Realtime), after giving the caller the challenge answer and the tool schema
  required fields: `identity-fault-tool-error-001` — the model called `verify_identity`, the gateway returned
  the injected `DEPENDENCY_TIMEOUT` (`fault_injected: true`) and the reply was approved. `identity-fault-
  caller-interrupts-001` (now two turns) — the runner cut in 500 ms into the first reply with a client
  `interrupt` (generation cancelled, history truncation acknowledged by the provider), then the second turn was
  approved; a non-fatal `TOOL_ARGUMENTS_INVALID` adapter error from the model's own tool call is reported.
  Provider-VAD barge-in (`interrupt_mode="vad"`, `barge_after_tool=True`): the next caller turn's speech
  overlapped the model's post-tool reply and the adapter emitted `interrupted` (1 observed, no client
  `interrupt` call). The following turn then reported `no_audio` (the cancelled reply's own turn_end was
  drained and no new reply arrived before the next turn_end), so recovery after a VAD barge-in is not yet
  shown. Overlap depended on timing after the tool result, not on a controlled playback position.
  Live chooser: one real OpenAI call picked an eligible branch with no simulator failure.

- Live (OpenAI Realtime, synthetic data), after fixing the runner to treat the response that follows a tool
  result as part of the same turn:
  - `identity-fault-tool-latency-001`: `get_account_summary` returned after 4.17 s (4 s injected); the spoken
    balance matched the tool value and was approved. Before the fix this reply was held `UNEXPECTED_AMOUNT`:
    amounts returned by trusted tools are now *allowed* (not required) in the semantic check
    (`allowed_amounts_minor`).
  - `commit-cancel-unconfirmed-001`: with no confirmation id the model refused to cancel and made no
    `cancel_payment` call.
  - `commit-cancel-confirmed-001`: the harness presented and confirmed the proposal on the trusted channel and
    gave the model the id as context; the model called `cancel_payment` with it, the commit succeeded once and
    the spoken confirmation was approved (AC-15 positive path on a live adapter).
  - Not shown live: a model fabricating a confirmation id (it asked for one instead); the offline gateway
    tests cover forged ids.

## Not yet established

- (Superseded: the 2026-10-28 fair bake-off ran 3 repetitions per cell; variance beyond that is still open. Nova runner use is done.)
- Live-model chooser for the AC-16 simulator (`model_simulator.py` is offline-tested with an injected chooser: model picks branch, oracle rejected, bad output attributed to the simulator with fallback, hashed prompt/config recorded; `OpenAIChooser` (opt-in `VOICE_HARNESS_LIVE_JUDGES=openai`) added). Synthetic commit (AC-15) is tested through the real MCP gateway with a scripted adapter (`tests/test_phase4_commit.py`: denial before identity/confirmation, single-use consumption, changed-amount invalidation, restart idempotency); a live model has not been driven to propose a commit.
- (Superseded: OpenAI vs Nova comparisons were run, then four-provider bake-offs; see Current position.)
- The default synthetic caller uses macOS `say` (the opt-in `openai_tts` voice avoids it); the catalogue declares locales but
  `say` voice selection is not yet locale-aware. Narrowband/noise are simple synthetic shaping, not measured telephony.
- (Superseded: the xAI and Google adapters were merged later; see their sections below.)

## Response to the 2026-10-18 code/spec review

Addressed (driver-to-engine tests in `tests/test_live_review_fixes.py`):

| Finding | Change | Still open |
|---|---|---|
| 1 interruption | Provider and client interruptions call `Engine.interrupt` (epoch, tokens, queued tools, history state). Held audio reports 0 ms played. A late ASR result after an interruption is `superseded`, never approved. | Truncation offsets from real playback acknowledgements; late-event quarantine by response id. |
| 2 buffers | Byte and duration limits apply from ingress; overflow discards, records `audio.backpressure` and asks the provider to cancel. Adapter audio is bounded (`AUDIO_QUEUE_MAX`) with a non-bounded control path and a single `AUDIO_QUEUE_OVERFLOW` signal. | Shared per-session budget across all buffers; queue bound counts events, not bytes. |
| 3 money claims | Foreign currency and signed/overdrawn language are `untestable`; a response with no assessable claims reports `NO_ASSESSABLE_CLAIMS`, not `CLAIMS_MATCH_EXPECTED`; trusted-tool amounts are allowed only for the turn they were returned in. | Typed facts bound to entity/field/currency/source; account-switch and stale-fact holdouts. |
| 4 evidence | `run.live_binding` (scenario id + hash, adapter, seed, limits), `input.caller_audio` (text/PCM hashes, boundaries) and `adapter.native_event` (every provider event: type, size, hash; payload not stored, with an explicit omission reason). | Payload retention policy; linking normalised events to native event ids; capability report in the binding. |
| 5 blocking ASR | Turn-end assessment runs in a worker thread, so the loop (and Nova's silence pump) stays responsive. | Interruption is only observed after the worker returns; a true cancellable async ASR client. |
| 6 `blocking_enforced` | Split into `blocking_eligible` (calibration permits) and `blocking_enforced` (always false until a guardrail acknowledges, with `enforcement_decision_id`). | Wiring a guardrail consumer. |
| 7 Nova clock | Nova `ts_ms` is process-monotonic like OpenAI. | Runner still supplies no speech boundaries for the latency metrics (`input.caller_audio` records stream start/end for a follow-up). |
| 9 comparison | Duplicate observation units are rejected; run IDs must exist; bound/unbound counts recorded as `provenance`. | Extracting observations server-side from sealed manifests; capability/clock/version checks. |
| 10 fatal errors | Typed `provider_error`/`stream_ended` outcomes, buffers reset on timeout, cost recorder closed in `finally`. | Persisting the failure disposition as its own event. |
| 11 scenario race | The inserted row is re-read; a different winning hash returns 409. | |

Not started: finding 8 (live scenarios through the API/UI/CLI run lifecycle) and the provider-config registry. Live runs remain a library path.
Missed by the review, noted by the review agent: `_cancelled_ends` is a counter heuristic for swallowing an interrupted reply's `turn_end`; the barge-in outcome is now `barge_in_not_assessed` so it is not read as an approval disposition.

## Response to the 2026-10-18 recheck (R1-R7)

| Item | Change | Still open |
|---|---|---|
| R1 late worker release | Timeout/fatal paths call `driver.abandon()`: advances a fence, marks the item dead, resets buffers and calls `Engine.interrupt` (units discarded, tokens revoked in one engine transaction). The worker snapshots and clears the driver's buffers before assessing, so it cannot clobber the next response, and a worker that finishes after the fence reports `superseded`. | A disposition event for the abandoned response; cancellable async ASR. |
| R2 interruption behind assessment | The runner pump keeps reading provider events while assessment runs on a worker. Interrupted items are tracked by provider item id (Nova's turn id spans the session), and their late audio is dropped. | `_cancelled_ends` is still a counter heuristic. |
| R3 deadline | `revise_audio` keeps the original `created_at`, so ASR time counts against the decision limit. | |
| R4 queues | Audio and transcript deltas are bounded in both adapters (control events never are); the runner flushes native-event records every 100. | Shared byte/duration budget across all buffers. |
| R5 claims | Signed-amount patterns cover "£-10" and "-10 pounds". | Typed facts bound to field/account/currency/turn. |
| R6 evidence | Native records flush mid-turn (bounded loss on a crash). | Payload retention (kept hash-only by decision); manifest binding. |
| R7 provenance | In GET and the export, covered by `provenance_hash`; tampering fails `verify_stored_export`. | |

Nova fixes: caller lines no longer include the account id (the tool only takes the security answer, and the id was being misheard); `TOOL_CALL_ENDS_RESPONSE` is per adapter (OpenAI true, Nova false) so Nova's single post-tool reply is not skipped. Catalogue hashes for the fault/commit variants changed with the line.

Single-sample comparison after these fixes: all seven catalogue scenarios approved on both OpenAI and Nova. In the tool-latency scenario neither model called `get_account_summary` this time, so the latency fault did not fire in that sample.

## Response to the 31ccddd recheck (F1-F5)

| Item | Change |
|---|---|
| F1 second end blocks interruption | Response ends are keyed work items: `AdapterTurnDriver.begin` freezes an immutable `AssessmentJob` (item ids, engine epoch, deep-frozen facts and their hash), `analyse` runs on a worker (bounded to 2 concurrent) and `commit` applies the result on the event-loop thread after a validity check. The runner pump never awaits an assessment inline, so interruptions and errors are handled while any number of assessments run; commits and controls are serialised in arrival order. A duplicate/no-audio end can no longer replace a real outcome. |
| F2 item identity forgotten | Item identities are tombstoned from the whole buffered set and every in-flight job on interrupt or abandon, independent of buffer resets; late audio of a retired item is dropped, a new item is accepted. Events without an item id are accepted (no key to retire). |
| F3 mutable facts | Facts are copied into the job at the response end and the runner rebuilds rather than mutates its context. Tool results become typed facts (account, field, signed minor amount, currency, source call and time); an amount must match a fact whose field family (balance / available / payment) is the one the response talks about (`FIELD_MISMATCH` otherwise). |
| F4 queues | One budget per adapter queue: event count and decoded audio bytes (20 s) for audio, transcript deltas and finals; errors, tool calls and turn boundaries are always admitted; one `AUDIO_QUEUE_OVERFLOW` signal makes the driver discard the response. Provider cancellation after an overflow now has a 5 s timeout. |
| F5 retention test | Follows the register cursor instead of assuming page one. |

Found while verifying live: headless runs never receive playback acknowledgements, so released audio stays pending in the engine's buffer accounting; with the 15 s limit a long session overflowed on its fourth response and the provider cancel hung until the turn timeout (about 1 in 5 OpenAI confirmed-cancel runs). Live limits are now a 120 s session budget with a separate 15 s per-response cap. Six consecutive OpenAI runs and the Nova run approved afterwards (single samples).

Still open: account/ownership binding beyond the fact's account id, a shared byte budget across adapter, driver and engine, unkeyed (no item id) late events, API/UI execution of live scenarios (finding 8).

## Closing the first three open items

- **Account binding:** the job's frozen context carries `verified_account_id` (null until identity is verified); a spoken amount must match a fact for that account (and GBP). Facts are built from the tool *result* (account id, currency), not its arguments. Debits are matched on absolute value because signed wording abstains.
- **Unkeyed events:** after an interruption or abandon that could not be keyed (no provider item id), unkeyed audio is refused until the next response boundary (turn start/end); keyed new items are never quarantined.
- **Abandon disposition:** `assessment.abandoned` records reason, retired jobs, buffered item ids, epoch and `released: false`.
- **Tool-latency scenario:** now two turns (verify, then ask for the balance). With the tool description telling the model which account id to use, both providers call `get_account_summary`, the 4 s fault fires, and the spoken balance is approved through the typed-fact path (single samples).

Deferred by decision: finding 8 (live scenarios through the API/UI/CLI) and a shared byte budget across adapter, driver and engine.

## Response to the 89fa78f recheck

| Finding | Change | Still open |
|---|---|---|
| 1 Amounts not bound to claim | `semantic_check` attributes each amount to the field family named in its own clause (words since the previous amount), so swapped balance/payment is `FIELD_MISMATCH`; with several fields in play and no single field in the clause it abstains (`AMBIGUOUS_CLAIM_ASSOCIATION`). An account named in the response (`acct_NNNN` / "account NNNN") other than the verified one is `ACCOUNT_MISMATCH`. | Clause attribution is lexical (field keywords, account-id mentions); amounts with no field keyword in a single-field response still bind to that field; no entity extraction for accounts named by other means (e.g. "your savings account"). |
| 2 Expired verification | The driver's `verified_account_id` and the engine content layer (`identity_verified`) require `identity_expires` to be in the future, as the tool authoriser does. Expiry during ASR was already blocked at approval (regression kept). | Identity version is not frozen into the job; revoke/lock during ASR relies on the approval-time engine check. |
| 3 Unkeyed interrupt before first frame | `_mark_dead` quarantines unkeyed audio whenever the retirement cannot be keyed (no item id given, buffered or in flight) or unkeyed frames were seen, including mixed keyed/unkeyed responses; cleared at the next response boundary. | Resume is at turn start/end, not a validated provider response id. |

## Finding 8: live scenarios through the API and Runs page

- `provider_config_id` now accepts `openai-realtime-live` and `nova-sonic-live` (registry in `live_providers.py`; `xai-voice-live` and `gemini-live` were added later). `POST /api/v1/runs` pairs live scenarios only with live providers (`SCENARIO_PROVIDER_MISMATCH`) and refuses an unconfigured provider at launch (`PROVIDER_NOT_CONFIGURED`). `GET /api/v1/live-providers` lists availability as reason codes only.
- Live runs use a second engine with `LIVE_LIMITS`, so the run manifest records the live budgets. Lifecycle: queued, probing, running, then `run.completed`, after which the deterministic rubric is assessed (`judge.adjudicated`; `/assurance` is ledger-derived) or `run.failed` (`PROVIDER_FAILURE`).
- The Runs page shows a provider selector for catalogue scenarios; unavailable providers are disabled with their reason.
- Verified: 469 Python tests, 29 browser unit tests, lint; a real run through the API completed on both OpenAI and Nova (single samples). **Not verified in a browser** (the Chrome extension was not connected); the page logic is covered by unit tests only.
- Still open: live runs are not covered by the Compose/PostgreSQL suite, the container has no credentials (live launch is refused there), cancel of an in-flight live run, judge/assurance for live runs, and the identity-version/quarantine items listed above.

## Judge and assurance for live runs

- Assessment resolves catalogue scenarios (`scenario_for`), so `POST /runs/{id}/assessments` and the online judge work on live runs. A completed live run is assessed automatically with deterministic checks only; criteria that need a judge (CARE-01) stay `untestable` until an operator runs an assessment with independent judges. The tested model is recorded from the provider config so judge independence is checked.
- Catalogue scenarios now declare CARE-01 for vulnerable personas and CONFIRM-01 for the cancel scenarios (their hashes changed). FACT-01 was declared in a follow-up (see below).
- The Runs page has an Assurance section (criterion, verdict, source).
- Verified live (single samples): OpenAI and Nova runs assessed `pass`; the bereaved Nova run was `untestable` for CARE-01 until an OpenAI judge (`VOICE_HARNESS_LIVE_JUDGES=openai`) was run through the API, then `pass` with source `judges`. Judge calibration is still absent, so judge verdicts are uncalibrated.
- Open: FACT-01 declaration and its deterministic check against live evidence, CONFIRM-01 `pass` on the unconfirmed scenario has not been checked for what it actually tests.

Browser check (Chrome, local dev server): the Runs page loads the catalogue and runs, the Assurance section renders for a judged live run (verdicts labelled deterministic/judges), and the provider selector appears only for live scenarios. This found a CSS bug unit tests could not: `form { display:flex }` overrode the `hidden` attribute (launch form visible before connecting, provider selector always visible); fixed with a `[hidden]` rule. Screenshots and page JavaScript were blocked by another extension in this session, so layout was checked through the accessibility tree and page text, not visually.

## Cancelling in-flight live runs

- `POST /runs/{id}/cancel` still settles the ledger through `Engine.cancel` (epoch bump, tokens revoked, held audio discarded) and now also cancels the runner task, which closes the provider session with reason `cancelled`. A cancel that lands before the task registers is honoured at registration. A cancelled run emits no `run.completed`, no assessment and no assurance.
- Verified: a hanging scripted session cancels and closes (test); real OpenAI and Nova runs cancelled mid-turn went to `cancelled` with no completion or assessment events. The Runs page shows a Cancel button for non-terminal runs (present in the browser on a running run; the click itself was not exercised there because the pane re-renders every 2 s and the automation's element references went stale).
- Open: a cancel while an ASR worker thread is mid-call lets that call finish (its result is discarded), and the 2 s re-render replaces the Cancel button while a user is aiming at it.

## CI fix after the live-run work

- CI failed on `921013e`, `146a597` and `4b3d160` (container tests only). Cause: caller speech is synthesised with macOS `say`/`afconvert`, absent in the Linux container, so a live run failed in the worker; my tests also cleared the whole environment, dropping the PostgreSQL credentials. Fixes: `availability()` now refuses with `NO_CALLER_VOICE_SYNTH` where the synthesiser is missing, tests substitute a tone and strip only the live-provider variables, and the worker logs its exception before failing the run.
- Lesson: these changes had been validated on SQLite only; the Compose/PostgreSQL suite (`make test`, 471 tests) now passes locally.
- Consequence: live runs through the API work on a macOS host with credentials, not inside the container.

## FACT-01 for live runs

- `eval_fact` now takes the independent ASR revision (`transcript.revised`, authoritative, source `independent_asr`) as the text of a spoken unit, falling back to the provider transcript. A wrong amount in the ASR text fails (`BALANCE_MISMATCH`) even if the provider transcript was right, and the reverse passes.
- The catalogue declares FACT-01 for every scenario whose caller asks about the balance or account (20 of 24 at the time; 25 of 33 now; hashes changed).
- Finding while checking against live runs: catalogue variants copied the base scenario's per-turn `prohibited_actions` (`get_account_summary`) into every turn, so the correct post-verification behaviour in the tool-latency and caller-interrupts scenarios was assessed `fail` (SCENARIO-PROPOSAL-01). Those two scenarios now allow the tool (`verified_flow`); calling it before verification remains IDENTITY-01's finding.
- Verified live (single samples): tool-latency on OpenAI and Nova spoke "£1,234.56", matched the tool result, FACT-01 `pass`, overall `pass`; caller-interrupts on OpenAI `pass`. Impatient and social refusals: FACT-01 `pass` with no amount claimed.
- Open: FACT-01 only checks amounts (not other account facts such as account id or payment dates), and a refusal scenario passes FACT-01 by making no claim, which says little; a spoken amount with number words is `untestable`.

## Judge calibration and automatic judging

**Automatic judging.** `HARNESS_AUTO_JUDGES` (comma list, e.g. `openai,bedrock`; live backends also need `VOICE_HARNESS_LIVE_JUDGES`) names judges the server runs on every completed live run, after the deterministic checks. Eligibility against the tested model is decided by the assessment (a judge from the tested provider/family is ineligible). A configured judge that cannot start is logged and reported by `GET /api/v1/live-providers` (`auto_judges`), and criteria that need it stay `untestable`; nothing is silently passed. The assurance payload now carries `judge_calibration` (per judge: no report, or the bound report and whether its policy is approved), and the Runs page shows it as advisory-only.

**Calibration run (single run, synthetic holdout `holdout-v1`, 48 examples per criterion, default exploratory policy).** Judge `openai / gpt-4.1-mini / offline-judge/1`; report `calrep_d2ffc86215fa881c46ae`, exported to `artifacts/phase4/judge-calibration-openai-gpt-4.1-mini.json`; 293 s for 240 judge calls. Provider usage/cost was not recorded for this run.

| Criterion | Precision (95% lower) | Recall (95% lower) | Failed policy checks |
|---|---|---|---|
| CARE-01 | 1.00 (0.86) | 1.00 (0.86) | reviewer approval only |
| IDENTITY-01 | 0.92 (0.76) | 1.00 (0.86) | precision lower bound |
| CONFIRM-01 | 0.75 (0.58) | 1.00 (0.86) | precision lower bound |
| FACT-01 | 1.00 (0.68) | 0.33 (0.18) | precision and recall lower bounds |
| AUDIO-01 | 0.90 (0.60) | 0.38 (0.21) | precision and recall lower bounds |

- The judge **passed 16 of 24 FACT-01 failures and 15 of 24 AUDIO-01 failures** (missed detections). It is not fit to decide those criteria, which is why they keep deterministic authority and why blocking stays off. All criteria are `advisory`; no policy has been approved (approval is a human reviewer action and was not taken).
- Only CARE-01 (judge authority) clears the numeric thresholds; the holdout is synthetic with scripted-silence audio, the thresholds are the exploratory defaults, and one run says nothing about run-to-run variance. This does not establish that the judge is accurate on real live conversations.
- Open: other judge models (Bedrock default still untested), repeated calibration runs for variance, a holdout built from real live-run transcripts, why the judge misses FACT-01/AUDIO-01 failures (prompt, oracle content, or the synthetic examples), and whether a judge/deterministic disagreement on those criteria is surfaced for review.

## Response to the 8160559 recheck (R1-R5)

| Item | Change | Still open |
|---|---|---|
| R1 live FACT-01 false passes | In a live run (`run.live_binding` present) FACT-01 uses only the independent ASR text, covers every generated unit whether or not the provider sent text, and is `untestable` (`INDEPENDENT_ASR_UNAVAILABLE`) if any unit has no independent transcript. Amounts use the guardrail's normaliser (`extract_claims`), so eleven to nineteen and other number words are interpreted; the controlled-fixture scope keeps its labelled behaviour but now abstains on every number word. The interruption scenario no longer declares FACT-01 (its first reply is cut off before any independent assessment, which would leave the criterion permanently untestable there). | Dates and non-amount account facts; whether withheld-but-unassessed units should block a FACT-01 pass is decided strictly (untestable). |
| R2 expiry during ASR | The job freezes the identity version; when a spoken amount was accepted on a verified identity the engine re-checks, in the approval transaction, that identity is still verified, unexpired, the same version and the same account (`IDENTITY_NOT_CURRENT`, unit blocked). Expiry test now begins at 1899 and commits at 1901 (inside the 5 s decision window) and asserts the identity reason; a re-verification test covers version changes. | Identity binding applies to approvals with an amount claim; other sensitive content still relies on the content guardrail. |
| R3 manifests assert mock | `run.live_binding` now records requested/returned model, provider session id and the capability-report binding (`capability_binding`: verified only with a current report, otherwise a labelled diagnostic); the probing event records the same; the manifest provider, limitations and pricing derive from the ledger (mock labels only for mock runs); live runs record provider-reported usage through `UsageRecorder`. Real OpenAI and Nova exports show the returned model, the bound capability report and a cost section, and verify offline. | Signed-export verification with a keyring for live bundles; endpoint/region/SDK version; provider tariffs remain null. |
| R4 timeout and lifecycle | `turn_timeout` is a failed run (`TURN_TIMEOUT`); provider errors are `PROVIDER_FAILURE`. The worker moves to `evaluating`, assesses, then writes the terminal state once (`run.completed` carries the assurance), so an assessment failure is `failed`, never `completed` then `failed`. | Typed dispositions for scenario-injected faults in reliability denominators. |
| R5 consent mechanics | The confirmed cancellation scenario is labelled `out_of_band_confirmation` (catalogue `mechanics`, recorded in the binding, the manifest provider and limitations) and no longer declares CONFIRM-01, so it contributes nothing to voice-confirmation assurance. | Authorising only after a trusted observation of the spoken confirmation, and denied/ambiguous/revised speech paths, before claiming voice AC-15 coverage. |

## Response to the 497faeb recheck

| # | Finding | Fix |
| --- | --- | --- |
| 1 | Empty or whitespace independent ASR became `pass / NO_AMOUNT_CLAIM` | Live FACT-01 treats a blank authoritative transcript as unresolved (`untestable / EMPTY_TRANSCRIPT`) and never infers "no claim" from it. API regressions for empty and whitespace ASR. |
| 2 | `caller-interrupts` excluded FACT-01, hiding a wrong balance in the second turn | FACT-01 is declared again for that scenario. Unassessed units (interrupted, missing or blank ASR) are reported as `untestable` only after the assessable units are checked, so a supported failure is never masked by an evidence gap. Unit regressions cover both orders. |

Not done: an API-level interruption probe with a wrong second-turn balance (covered at the evaluator level only).

## Post-497faeb work (six parallel branches, merged)

Detail is in the per-branch notes: `PHASE4_NOTES_interrupt_probe.md` (API-level interruption probe), `PHASE4_NOTES_latency.md` (matched latency on 37732bb vs 89fa78f; component only, noisy control), `PHASE4_NOTES_signed_export.md` (keyring tests for live bundles; endpoint host, region and SDK versions in exports), `PHASE4_NOTES_fact_faults_confirm.md` (FACT-01 for dates and ids, typed fault dispositions, confirmation authorised only after observed speech), `PHASE4_NOTES_caller_voice.md` (opt-in `openai_tts` caller voice), `PHASE4_NOTES_bedrock_calibration.md` (Claude Haiku 4.5 judge: only CARE-01 clears numeric thresholds; no policy approved, no blocking).

## Response to the 8e2b402 recheck

| # | Finding | Fix |
| --- | --- | --- |
| 1 (P1) | "I cannot confirm the cancellation" authorised a cancellation | Consent now needs an explicit affirmative that opens the reply (yes, go ahead, I confirm, ...). `confirm` elsewhere in a sentence is not consent. Negation (including `cannot`, any `n't`, typographic apostrophes), hedges, conditionals, questions and mixed speech are refused as deny or ambiguous. Regressions: classifier phrasings and API-level runs for `cannot` and `can't ... yet`, asserting no accepted confirmation and no id delivered. |
| 2 (P2) | A wrong transaction date passed by matching an unrelated `as_of` date | Returned dates keep their entity and field. A spoken date is checked only against the entity named in its sentence (or the field its cue word names). Several entities in one sentence, or a date that fits only some candidate entities with no owner named, is `untestable` (`DATE_ENTITY_UNRESOLVED`). Regressions: unrelated `as_of`, swapped transaction dates, unnamed owner. |

Not done: the API-level consent regressions mock the engine's present/confirm step like the existing ones, so the review's real-engine cancellation probe is not reproduced here; there is no API-level date test through the local MCP.

## xAI Grok Voice (merged fcca1de)

`xai-voice-live` adapter, capability probe (`cap_xai_grok_voice_3_0_20261018`, expires 2026-10-25), live AC-09 (5 of 5 after a truncate fix) and one live smoke run (`identity-impatient-en-gb-clean-001`, single sample, FACT-01 `NO_AMOUNT_CLAIM` only). Coverage: `xai_grok_voice` is `implemented_and_verified`. Terms attestation for xAI was recorded 2026-10-18 on the operator's statement. Direct MCP, reconnect/resume and audio alignment are Unknown. See `docs/PHASE4_NOTES_xai.md`.

## Google Gemini Live (merged eb6a32d)

Superseded by the Gemini 3.8 migration below: the model, capability report (now expiring 2026-10-26) and AC-09 result have changed; the 2.5 observations are kept as history.

`gemini-live` adapter for `gemini-live-2.5-flash-native-audio` on Vertex AI (`us-central1`, authorized-user ADC, no new dependency), capability probe (expires 2026-10-25), live AC-09 (audio in/out, tool correlation, error pass; interruption excluded because truncation is unsupported) and one live smoke run on `identity-impatient-en-gb-clean-001` (single sample, no tool calls, overall `pass`). Coverage: `google_gemini_live` is `implemented_and_verified` while its report is current. Compose suite: 592 tests OK, 5 skipped.

- Observed, not verified: every capability row is Observed from one probe run; no row cites documentation. Output sample rate (24 kHz) is inferred from byte count, not stated by the server.
- Barge-in is detection only: the server sends `interrupted`, there is no client cancel or truncate, and `interrupt()` raises `CapabilityUnsupported`.
- Unknown: reconnect with the resumption handle, audio alignment, direct MCP, `goAway`/`toolCallCancellation`, other regions, quotas, retention and pricing. The `global` endpoint connected then closed with 1008 (model not found) once.
- Live Gemini runs need host ADC; containers have none (`NO_GOOGLE_ADC`). Service-account ADC is unsupported without `google-auth`.
- Terms attestation for Google (and xAI) was recorded 2026-10-18 on the operator's statement; no prices recorded. The merge was scanned for ADC and provider key material with no matches. See `docs/PHASE4_NOTES_google.md`.

## Vendor prices (2026-10-18)

`artifacts/pricing/vendor-pricing-2026-10-18.json` holds list prices read from the vendors' pricing pages, loaded with
`HARNESS_PRICING_TABLE`. The shipped table is unchanged, so default exports still say `unknown`.

- Priced: OpenAI `gpt-realtime-2.1` (audio and text tokens), Amazon Nova 2.5 Sonic (us-east-1), OpenAI `gpt-4.1-mini`
  judge (listed as Deprecated by OpenAI), and `gpt-4o-mini-transcribe` ASR.
- xAI: the recorder now emits a `billable_audio_seconds` meter (seconds, as xAI reports it) priced at the published
  $0.08/min "STS" rate. The page does not name `grok-voice-3.0`, so that mapping is an assumption. The unpriced token
  meters share an exclusivity group, so they no longer make an xAI run partial. No rounding or minimum is applied
  (none documented); whether cut-off audio is billed is still not established.
- Google Gemini Live: usage is now split into audio and text tokens from `usageMetadata` modality details (any other
  modality, or counts that do not sum to the total, stay unclassified and unpriced) and priced at the "Gemini 2.5
  Flash Live API" rates ($3.00/$0.50 in, $12.00/$2.00 out audio/text). That row is matched to the model by name only.
  Gemini Live re-bills the session context each turn, so every turn's reported total is billable.
- Not priced: the Bedrock Haiku 4.5 judge (it uses the `us.` geo profile and only the global cross-region rate was
  readable). Quotas, rounding and minimum-billing rules are not established.
- Correction (2026-10-19): this said live runs record `openai_realtime`. They record provider `openai` and service
  `realtime`, so the 2026-10-18 OpenAI rows (`openai_realtime`) never matched a live run. See below.

## Voice cost in the bake-off (2026-10-19)

- `artifacts/pricing/vendor-pricing-2026-10-19.json` is the 2026-10-18 table with the same prices and corrected
  identifiers (the 2026-10-18 file is kept unchanged for earlier exports): OpenAI rows use `openai`/`realtime`; xAI's
modality-split token meters join the unpriced alternative to the per-minute line; Gemini 3.8 Live (GA 2026-09-24,
  `us-central1`) is priced as below (its Vertex model page,
  links to pricing but no rate could be read there; it is priced from the Gemini Developer API page
  (https://ai.google.dev/gemini-api/docs/pricing, supplied by the operator): audio $3.00 in / $12.00 out, text $0.75 in /
  $4.50 out per 1M tokens. The harness runs Gemini on Vertex AI, whose own page was not readable, so the Vertex rate is
  assumed equal; image/video input stays unpriced). `make serve-live` loads it by default.
- Nova runs now record their Bedrock region (`NovaSonicAdapter.region`), so new runs match the `us-east-1` rows.
  Earlier Nova runs recorded no region and stay unpriced (`NO_TARIFF_FOR_METER_DATE`); ledgers are append-only.
- The bake-off (`bakeoff.observe`) prices each run's voice usage; the report has a "Voice cost per run" table
  (priced / partly priced / not priced, runs priced, median and mean over fully priced runs, known spend, reasons).
  ASR and judge cost are excluded as shared harness overhead; failed runs are costed; cost never enters the composite.
  `scripts/benchmark_report.py --pricing-table` re-renders an existing matrix with cost.
- Re-rendered 5350e4a benchmark (`reports/voice-provider-benchmark-5350e4a-2026-10-19-cost.html`, list prices, one
  sample per cell), median voice cost per run: OpenAI $0.0140 (33/33 priced), xAI $0.0107 (32/33), Gemini $0.0022
  (31/33), Nova $0.0029 (33/33). One xAI and two Gemini runs reported no usage and stay unpriced.
- Nova's cost comes from a second batch (`artifacts/provider-matrix/matrix-nova-cost-2026-10-19.json`, 33 scenarios, run
  with fresh AWS session credentials after the region fix; the original Nova runs recorded no region). The report keeps
  the original runs' outcomes and latency and takes cost from the later run of each scenario
  (`benchmark_report.py --cost-from`); it says so in its limits. Most of the later Nova runs ended `untestable` because
  the independent-ASR credits were still exhausted, which does not affect voice usage.
- Why the costs differ: token counts per run are similar across providers; the rates are not. OpenAI charges $32/$64
  per 1M audio in/out tokens against $3/$12 for Gemini and Nova, and xAI bills $0.08 per audio minute (about 18.5
  billable seconds per run). Gemini's rate is the Developer API rate assumed for Vertex. Providers count audio
  tokens differently, so compare dollars, not token counts.

## Gemini 3.8 migration (2026-10-19)

The active `gemini-live` configuration now requests `gemini-3.8-live` on Vertex AI
`us-central1`. Fresh speech, tool and interruption probes and AC-09 results replace the
current 2.5 capability binding; the historical baseline is preserved under
`artifacts/gemini38/baseline/`. AC-09 passed its supported dimensions; client truncation
remains excluded and the server does not echo a model version. The full four-provider
scenario rerun, report and validation results are recorded in
[GEMINI_3_8_BENCHMARK_2026-10-19.md](GEMINI_3_8_BENCHMARK_2026-10-19.md). That batch had eight undeclared
timeout injections; the fault-isolation fix (`5350e4a`) and its rerun are in
[BENCHMARK_5350E4A_2026-10-19.md](BENCHMARK_5350E4A_2026-10-19.md), which is limited by exhausted
independent-ASR credits.
