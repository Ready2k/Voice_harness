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

- Live, once each, synthetic data (OpenAI Realtime): `identity-fault-tool-error-001` — the model called
  `verify_identity` through the gateway and the reply was approved; the call was refused `INVALID_ARGUMENT`
  (the model's arguments failed validation before the injected timeout could apply), so the injected-fault
  *outcome* is still not shown live. `identity-fault-caller-interrupts-001` — single turn, no real barge-in
  observed. Live chooser: one real OpenAI call picked an eligible branch with no simulator failure.

## Not yet established

- Fault paths (`tool_error`, `tool_latency`, `caller_interrupts`) against a live provider; Nova runner use.
- Live-model chooser for the AC-16 simulator (`model_simulator.py` is offline-tested with an injected chooser: model picks branch, oracle rejected, bad output attributed to the simulator with fallback, hashed prompt/config recorded; `OpenAIChooser` (opt-in `VOICE_HARNESS_LIVE_JUDGES=openai`) added). Synthetic commit (AC-15) is tested through the real MCP gateway with a scripted adapter (`tests/test_phase4_commit.py`: denial before identity/confirmation, single-use consumption, changed-amount invalidation, restart idempotency); a live model has not been driven to propose a commit.
- A first OpenAI vs Nova comparison on the catalogue (needs go-ahead for API spend).
- The synthetic caller uses macOS `say`; the catalogue declares locales but `say` voice selection is not
  yet locale-aware. Narrowband/noise are simple synthetic shaping, not measured telephony.
- xAI and Google adapters remain blocked on API keys.
