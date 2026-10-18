# Code review against specification v0.3 — 18 October 2026

Reviewed the current working tree against `voice_evals_spec_v0.3.md`, at HEAD `eedb4ca5457a5a332b3d4f8d22944f2c053d5fec` plus the existing Nova adapter edit. This is a code and executable-check review, not full acceptance certification. No implementation changes were made. Existing work in the Nova adapter and `scripts/render_comparison.py` was preserved.

The deterministic core has substantial coverage. Live adapter integration still has material gaps in safety state propagation, evidence capture, bounded buffering and assessment. Passing the existing tests does not establish that the integrated live path meets the specification. Prioritise closing that path before expanding providers or publishing comparative assurance claims.

## Validation

- Local: `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`: 429 tests, successful, 6 skipped.
- Browser: `node --test tests/browser/*.test.mjs`: 28 passed.
- Container/PostgreSQL: `make test`: 429 Python tests, successful, 5 skipped; 28 browser tests passed.
- `make lint`: successful (ruff and bandit).
- Additional temporary probes reproduced the buffer, interruption, semantic-assessment, comparison-input and fatal-provider-error issues below. These probes did not change implementation or test files.
- No paid provider calls, live ASR/judge runs, physical playback measurements or performance rebaseline were performed.

## Findings and recommended fixes

P1 means resolve before treating live runs as assurance evidence. P2 means a material conformance/integration gap to close before the relevant release gate. References are repository-relative.

### 1. P1 — Live interruptions do not invalidate engine approvals or playback state

**Spec:** §§5–6, 13; AC-03, AC-14. **Code:** `live_assessment.py:231`, `live_scenario.py:123–132`; compare `engine.py:1574` (`interrupt`).

The driver's `interrupted` branch only resets local lists. It does not call `Engine.interrupt`, advance the generation epoch, revoke engine tokens, cancel undispatched tools, or record history reconciliation. The runner collects client interruption receipts in its return value without applying them to the engine. It also passes a fixed 500 ms playback offset after sleeping, although the reply is still held for whole-response assessment and no client playback acknowledgement establishes that offset.

**Reproduced:** create an approved engine unit, feed `Interrupted()` to the driver: generation epoch remains 1, and the pre-interruption token still releases 960 bytes.

**Fix:** route both provider and client interruptions through one orchestration operation that updates engine state, flushes the scheduler, records the provider receipt, and marks history divergence. Derive truncation offsets from actual software playback acknowledgements; held audio has zero observed playback. Quarantine late events by response/generation identity. Exclude subsequent strict conversational turns when history cannot be reconciled.

**Acceptance:** a live-driver test, rather than only an engine test, proves stale tokens fail, queued tools cannot dispatch, old audio cannot re-enter the next response, and unknown truncation creates an exclusion.

### 2. P1 — Live queues bypass the configured audio limits

**Spec:** §§6, 13, 15, 18; AC-03. **Code:** `live_assessment.py:201–243`, `adapters/openai_realtime.py:86,302`, `adapters/nova_sonic.py:159,334`.

Both adapters construct unbounded `asyncio.Queue()` instances. The driver appends decoded PCM to an unbounded list and only calls `Engine.hold_audio` after a response ends. The engine's byte/duration limit therefore does not protect buffering while a provider is streaming, while a tool is awaited, or when no response-end event arrives. Joining the list also temporarily duplicates the response allocation.

**Reproduced:** 1,500 ordinary 20 ms frames buffer 30 seconds of audio under the engine's configured 2-second limit, with zero engine units and no overflow failure.

**Fix:** account for bytes and duration from ingress, with bounded adapter queues and a shared per-session budget covering all pending buffers. Overflow must persist the typed failure, discard unreleased audio and request provider cancellation. Reserve a path for shutdown/error controls when a queue is full.

**Acceptance:** stalled consumers, long responses and missing end events remain bounded and emit the specified failure without releasing audio.

### 3. P1 — Speech approval can pass unsupported or contradictory money claims

**Spec:** §§6–7, FACT-01, AC-14. **Code:** `semantic_checks.py:206–236`, `live_scenario.py:94–98`, `live_assessment.py:68–101`.

The checker recognises a subset of GBP expressions and treats no recognised claims as safe when no expected claims are required. It does not preserve sign. The runner adds every top-level `*_minor` tool value to a session-wide allow-list, losing the account, field, currency, point-in-time source and factual relationship. A transaction amount or available balance can consequently be narrated as a current balance and pass the membership check.

**Reproduced:** `Your balance is fifty dollars.` with empty expectations returns `safe`; `Your balance is minus ten pounds.` against expected positive 1,000 minor units returns `safe`. A balance claim of ten pounds also passes an allow-list containing both 1,000 and 123,456 without identifying which is the actual balance.

**Fix:** represent claims as typed facts bound to entity, field, sign, ISO currency, source event and validity time. Recognised contradictory claims fail; unsupported currencies/financial constructions abstain as `untestable`. Limit safe classification to assessed scope. Scope authorised facts to the relevant response and current identity/tool state. Add sign, currency, account-switch, stale-fact and swapped-field holdout cases.

**Acceptance:** these examples cannot receive strict approval; the exported assessment describes which claims were checked and which remain unassessable.

### 4. P1 — Native events and live input evidence are not durably preserved

**Spec:** §§4–5, 9–10, 15, 17; native-event preservation and reproducible input/correlation graph. **Code:** `adapters/openai_realtime.py:304–404`, `adapters/nova_sonic.py:340–433`, `adapters/__init__.py:47–113`, `live_scenario.py:83–86,163–182`.

The adapters translate selected fields and discard unhandled native event types. Normalised event models carry no native payload/reference or producer sequence. Debug output is redacted, truncated and nondurable. The driver largely keeps transcript/audio events in memory until response completion, so interrupted output disappears from that path. The runner sends caller PCM without persisting the corresponding input audio/timing events. Its returned scenario hash is not checked against, or bound into, the engine run configuration.

**Fix:** persist each native event at ingress with an explicit redaction/omission record and canonical correlation metadata, including unknown event types. Link normalised events to it. Record synthetic caller source text, generated PCM hash/artifact when retention permits, transformations and speech boundaries. Bind the actual catalogue scenario, adapter configuration, capability report and seed into the immutable run before execution.

**Acceptance:** an interrupted/tool-using live-style replay exports a verifiable input/output event graph, including unknown native events and explicit retained/missing audio, and cannot claim another scenario/provider in its manifest.

### 5. P1 — Synchronous ASR blocks the audio event loop

**Spec:** §18 implementation-stack constraint; §§6, 13 timeouts/backpressure. **Code:** `live_scenario.py:125`, `live_assessment.py:249`, `asr.py:103–110`.

The async receiver calls synchronous `driver.feed`, which invokes synchronous ASR HTTP with a 30-second timeout. That blocks other asyncio work on the same loop: receive tasks, the Nova silence/input pump, interruption and turn-timeout handling. `asyncio.wait_for` cannot enforce its deadline while synchronous work monopolises the loop. The engine can reject a late decision afterwards, but that does not restore realtime responsiveness.

**Fix:** run assessment and associated CPU/storage work in bounded workers or use an async ASR client with an explicit concurrency budget. Continue processing cancellation and ingress while assessment is pending. Recheck response and assessment epochs before applying a worker result; cancellation of a waiter must not permit a late approval.

**Acceptance:** a deliberately stalled ASR backend leaves heartbeats/interruption responsive, respects the decision deadline, and cannot later release the cancelled response.

### 6. P1 — Online findings can claim enforcement without an enforcement action

**Spec:** §§4, 6–7; calibration permits blocking, and only the guardrail enforces. **Code:** `judges/offline.py:113–124`, `judges/service.py:139–166`.

`blocking_enforced` becomes true when a finding is valid and the calibration callback permits blocking. The service then appends the finding; it does not apply a guardrail action or obtain an enforcement acknowledgement. There is no engine consumer of this flag. The Phase 3 notes acknowledge that limitation, but the exported field still asserts an action happened.

**Fix:** separate `blocking_eligible`, requested action and applied enforcement. Keep `blocking_enforced=false` until the guardrail durably records the action and links its decision ID. Either wire a bounded online worker to the live event stream or expose this feature as advisory only.

**Acceptance:** a calibrated finding without a guardrail acknowledgement never reports enforcement; an applied action references the actual blocked unit/call.

### 7. P2 — Nova timestamps are wall-clock values in a monotonic metric contract

**Spec:** §§5, 11; AC-21. **Code:** `adapters/nova_sonic.py:337–338`, `live_assessment.py:172–176,260–294`, `live_scenario.py:73`.

OpenAI uses `time.monotonic()` for `ts_ms`; Nova uses `time.time()`. The driver defaults to treating adapter timestamps as belonging to its own monotonic domain. Supplying valid monotonic speech boundaries for Nova would produce meaningless differences. The runner currently supplies no boundaries, so these latency values are null rather than measured; that avoids fabricated numbers today but leaves the measurement requirement incomplete.

**Fix:** use central monotonic receipt times and an explicit process/boot domain on every adapter event, keeping wall time separately. Record caller speech start/end in that domain and pass them to the driver. Give unknown/foreign clocks explicit exclusion reasons.

**Acceptance:** Nova-style timestamps cannot produce epoch-sized latencies; real runner metrics have input-boundary evidence and clock eligibility.

### 8. P2 — The supported run API/UI can execute only mock scenarios

**Spec:** §§13, 15 functional criteria, Phase 2/4 integrated use. **Code:** `contracts.py:15–29`, `api.py:80–109,299–330`, `apps/web/runs.mjs:40–47`.

`RunConfig` accepts three fixed scenario IDs and only `mock-banking-v1`. API workers always call `run_scenario`; catalogue entries deliberately advertise `runnable:false`. The new live runner is a separate library path with no corresponding CLI command. Thus the operator cannot select a live model/catalogue scenario and obtain the standard run lifecycle, playback session, evidence export and comparison through the supported control surface. The driver calls `release_audio` but does not send the returned PCM to a client or collect playback acknowledgements.

**Fix:** introduce an immutable provider-config registry and a runner factory shared by API and CLI. Probe the exact configuration/profile, distinguish verified runs from diagnostics, construct assessors/cost recorders, bind the actual scenario, and use the existing client scheduler for approved output. Retain an explicit headless mode with no observed-playback claim.

**Acceptance:** one operator flow launches either adapter against a catalogue scenario, records lifecycle and transcripts, shows gate/tool state, exports its signed evidence and represents unavailable assessment/playback honestly.

### 9. P2 — Comparison reports accept unbound and duplicate observations

**Spec:** §§3, 11–12, 15; AC-11 and AC-20. **Code:** `comparison.py:61–128`, `comparison_api.py:24–35`.

The API computes reports directly from caller-supplied observations. It does not resolve run IDs, inspect manifests/capability reports, validate claimed version/clock eligibility, or reject duplicate benchmark-unit observations. `run_id` is optional. Report hashing proves reproduction of the supplied input, not its origin. Duplicate rows can change repetition weighting and denominators. Coverage exclusions are not bound to the report's provider arms.

**Reproduced:** a nonexistent run ID and an exact duplicate observation both pass request validation and report generation.

**Fix:** extract observations server-side from identified, sealed run manifests. Enforce unique experiment/scenario/variant/seed/profile/provider/repetition units, eligibility and arm-specific capability evidence. Preserve manual import as explicitly unverified diagnostic data. Export source manifest hashes and all-family coverage alongside the report.

**Acceptance:** duplicate observations are rejected/deduplicated by explicit policy; nonexistent or ineligible runs cannot silently enter an evidence-backed comparison.

### 10. P2 — Fatal live-provider failures escape as a programming error

**Spec:** §§13, 15, 18; explicit provider failure disposition, durable lifecycle and graceful degradation. **Code:** `live_scenario.py:119–122,143–148,173–176`.

A fatal error or normal stream end returns `None` from the pump. `_run_turns` immediately indexes it as an outcome, raising `TypeError`. Cost recorder closure only occurs on the normal path. A timeout also leaves the driver's partial PCM/transcript state in place, so continuing the next turn risks combining responses.

**Reproduced:** a scripted fatal `ProviderError` produces `TypeError: 'NoneType' object is not subscriptable`, wrapped by the MCP task group, rather than a typed provider failure summary.

**Fix:** return typed outcomes for stream end, fatal error and timeout; persist them before terminating or explicitly restarting the session. Reset/quarantine response buffers, cancel pending generation, and close metering/adapter resources in `finally`. Honour scenario duration/stop conditions in the live runner as well as max turns.

**Acceptance:** fatal/disconnected/timed-out adapters produce durable failure dispositions and no cross-turn PCM reuse, while preserving known tool outcomes and unknown costs.

## Other remaining scope and maintenance work

- **Provider breadth / AC-20:** xAI and Google have no adapters. Remaining unavailable dispositions need dated evidence if used to satisfy the Phase 4 gate. Coverage records omit the applicable profile and the registry selects one latest report per family, rather than the required model/surface/region/profile configuration key. Do not infer that one tested configuration verifies all configurations.
- **Scenario depth / §14:** the catalogue adds useful persona/noise variants but is concentrated on identity and cancellation. Catalogue count does not establish coverage of the distinct safety situations in the specification. Create a requirement-to-scenario map for vulnerable-customer handling, prompt injection, contradictory policy, factual revisions, consent revisions, cancellation/recovery and cost faults; separate mock from live evidence.
- **Calibration / §7:** the supplied synthetic holdout is useful pipeline evidence, not validation for live speech/populations. Keep AI rules advisory until versioned human-labelled locale/audio holdouts satisfy approved error-cost-based policies. Scored voice confirmation must assess actual speech; trusted out-of-band confirmations demonstrate harness mechanics only.
- **Costs / §11:** provenance/unknown accounting is implemented and tested, but recorders are optional at the driver call site and the shipped real tariffs are null. Construct recorders in the integrated runner and verify tariffs before cost comparisons. Unknown charges should remain unknown.
- **Resampling / §18:** native libsoxr is already implemented, contrary to older status prose. However, `AdapterTurnDriver` uses independent one-shot `resample()` calls for mismatched chunks, rather than the available `StreamResampler`; use a stream per response and test continuity/flush behaviour before claiming a mismatched-rate output path.
- **Scenario creation / §13:** `api.py:275–297` checks for a version, then inserts with `ON CONFLICT DO NOTHING` and reports creation without inspecting the winner. Concurrent conflicting writers can receive a success for content that was not stored. Make insert/check atomic and return 409 for a different winning hash.
- **Documentation:** README, CLAUDE and parts of Phase 1/2 notes still describe no live adapters, unsigned manifests, no metadata purge or a Python resampler, despite current implementations. Update a single current acceptance matrix and mark old measurements/status as historical. Existing performance artifacts do not establish current live latency or the full reference-machine targets.
- **UI implementation choice:** the spec fixes React/TypeScript defaults, while current pages are plain JavaScript modules. Record an explicit engineering deviation or migrate when building the integrated provider/run flow; this is lower priority than the safety defects.

## Recommended sequence

1. **Close live safety and evidence defects:** findings 1–6. Add focused driver-to-engine integration tests around the reproduced cases, not additional copies of engine-only tests.
2. **Ship one complete operator path:** findings 7, 8 and 10; exact provider/scenario/config binding, capability gate, bounded workers, caller boundaries, scheduler acknowledgements, metering and signed export. Prove it with both a scripted adapter in CI and an explicitly enabled synthetic live smoke test.
3. **Make comparisons derive from evidence:** finding 9; deterministic run extraction, unique paired units, capability/clock/version exclusions and reproducible comparison exports.
4. **Then expand the study:** broaden scenario strata, establish calibration and tariff evidence, and run a paired exploratory study with the specified 20 repetitions per scenario/provider/profile and randomised arm order. Keep physical audibility and unavailable dimensions explicit.
5. **Refresh release evidence:** run the full PostgreSQL/browser suite and rebaseline the current component/live paths on a declared machine. Reconcile the acceptance matrix with dated artifacts before declaring the applicable phase gates complete.
