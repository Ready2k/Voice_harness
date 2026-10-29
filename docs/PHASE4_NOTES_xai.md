# Phase 4 notes: xAI Grok Voice adapter

Date 2026-10-18. Model pinned to `grok-voice-3.0`. Key from `xAI_API_KEY` (never logged, written or echoed; a scan of every changed file and artifact for the key value found no match).

## What was added
- `src/voice_harness/adapters/xai_voice.py` (`XaiVoiceAdapter`, `conformance_factory`), `src/voice_harness/xai_report.py`, `probe from-xai-observations`, `scripts/probe/xai_probe.py`.
- `coverage.py` registers the adapter module; `live_providers.py` registers `xai-voice-live` (family `xai_grok_voice`, tested model `grok-voice-3.0`, availability = the same ASR and caller-voice gates plus `xAI_API_KEY`, reason `NO_XAI_API_KEY`); `contracts.RunConfig.provider_config_id` allows it (contracts regenerated). The Runs page needed no change: `providerChoices` is driven by `/api/v1/live-providers`.
- Evidence: `artifacts/phase0/xai-observations-2026-10-18T19-06-51-161Z.json`, `xai-probe-2026-10-18T19-06-51-161Z.jsonl` (audio as length and hash only), `xai-capability-report.json` (id `cap_xai_grok_voice_3_0_20261018`, expires 2026-10-25); `artifacts/phase2/xai_grok_voice-ac09.json`; `artifacts/phase4/xai-live-smoke-bundle.json`.

## Observed (probe, one run of the final probe script; earlier runs were discarded because the barge-in stage was invalid)
- `grok-voice-latest` alias resolves to `grok-voice-3.0` in `session.created` (observed once; runs pin the versioned name). `GET /v1/models` lists no voice models, so model discovery is by connecting.
- `session.update` takes no `type` or `model`. `audio/pcm` 24 kHz is echoed for input and output. `ping` events arrive and are ignored.
- Text turn, tool call (`response.function_call_arguments.done`), `function_call_output` plus `response.create`, output audio, output transcript delta/done, `response.done` with a top-level `usage` (token details, `output_audio_seconds`, `billable_audio_seconds`; `response.usage` inside it is empty). A tool-calling response can also carry a spoken preamble ("Checking that now.").
- Input transcription with `grok-transcribe`: `.updated` is cumulative; `.completed` repeats per item with growing then identical text. The adapter emits a user `TranscriptFinal` only when the text changes.
- Audio is generated much faster than real time (about 7x in one uninterrupted sample: about 2 minutes of audio for a twelve-sentence answer in about 16 s).
- `response.cancel` gives `response.done` status `cancelled`; `conversation.item.truncate` is acknowledged by `conversation.item.truncated`.
- Server speech detection ends the in-flight response even when `turn_detection` is null or `create_response` is false: `response.done` status `completed` with empty `output`, immediately before `input_audio_buffer.speech_started`, which is reported about 1.2 s after speech starts streaming. With `server_vad` the server then answers by itself; `create_response: false` is echoed in `session.updated` but not honoured, `interrupt_response` and `threshold` are silently dropped, `silence_duration_ms` is kept.
- Truncating a speech-cut item is only acknowledged up to about the playback position at speech onset (a larger `audio_end_ms` gets no event, no error). The adapter estimates that position from the first loud input chunk sent after first output audio, clamps to it and retries lower on no ack.
- An unknown client event gives an `error` (`invalid_event`) and the connection stays open. The error text lists accepted client event types, including `response.audio.delta.playback_started` and `playback_completed`, `conversation.item.delete`, `input_audio_buffer.clear`. The same list contains `test.*` and `regression_test.*` internals; none are used.
- A silence-only manual `input_audio_buffer.commit` produced no `committed` event and no error.
- `mcp` tool declarations are accepted by `session.update`; with an unreachable URL the server emitted `mcp_list_tools.failed` itself.

## Assumed or not observed (stays `unknown` or unclaimed)
- Direct MCP against a reachable server; reconnect/resume (history replay); audio alignment (`audio_duration_ms` and `start_time` exist, not verified as alignment); other codecs and sample rates; regions, quotas, retention, rate limits, terms. xAI's error-code list was not found, so the adapter's fatal set (auth and quota style codes) is a guess.
- The playback feedback events (`response.audio.delta.playback_started/completed`) look like the intended way to report played position to the server; not used or probed.
- Barge-in was exercised with one synthetic phrase (macOS `say`); no noise, accents or repeats.
- `operator_terms_attestation` is left empty: the existing attestation names only AWS Bedrock and OpenAI, and it was not extended on the operator's behalf.

## AC-09 (live)
Passed for audio_in, audio_out, tool_result_correlation, interruption and error; nothing excluded. Interruption is verified on the provider-detected path with manual turn detection (speech streams in, the server cuts the response, the adapter reports one `Interrupted` for the cut item, no audio follows, truncate acknowledged). The driver run history: before a driver fix (waiting for audio of a new item rather than the previous one) two of three runs failed on truncate; after it 5 of 5 passed. The conformance judge gained one additive field (`acknowledged_audio_end_ms`, `truncate_attempts`) shown only when the adapter had to retry lower; it is not set in the committed result because the first truncate was acknowledged. `Interrupted.audio_end_ms` for a speech-cut item is the estimated played position, not the generated audio.

## Live smoke (one sample, through the API runner)
`identity-impatient-en-gb-clean-001` on `xai-voice-live`, macOS `say` caller voice, `VOICE_HARNESS_LIVE_ASR=openai` independent ASR, no auto judges. Run completed in 12.4 s, overall assurance `pass` (IDENTITY-01, SCENARIO-PROPOSAL-01, AUDIO-01 pass; FACT-01 pass with `NO_AMOUNT_CLAIM`; CONFIRM-01 and CARE-01 not applicable). The reply was "I can check that right away, but first I need to verify your identity. What's your security answer?". Provider session bound with requested and returned model `grok-voice-3.0`, endpoint host `api.x.ai`, `websockets` 17.2, capability status `verified`. Seven usage reports were recorded (xAI audio/text input and output tokens, three ASR meters). No tool calls were made in this scenario, so it does not exercise the gateway round trip live (AC-09 does). A single sample proves little; no repeats were run. The exported bundle verifies offline (`verify-bundle --public-key <that run's signing key>`: integrity true, signature ok; the run used an ephemeral signing key, so the key is not committed).

## Usage and cost
Usage is recorded only as xAI reported it (token details, audio seconds); no prices are recorded and none were invented. The documented $0.08/min in `DOC_EVIDENCE.md` is a doc claim, not applied. `billable_audio_seconds` differs from `output_audio_seconds` in the samples (for example 1 versus 1.28, and 3 versus 20.4 for a speech-cut response), so whether cut-off audio is billed is not established. Paid usage across probes, discovery experiments, AC-09 runs and the smoke run was a few dozen short turns, one of them an uninterrupted long answer of about 2 minutes of audio.

## Text-input cost (2026-10-29)

xAI lists `$0.004 / text input` beside the $0.08/min rate and does not define the unit. The adapter counts each user text item
(`send_text`) and reports the total at close; the recorder prices it as an estimate flagged `UNIT_ASSUMED`. Tool results and audio
are not counted. If xAI defines the unit differently, only the counting rule changes.
