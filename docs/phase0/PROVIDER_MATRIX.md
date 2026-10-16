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
| Reconnect / resume | unknown | unknown | unknown | unknown |
| Direct MCP (else `gateway_bridge`) | unknown | unknown | unknown | unknown |
| Retention terms (operator attestation) | unknown | unknown | unknown | unknown |

Open items needing the user: provider accounts/keys, target regions, Gemini Developer API vs Vertex AI.
Coverage state for all four families stays `not_implemented` (`coverage.py`) until Phase 2.

## Update after live probes (2026-10-16): APPROVED by user 2026-10-16
The first table above is the original baseline. Approved state, from `artifacts/phase0/*-capability-report.json`:

| Row | OpenAI `gpt-realtime-2.1` | Amazon `amazon.nova-2-5-sonic` (us-east-1) | xAI | Google |
|---|---|---|---|---|
| Account access / model discovery | verified | verified (alias, `version_unresolved`) | unknown (no API key) | unknown (no API key) |
| Input / output codecs | observed (24 kHz PCM) | observed (16 kHz in, 24 kHz out) | unknown | unknown |
| Audio in / out | verified | verified (speech recognised, synthetic voice) | unknown | unknown |
| Tool args and results | verified (native; MCP bridged = `gateway_bridge`) | verified (native) | unknown | unknown |
| Transcript finality | verified | observed | unknown | unknown |
| Audio alignment | unknown | unknown | unknown | unknown |
| Turn signalling | verified (manual turns) | observed | unknown | unknown |
| Cancel / truncate | observed (cancel; truncate ack not seen) | observed (speech barge-in: `INTERRUPTED` stopReason about 0.5 s after speech began; two runs) | unknown | unknown |
| Usage granularity | verified | verified | unknown | unknown |
| Reconnect / resume | unknown | unknown | unknown | unknown |
| Direct MCP | unknown | unknown | unknown | unknown |
| Terms attestation | recorded 2026-10-16 (synthetic data only) | recorded 2026-10-16 (synthetic data only) | none | none |

xAI and Google: operator has no API access yet, so no probe is possible. Coverage stays `not_implemented` and documentation-only evidence applies (`DOC_EVIDENCE.md`). Reports expire 2026-10-23 and must be re-probed after that.
