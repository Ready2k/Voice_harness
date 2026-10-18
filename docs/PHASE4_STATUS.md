# Phase 4 status (in progress)

## Delivered

- **Scenario catalogue** (`scenario_catalogue.py`): 18 persona/locale/profile live-adapter scenarios built as ordinary
  `ScenarioDefinition` records (personas: impatient, bereaved/vulnerable, social engineer; locales
  en-GB/en-US; audio profiles clean, street noise 10 dB, telephone narrowband) plus 4 fault-injected (22 entries)
  identity variants. `FaultInjection` is a separate closed model kept outside the definition, so the
  three mock built-ins keep their content hashes. `GET /api/v1/scenarios` lists them with
  `execution: "live_adapter"`, `runnable: false` (the mock runner does not execute them) and their faults.
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

- Nova runner use; the live runs are single samples, not repeated.
- Live-model chooser for the AC-16 simulator (`model_simulator.py` is offline-tested with an injected chooser: model picks branch, oracle rejected, bad output attributed to the simulator with fallback, hashed prompt/config recorded; `OpenAIChooser` (opt-in `VOICE_HARNESS_LIVE_JUDGES=openai`) added). Synthetic commit (AC-15) is tested through the real MCP gateway with a scripted adapter (`tests/test_phase4_commit.py`: denial before identity/confirmation, single-use consumption, changed-amount invalidation, restart idempotency); a live model has not been driven to propose a commit.
- A first OpenAI vs Nova comparison on the catalogue (needs go-ahead for API spend).
- The synthetic caller uses macOS `say`; the catalogue declares locales but `say` voice selection is not
  yet locale-aware. Narrowband/noise are simple synthetic shaping, not measured telephony.
- xAI and Google adapters remain blocked on API keys.

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
