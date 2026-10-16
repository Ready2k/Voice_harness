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
