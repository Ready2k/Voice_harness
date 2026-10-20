# Gemini turn-boundary diagnostic — 20 October 2026

Three sequential live runs of happy-balance-street-001, fixture seed 42, standardised-v2 and the same seven tool definitions. Static macOS caller utterances were synthesised once and reused; deterministic 10 dB noise was applied by the runner. results.json verifies identical post-noise PCM hashes, instructions hashes and tool hashes across all three conditions.

| Condition | Result | Total run wall time |
|---|---|---:|
| Automatic detection, 45-second turn timeout | TURN_TIMEOUT after the balance question | 63.28 s |
| Explicit activityStart/activityEnd, 45-second timeout | Pass; £1,234.56 delivered | 27.55 s |
| Automatic detection, 120-second timeout | TURN_TIMEOUT after the balance question | 137.93 s |

With explicit boundaries, get_account_summary was requested about 0.61 s after the follow-up stream ended. Both automatic cases verified identity, then made no balance-tool call after the follow-up. Extending the timeout alone did not resolve the stall. This strongly implicates automatic input activity/turn detection under the tested noise and transport, rather than an insufficient 45-second timer. It does not establish whether the underlying defect is server VAD, end-of-audio signalling or another provider interaction, and one sample per condition is not a reliability estimate.

The diagnostic temporarily selected manual turn detection in Gemini's session config and called its existing request_turn() after each caller clip. In automatic mode that call is a no-op; in manual mode it sends activityEnd. Each manual activityStart/activityEnd pair is recorded in results.json. The diagnostic changed no production default, target prompt, tool interface or timeout setting. Session overrides are recorded in each run's live binding as diagnostic_turn_detection and diagnostic_turn_timeout_seconds.

WAV files contain caller clips and gate-released agent replies joined with short gaps. Actual waiting times are omitted. They are fresh diagnostic recordings, not the prior benchmark recordings. Local ledgers are in .local/gemini-turn-diagnostic-2026-10-20/<condition>/diagnostic.sqlite. No automatic subjective judges were configured.

Recommended next change: use explicit turn boundaries for fixed prerecorded Gemini tests, retaining automatic detection for tests specifically evaluating natural speech endpointing. Keep the response timeout as a separate failure bound; do not treat increasing it as the fix. Validate clean/noisy and tool/interruption cases before changing defaults.
