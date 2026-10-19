# Phase 0 adapter capability / unknown matrix

Status: **APPROVED by user 2026-10-16 as the Phase 0 baseline (all cells unknown; approval does not verify anything).** Every cell is `unknown` (spec §2). No provider was contacted; no model ID is verified.
Cells move to `verified` only with documentation evidence AND a passing account/region probe (`python -m voice_harness.probe`). Candidate IDs are discovery seeds, not configured IDs.

| Row | OpenAI (`gpt-realtime` seed) | Amazon Nova Sonic (Bedrock ID TBD) | xAI (`grok-voice-latest` seed) | Google Gemini Live (TBD) |
|---|---|---|---|---|
| Model ID / lifecycle / alias resolution | unknown | unknown | unknown | unknown |
| Account access, region, quotas, pricing | unknown | unknown | unknown | unknown |
| Input/output codecs and rates | unknown | unknown | unknown | unknown |
| Audio in / audio out | unknown | unknown | unknown | unknown |
| Tool args and result delivery | unknown | unknown | unknown | unknown |
| Transcript finality / audio alignment | unknown | unknown | unknown | unknown |
| Turn signalling / VAD | unknown | unknown | unknown | unknown |
| Cancel / truncate | unknown | unknown | unknown | unknown |
| Usage granularity | unknown | unknown | unknown | unknown |
| Reconnect / resume | unknown | unknown | unknown (fresh connection works; documented history replay untested) | unknown |
| Direct MCP (else `gateway_bridge`) | unknown | unknown | unknown | unknown |
| Retention terms (operator attestation) | unknown | unknown | unknown | unknown |

Open items needing the user: provider accounts/keys, target regions, Gemini Developer API vs Vertex AI.
Coverage state for all four families stays `not_implemented` (`coverage.py`) until Phase 2.

## Update after live probes (2026-10-16): APPROVED by user 2026-10-16
The first table above is the original baseline. Approved state, from `artifacts/phase0/*-capability-report.json`:

| Row | OpenAI `gpt-realtime-2.1` | Amazon `amazon.nova-2-5-sonic` (us-east-1) | xAI | Google |
|---|---|---|---|---|
| Account access / model discovery | verified | verified (alias, `version_unresolved`) | verified (`GET /v1/models` lists no voice models; `session.created` returns `grok-voice-3.0`; alias `grok-voice-latest` resolved to it) | observed (Vertex AI via Application Default Credentials; `gemini-live-2.5-flash-native-audio` is the only native-audio S2S Gemini model listed; `us-central1` connects, `global` refused the model at connect; no model version returned, so unresolved) |
| Input / output codecs | observed (24 kHz PCM) | observed (16 kHz in, 24 kHz out) | observed (24 kHz PCM both ways; other codecs untested) | observed (16 kHz PCM in; output `audio/pcm` with no rate declared, 24 kHz inferred from byte count vs audio tokens) |
| Audio in / out | verified (speech recognised, synthetic voice) | verified (speech recognised, synthetic voice) | verified (synthetic speech transcribed; a silence-only manual commit gets no `committed` event) | observed (synthetic speech transcribed and answered; output audio PCM) |
| Tool args and results | verified (native; MCP bridged = `gateway_bridge`) | verified (native) | verified (native function call, `function_call_output`, `response.create`; `gateway_bridge`) | observed (`toolCall` with id, `toolResponse` by id; `gateway_bridge`) |
| Transcript finality | verified | observed | verified (output delta/done; input `.updated` is cumulative and `.completed` repeats per item) | observed (incremental text fragments with an empty `finished` marker) |
| Audio alignment | unknown | unknown | unknown (`audio_duration_ms` and `start_time` seen, not verified as alignment) | unknown (no timing fields seen) |
| Turn signalling | verified (manual turns) | observed | verified (`response.done`; manual and `server_vad` `speech_started`/`speech_stopped`) | observed (no turn start; `turnComplete` ends a turn including the tool-calling turn; automatic activity detection) |
| Cancel / truncate | verified (speech barge-in via server VAD cancels the response in about 0.2 s; truncate acknowledged) | observed (speech barge-in: `INTERRUPTED` stopReason about 0.5 s after speech began; two runs) | verified (`response.cancel` gives `response.done` status `cancelled`, `conversation.item.truncate` acknowledged; speech ends the in-flight response but reports status `completed` with empty output, and truncating that item is only acknowledged up to about the playback position at speech onset) | observed (server `interrupted` on barge-in about 0.65 s after speech began; no client cancel or truncate message, AC-09 interruption excluded) |
| Usage granularity | verified | verified | verified (top-level `usage` on `response.done`; text/audio token split, audio seconds; no prices recorded) | observed (`usageMetadata` on `turnComplete`: token counts by modality; no prices recorded) |
| Reconnect / resume | unknown | unknown | unknown (fresh connection works; documented history replay untested) | unknown (resumption handle issued; reconnect not exercised) |
| Direct MCP | unknown | unknown | unknown (session accepts an `mcp` tool and the server tries discovery itself; no reachable MCP server to exercise it) | unknown (not exercised) |
| Terms attestation | recorded 2026-10-16 (synthetic data only) | recorded 2026-10-16 (synthetic data only) | recorded 2026-10-18 (operator statement; synthetic data only) | recorded 2026-10-18 (operator statement; synthetic data only) |

Google column: added 2026-10-18 from `scripts/probe/google_gemini_probe.py` (`artifacts/phase0/google_gemini-observations-*.json`, report `artifacts/phase0/google_gemini-capability-report.json`, expires 2026-10-25), observed values only; no row is `verified` because the Vertex Live pages could only be read in part. See `docs/PHASE4_NOTES_google.md`.

xAI column: added 2026-10-18 from `scripts/probe/xai_probe.py` (`artifacts/phase0/xai-observations-*.json`, report `artifacts/phase0/xai-capability-report.json`, expires 2026-10-25), observed values only; see `docs/PHASE4_NOTES_xai.md` for what stays unknown. Coverage for `xai_grok_voice` is derived by `coverage.py` like the other families.

## Gemini 3.8 update (2026-10-19)

The configured Gemini model is now `gemini-3.8-live` on Vertex AI `us-central1`. A fresh
probe accepted setup, returned PCM audio and transcripts, and completed a tool round trip.
The global endpoint rejected this model for the current project. The table above retains
the historical 2.5 observations; refreshed evidence and benchmark results are recorded in
`../GEMINI_3_8_BENCHMARK_2026-10-19.md`.
