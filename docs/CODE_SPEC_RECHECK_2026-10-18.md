# Fix recheck and performance baseline — 18 October 2026

Reviewed commit `7d173f5a35c70e7c756f01b5c2b64a4f9f600828`, including the preceding fatal-provider-error fix. This supersedes the current-state conclusions of `CODE_SPEC_REVIEW_2026-10-18.md`; retain that document as the original review. No implementation changes were made during this recheck.

The fixes improve several concrete behaviours, and the existing suite passes. Live assurance remains blocked by cancellation/deadline races, incomplete queue budgets and financial fact assessment. The new component baseline measures the deterministic harness; it cannot close these live-path requirements.

## Validation

- `make test`: 440 Python tests against PostgreSQL, successful with 5 skips; 28 browser tests passed. The new review-fix tests are included.
- `make lint`: ruff and bandit successful. Bandit emits existing comment/suppression warnings.
- A newer, concurrent edit to `scenario_catalogue.py` removed account IDs from two caller prompt templates after the frozen image was built. It was preserved and excluded from this baseline's source identity. A supplemental local run of `test_phase4_scenarios`, `test_phase4_commit` and `test_live_review_fixes` passed all 27 tests with that edit present. The 440-test PostgreSQL result and performance baseline apply to the frozen revision above.
- Temporary, credential-free probes reproduced the remaining defects below against the current source. Their results supplement the passing suite; they are not tests already covered by that suite.
- No paid provider calls, live ASR/judge tests or physical playback measurements were performed.

## Recheck of the original findings

| Original finding | Recheck | Remaining work |
|---|---|---|
| 1. Interruption propagation | Partial | Direct driver interruptions now advance the engine epoch and revoke tokens; client-held playback offset is correctly zero. Queued interruption during assessment and late old-response audio remain unsafe (R1/R2). |
| 2. Live bounds | Partial | The original 30-second driver-buffer case is rejected. Adapter limits count queue entries, not bytes/duration; non-audio and native-evidence queues remain unbounded (R4). |
| 3. Money claims | Partial | The tested dollar/euro and word-form negative examples abstain; allowed values reset per customer turn. Numeric negatives and swapped facts still pass (R5). |
| 4. Native/input evidence | Partial | Hash/size/omission events and a live binding event are added. Native payloads are still not retained, records flush only after a turn, and the actual configuration is not the run's authoritative manifest configuration (R6). |
| 5. Blocking ASR | Partial | Turn-end work now runs in a thread, so the event loop can schedule unrelated tasks. The consumer awaits that thread and cannot process queued interruption; timeout does not invalidate the worker (R1/R2). |
| 6. False enforcement claims | Fixed for the stated advisory scope | Findings now distinguish eligibility, keep `blocking_enforced=false`, and disclose that enforcement is not wired. Actual AI enforcement remains a future feature. |
| 7. Nova clock | Timestamp defect fixed; metrics partial | Nova now uses process-monotonic timestamps. The runner records stream times but does not pass speech boundaries into the driver, so input-relative metrics remain absent. Stream-send duration is not automatically a speech boundary. |
| 8. Live API/UI/CLI | Open | Run configuration and the supported API still select only the three mock scenarios; no integrated provider configuration/run factory or client playback delivery. |
| 9. Comparison provenance | Partial | Duplicate benchmark units and unknown run IDs are rejected. Values are still caller-authored, not derived from manifests; existence of a run does not validate its provider/scenario/outcomes. New provenance metadata is omitted from GET/export (R7). |
| 10. Fatal-provider error | TypeError fixed; lifecycle partial | Typed fatal/end outcomes and cost cleanup are present. Failure disposition is not durably recorded as its own run event; timeout still permits a late release (R1). |

The additional scenario-version conflict issue is improved: a concurrent winner is re-read and a different hash is rejected. Same-content concurrent insertion can still report `created:true` for both callers; that is lower impact than accepting conflicting content.

## Remaining findings, in priority order

### R1 — P1: Timed-out assessment worker can still release audio

**Code:** `src/voice_harness/live_scenario.py:177–210`; `src/voice_harness/live_assessment.py:303–308`. **Spec:** §§6, 13, 18; AC-03/14.

`asyncio.wait_for` cancels the coroutine awaiting `asyncio.to_thread`, not the running thread. The timeout handler only resets the driver's lists. The engine unit stays pending, so the worker can finish ASR, revise, approve and call `release_audio` after the runner has returned `turn_timeout`. The worker also shares mutable driver buffers, expected facts and metric fields with the next turn.

**Reproduced:** scripted ASR sleeps 200 ms; `_await_turn` has a 30 ms timeout. At timeout there are zero releases. After waiting for the worker, the ledger contains one `playback.released`.

**Fix:** atomically invalidate the pending assessment/generation in the engine before returning timeout, persist its disposition, and fence all result application by an immutable job/response ID and captured epochs. Have workers return assessment data; keep approval/release in the orchestrator after a transactional validity check. Do not share a reusable driver's mutable state with a still-running cancelled worker.

**Acceptance:** timeout followed by a late ASR success produces zero releases, no new valid token, and no mutation of a subsequent turn's buffers/facts/metrics.

### R2 — P1: Interruption is still delayed behind assessment; stale responses are not quarantined

**Code:** `src/voice_harness/live_scenario.py:166–198`; `src/voice_harness/live_assessment.py:233–258`. **Spec:** §§5–6, 13.

Moving assessment to a thread frees the event loop, but the sole adapter consumer awaits that thread. A provider reader can queue `Interrupted` while the consumer is awaiting ASR; the worker releases the response before the consumer handles the interruption. Separately, the driver's reset forgets the interrupted response ID and accepts subsequent old-response frames into a new unit using the current engine epoch.

**Reproduced:** enqueue a barge-in 30 ms into a 200 ms ASR operation: `_await_turn` returns `approved`, one release exists, zero interruptions have been applied, and the interruption remains queued. A second probe sends `AudioOut(old-response) -> Interrupted(old-response) -> AudioOut(old-response) -> TurnEnd(old-response)`; the late old-response audio is approved and released in engine epoch 2.

**Fix:** keep control/event intake active while assessment is pending, invalidate assessments immediately on barge-in, and tag/quarantine frames using provider response/item identity plus the captured generation epoch. Direct engine calls in an ASR test do not prove that the runner consumes a real interruption in time.

**Acceptance:** both sequences produce zero old-response releases and a durable interruption/history disposition.

### R3 — P1: Independent ASR revision resets the decision deadline

**Code:** `src/voice_harness/live_assessment.py:75–80`; `src/voice_harness/engine.py` (`revise_audio`, replacement `created_at`). **Spec:** §6, five-second semantic-decision deadline after unit completion.

The assessor waits for ASR, then creates a replacement approval unit through `revise_audio`. That replacement receives a fresh `created_at`, so `approve_audio` measures almost no wait even if ASR exceeded the original unit's deadline.

**Reproduced without a wall-clock sleep:** a controlled engine clock advances six seconds inside scripted transcription under the default five-second decision timeout. The assessment returns `approved` with a valid token.

**Fix:** preserve an absolute decision deadline derived from original response completion across all revisions/replacements; apply it transactionally before issuing or using approval. A new transcript revision must not extend the decision budget.

**Acceptance:** an ASR result arriving after the deadline expires/discards the unit and never issues a release token, including when the outer turn timeout is longer.

### R4 — P1: Queue bounds are incomplete

**Code:** `src/voice_harness/adapters/__init__.py` (`AUDIO_QUEUE_MAX`); both adapter `_emit` methods; `src/voice_harness/live_scenario.py:81–100`.

The 1,500-entry audio check does not cap PCM bytes/duration, and all transcript/tool/control entries bypass it. `_native` is another unbounded list, flushed only after a turn or final cleanup. A stalled tool or ASR consumer can therefore accumulate evidence and non-audio events indefinitely. A large audio frame also consumes much more than the nominal 20 ms frame budget while counting as one queue entry.

**Reproduced:** 2,000 transcript events enter the OpenAI queue; `qsize=2000`, `maxsize=0`.

**Fix:** explicit byte/duration/count budgets across all queues, with bounded reserved control capacity and bounded durable evidence ingestion. Count incoming allocation before buffering; overflow must cancel/quarantine the relevant response, not silently drop frames and later approve a discontinuous reconstruction.

### R5 — P1: Claim hardening still permits false financial approval

**Code:** `src/voice_harness/semantic_checks.py` (`SIGNED_AMOUNT`, `semantic_check`); `src/voice_harness/live_scenario.py:138–142`.

The new sign pattern catches words and `-£`, but not `-10 pounds` or `£-10`. Flat membership in `allowed_amounts_minor` still has no balance/available/transaction field, account, currency or timestamp binding. Resetting the list per customer turn limits staleness but does not prevent wrong attribution within that turn.

**Reproduced:** with allowed positive 1,000 minor units, `Your balance is -10 pounds.` returns `safe/CLAIMS_MATCH_EXPECTED`; `Your balance is £-10.` returns `safe/NO_ASSESSABLE_CLAIMS`. `Your balance is ten pounds.` is still safe against allowed values `[1000,123456]` regardless of which value is the actual balance.

**Fix:** typed factual bindings and complete sign/currency handling or abstention. Unrecognised financial assertions cannot become strict-safe merely because no claim was extracted. Add these exact cases and swapped-field/account cases to the independent-assessment tests.

### R6 — P2: Hash-only evidence and informational binding do not establish replay/provenance

**Code:** `src/voice_harness/live_scenario.py:86–119`; supported `RunConfig`/manifest construction.

The new records honestly declare omission, which improves reporting. They cannot reconstruct native fields or caller audio, do not connect each normalised event to its native event, and are not durable before a mid-turn crash. `run.live_binding` records adapter class and scenario hash, but does not validate the original run configuration or bind the exact provider/model/report into the authoritative run manifest. A library caller can still run a catalogue scenario inside a run configured as the identity-bypass mock.

**Fix:** bounded, durable ingress of sanitised native payloads and correlation IDs; opt-in retained input artifacts with explicit omissions otherwise; immutable exact provider/scenario configuration established before execution. The original native-preservation requirement remains unmet by a digest alone.

### R7 — P2: Comparison provenance is dropped from read/export and does not validate values

**Code:** `src/voice_harness/comparison_api.py:24–44,71–79,104–110`.

The create response now reports counts of resolved/unbound IDs. GET and export omit that `provenance`, and `report_hash` covers only the report, not that diagnostic disclosure. Even resolved IDs can reference runs with unrelated configurations while supplying invented measurements.

**Fix:** carry provenance and source-manifest hashes in every read/export and its integrity binding. Derive scored observations from sealed evidence. Keep manual/unbound data explicitly diagnostic throughout, including when a predeclaration is supplied.

## Performance baseline

Artifacts:

- `artifacts/phase1-recheck-baseline-2026-10-18.json`: full observations and distributions.
- `artifacts/phase1-recheck-baseline-2026-10-18.provenance.json`: exact source/build input hashes, frozen revision/image, validation and per-profile historical comparison.

The existing component benchmark ran without implementation changes: PostgreSQL, 20 ms pacing, 1,000 observations per profile, 1/5/10 sessions, 1×/4× payloads, instrumentation off/on, plus three TCP WebSocket replay profiles. The benchmark container retained its existing 8-vCPU quota and 1 GiB memory cap. Platform/runtime/config/cgroup limits match the prior artifact; process RSS differs and database contents/background activity are not controlled identically. The host has 12 logical CPUs and 24 GiB RAM; Docker reports about 11.7 GiB RAM. This is not the complete specification reference profile, so targets remain provisional.

**Results:** 12,000 component observations and 3,000 WebSocket observations; all profiles have 1,000 samples and report zero failures, timeouts or discontinuities where applicable. Component `total_path` p95 is **8.29–10.67 ms**, versus **5.84–11.25 ms** in `phase1-post-gap-baseline.json`. Instrumented durable-write p95 peaks at **10.26 ms** and deterministic-policy p95 at **0.039 ms**. All measured profiles are below the provisional 50/100/25 ms thresholds for their respective component measures; this does not establish full orchestration or conversational latency.

| Sessions | Payload | Instrumentation | Previous path p95 ms | New path p95 ms | Change |
|---:|---:|---|---:|---:|---:|
| 1 | 1× | off | 6.023 | 9.837 | +63.3% |
| 1 | 1× | on | 5.841 | 9.326 | +59.7% |
| 1 | 4× | off | 6.002 | 8.292 | +38.2% |
| 1 | 4× | on | 6.272 | 9.058 | +44.4% |
| 5 | 1× | off | 9.262 | 9.870 | +6.6% |
| 5 | 1× | on | 9.584 | 9.822 | +2.5% |
| 5 | 4× | off | 9.814 | 9.621 | −2.0% |
| 5 | 4× | on | 9.500 | 9.979 | +5.0% |
| 10 | 1× | off | 11.168 | 10.575 | −5.3% |
| 10 | 1× | on | 11.246 | 10.669 | −5.1% |
| 10 | 4× | off | 10.550 | 9.982 | −5.4% |
| 10 | 4× | on | 10.618 | 10.148 | −4.4% |

The one-session profiles are materially higher than the historical artifact, while ten-session profiles are slightly lower. This is one new run versus one historical run, not a randomised before/after experiment; do not attribute the difference to the fixes or claim statistical significance. Most measured path time is durable persistence. If the single-session increase matters, compare both revisions under alternating run order and matched database/background-load conditions before optimising.

TCP WebSocket client-send-to-server-accepted p95 is **7.76 / 7.31 / 3.18 ms** at 1/5/10 sessions. These are software round trips, not speech-end-to-playback. The 25 ms injected storage fault measured **29.13 ms**; the stalled-consumer fixture rejected overflow at **96,000 bytes**. Those faults exercise benchmark component fixtures, not the live queues in R4.

Reproduce:

```sh
docker compose run --build --rm -T benchmark benchmark \
  --output /data/phase1-recheck-baseline-2026-10-18.json --observations 1000
docker compose run --rm --no-deps -T --entrypoint cat benchmark \
  /data/phase1-recheck-baseline-2026-10-18.json \
  > artifacts/phase1-recheck-baseline-2026-10-18.json
```

Rebuild from the recorded revision/inputs to reproduce the measured source; the current working tree includes the later scenario-text edit described above.

The artifact's existing `source_hash()` does not recursively cover adapter/judge subpackages. The companion provenance file records a 231-file source/build input hash map including those modules, the Git revision, runtime image and benchmark artifact SHA-256. All 228 files present in the runtime image were checked against that snapshot; the other three are host build/configuration inputs. Config/environment hashes, observation counts and reported failures were also verified. The only detected post-snapshot source change was the preserved scenario-text edit.

## Next work

1. Resolve R1–R3 as one orchestration change: immutable assessment jobs, continuous control intake, absolute deadlines and transactional result fencing. Add runner-level regressions for all four reproduced cancellation/deadline sequences.
2. Close R4/R5: unified queue budgets and typed financial facts. Existing happy-path fix tests are insufficient for either requirement.
3. Complete evidence binding and comparison provenance, then the supported live API/UI run path. Keep online judging explicitly advisory until enforcement is acknowledged by the guardrail.
4. Repeat this recheck after fixes. Use the new baseline for component regression tracking; measure the actual live speech/ASR/playback path separately before making conversational-latency claims.
