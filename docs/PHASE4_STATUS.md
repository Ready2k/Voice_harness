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

## Not yet established

- `tool_latency` against a live provider; Nova runner use; a live model proposing a commit.
- Live-model chooser for the AC-16 simulator (`model_simulator.py` is offline-tested with an injected chooser: model picks branch, oracle rejected, bad output attributed to the simulator with fallback, hashed prompt/config recorded; `OpenAIChooser` (opt-in `VOICE_HARNESS_LIVE_JUDGES=openai`) added). Synthetic commit (AC-15) is tested through the real MCP gateway with a scripted adapter (`tests/test_phase4_commit.py`: denial before identity/confirmation, single-use consumption, changed-amount invalidation, restart idempotency); a live model has not been driven to propose a commit.
- A first OpenAI vs Nova comparison on the catalogue (needs go-ahead for API spend).
- The synthetic caller uses macOS `say`; the catalogue declares locales but `say` voice selection is not
  yet locale-aware. Narrowband/noise are simple synthetic shaping, not measured telephony.
- xAI and Google adapters remain blocked on API keys.
