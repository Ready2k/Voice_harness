# ElevenLabs Agents adapter (`elevenlabs-live`)

Status: implemented, unit-tested against an offline fake server and smoke-tested live on 2026-10-25 (text probe, then
`happy-balance-clean-001` and `identity-fault-caller-interrupts-001` through `run_live`). There is no Phase 0
observation log, capability report or AC-09 run yet. **Update 2026-10-29:** `scripts/probe/elevenlabs_probe.py`,
`elevenlabs_report.py` and an AC-09 run (interruption excluded) now exist; coverage is `implemented_and_verified` while
report `cap_elevenlabs_agents_20261029` is current (7 days).

## Setup

1. `ELEVENLABS_API_KEY` in `.env` (written by `npx @elevenlabs/cli onboard init`; `make serve-live` loads it).
2. `PYTHONPATH=src .venv/bin/python -m voice_harness.adapters.elevenlabs_agent setup [--llm MODEL]` creates (or
   updates) the harness agent and records `.local/elevenlabs-agent.json` (git-ignored). `ELEVENLABS_AGENT_ID`
   overrides the stored id. Re-run `setup` whenever `live_providers.TOOLS` changes: sessions refuse a stale agent
   (`PROVIDER_AGENT_TOOLS_STALE`).
3. Configuration parity (2026-10-27, `PROVIDER_CONFIG_PARITY.md`): `agent_body` no longer sets the LLM
   `temperature: 0` (provider default, as for every provider). **An agent provisioned earlier keeps temperature 0 until
   `setup` is rerun.** Sessions are not refused for this: each run records `agent_llm`, `agent_config_current` and
   `sampling.stored_agent_temperature`, and the bake-off report flags the agent as outside the sampling policy.
   `status` prints `config_current`.

## Differences from the other providers

* A conversation runs against a stored agent. The 7 banking tools are **client tools on the agent**; the prompt,
  first message, language and voice are per-session overrides. The agent's LLM (default `gemini-3.5-flash`,
  `--llm` / `ELEVENLABS_AGENT_LLM`) is part of what is under test and is **not echoed by the session**.
* Turn detection is the server's VAD only (no manual commit): transport reports `provider_vad`, `request_turn`
  is a no-op, and the fixed caller's 1.2 s trailing silence ends the turn.
* No client cancel/truncate (`interrupt_truncate: unsupported`); server `interruption` is reported.
* No response start/end marker and **no usage event**: turns open on the first audio/text/tool call and close on
  `agent_response`, a tool call (as for xAI, the tool-calling response ends there) or an interruption. The adapter
  therefore reports the connected session time at close (`Usage` with `connected_seconds`, harness-measured); the recorder turns
  it into an `estimated` `connected_audio_seconds` meter priced at the $0.08/min Agents rate in the 2026-10-29 table. The agent
  LLM is a second `estimated` line (`agent_llm_estimated_seconds`, service `elevenlabs_agent_llm`) from ElevenLabs' own
  per-minute calculator figures (2026-10-29: 3.5 Flash $0.0071, 3.5 Flash-Lite $0.0015, 2.5 Flash $0.0014), applied to connected
  time. Burst pricing and any silence discount are not included.
* The agent LLM moved to `gemini-3.5-flash` on 2026-10-29 (2.5 Flash is being deprecated 2026-11-30); runs before that used
  2.5 Flash and are not comparable. The stored agent still has the old `temperature: 0.0` (a PATCH does not clear it), so
  the state file records 0.0, not `provider_default`. Runs
  without that table stay unpriced. Transcripts are final-only.
* Audio is `pcm_24000` both ways, so no resampling.

## Verified live (2026-10-25)

* Protocol shapes match the adapter: `conversation_initiation_metadata` (audio formats echo `pcm_24000`), `audio`,
  `agent_response`, `client_tool_call` / `client_tool_result`, `interruption`, `agent_response_correction`,
  `agent_tool_response`, `ping`/`pong`. An empty `first_message` override suppresses the greeting.
* **`agent_response` (text) arrives before its audio finishes**, so a turn closes on audio silence (`IDLE_S` 0.7 s)
  after the answer, not on the text. A tool-calling turn is closed when its result is submitted, so the reply that
  uses the result is its own turn (as for xAI).
* `happy-balance-clean-001`: both turns `approved`, `verify_identity` and `get_account_summary` executed, ASR
  `CLAIMS_MATCH_EXPECTED`; the same outcome as xAI on the same scenario. Session setup 0.3 s (xAI 1.2 s).
* `identity-fault-caller-interrupts-001`: turn 0 is `barge_in_not_assessed` (no client truncate, by design); turn 1
  `approved` in 4 of 5 runs and `no_audio` once (not reproduced; cause unknown, the trace is the next step).

## Still unverified

Whether `turn_timeout: -1` is honoured over long silences; `contextual_update` semantics (confirmation scenarios);
user transcript timing; the billing/usage source; agent LLM variance (default `gemini-3.5-flash`). Next steps:
re-probe before the report expires; operator terms attestation.

## Turn detection fixes (2026-10-25 bake-off)

- The server's VAD only ends the caller's turn once it hears silence, and the harness's caller stream stops at its last frame. In a telephone scenario about 3 runs in 10 never got a transcript (`TURN_TIMEOUT`). `request_turn` now feeds real-time silence (up to 6 s) until the server transcribes the caller or opens a turn; 0 of 20 runs timed out afterwards.
- A server `interruption` before any agent audio is ignored: nothing was speaking, so there is nothing to cut off.
- The agent uses `expressive_mode` (eleven_v4_turbo), so replies can carry tags such as `[sad]`. They are delivery cues in the TTS, not spoken text, and appear in the transcript.
