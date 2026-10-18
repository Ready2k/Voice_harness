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
| Account access / model discovery | verified | verified (alias, `version_unresolved`) | verified (`GET /v1/models` lists no voice models; `session.created` returns `grok-voice-3.0`; alias `grok-voice-latest` resolved to it) | unknown (no API key) |
| Input / output codecs | observed (24 kHz PCM) | observed (16 kHz in, 24 kHz out) | observed (24 kHz PCM both ways; other codecs untested) | unknown |
| Audio in / out | verified (speech recognised, synthetic voice) | verified (speech recognised, synthetic voice) | verified (synthetic speech transcribed; a silence-only manual commit gets no `committed` event) | unknown |
| Tool args and results | verified (native; MCP bridged = `gateway_bridge`) | verified (native) | verified (native function call, `function_call_output`, `response.create`; `gateway_bridge`) | unknown |
| Transcript finality | verified | observed | verified (output delta/done; input `.updated` is cumulative and `.completed` repeats per item) | unknown |
| Audio alignment | unknown | unknown | unknown (`audio_duration_ms` and `start_time` seen, not verified as alignment) | unknown |
| Turn signalling | verified (manual turns) | observed | verified (`response.done`; manual and `server_vad` `speech_started`/`speech_stopped`) | unknown |
| Cancel / truncate | verified (speech barge-in via server VAD cancels the response in about 0.2 s; truncate acknowledged) | observed (speech barge-in: `INTERRUPTED` stopReason about 0.5 s after speech began; two runs) | verified (`response.cancel` gives `response.done` status `cancelled`, `conversation.item.truncate` acknowledged; speech ends the in-flight response but reports status `completed` with empty output, and truncating that item is only acknowledged up to about the playback position at speech onset) | unknown |
| Usage granularity | verified | verified | verified (top-level `usage` on `response.done`; text/audio token split, audio seconds; no prices recorded) | unknown |
| Reconnect / resume | unknown | unknown | unknown (fresh connection works; documented history replay untested) | unknown |
| Direct MCP | unknown | unknown | unknown (session accepts an `mcp` tool and the server tries discovery itself; no reachable MCP server to exercise it) | unknown |
| Terms attestation | recorded 2026-10-16 (synthetic data only) | recorded 2026-10-16 (synthetic data only) | none (the 2026-10-16 attestation does not cover xAI; left unattested) | none |

Google: operator has no API access yet, so no probe is possible. Coverage stays `not_implemented` and documentation-only evidence applies (`DOC_EVIDENCE.md`). Reports expire 2026-10-23 and must be re-probed after that.

xAI column: added 2026-10-18 from `scripts/probe/xai_probe.py` (`artifacts/phase0/xai-observations-*.json`, report `artifacts/phase0/xai-capability-report.json`, expires 2026-10-25), observed values only; see `docs/PHASE4_NOTES_xai.md` for what stays unknown. Coverage for `xai_grok_voice` is derived by `coverage.py` like the other families.
