# Provider transport tuning — 20 October 2026

The harness now selects native turn controls by provider and caller source, without changing the common standardised-v2 system prompt, tools, independent assessment or scenario expectations.

| Provider | Fixed prerecorded and adaptive caller | Human microphone | Native input audio |
|---|---|---|---|
| OpenAI | Manual commit and response creation | Automatic server VAD | 24 kHz |
| xAI | Manual commit and response creation | Automatic server VAD | 24 kHz |
| Gemini | Explicit activityStart/activityEnd; automatic detection disabled | Automatic server VAD | Resampled to 16 kHz |
| Nova | Continuous audio and provider endpointing; MEDIUM for fixed, LOW for adaptive | Continuous provider endpointing, MEDIUM | Resampled to 16 kHz |

All use paced 20 ms input chunks and 24 kHz output. Nova retains its silence pump: no manual commit or client truncation is invented for it. Gemini and Nova still disclose unsupported client truncation; local audio fencing does not establish reconciled provider history. OpenAI/xAI preserve their native cancellation and truncation receipts. AC-09 speech-detection probes explicitly exercise provider VAD; they must not be converted to manual commits just to pass. Human microphone sessions likewise retain automatic speech detection.

Every new live binding records harness_transport (input mode, native turn control, rates, chunking, endpointing, truncation support, timeout and the current response budget). The fixed runner commits each completed caller clip. Adaptive calls already commit and now share the profile selection. The response timeout remains 45 seconds; it was not increased to conceal turn-detection stalls.

## Evidence

The Gemini diagnostic reused identical noisy PCM and prompt/tool hashes in three sequential conditions: automatic/45 seconds timed out, explicit/45 seconds delivered £1,234.56, and automatic/120 seconds still timed out. See [diagnostic recordings and timelines](../artifacts/gemini-turn-diagnostic-2026-10-20/README.md).

A new full matrix covered all 34 live scenarios against all four providers, one first attempt per cell:

| Provider | Assurance passes | Other first attempts |
|---|---:|---|
| OpenAI | 33/34 | Policy-question timeout |
| xAI | 34/34 | None |
| Gemini | 34/34 | None |
| Nova | 34/34 | None |

The previous common-tool matrix had 128 assurance passes, 7 untestable outcomes and 1 assessment failure. The tuned matrix has 135 passes and 1 untestable outcome. These samples support the transport fix; they do not establish a statistically reliable provider ranking or universal success.

All four providers passed the explicit failed-authentication scenario and both cancellation paths. All expected positive tool sequences were observed except Nova's requested handoff: it asks why the customer wants a person and never calls transfer_to_human. That remains a task-completion gap despite an assurance pass. No caller wording or expected outcome was changed to hide it.

## Bounded response allowance and overflow reporting

The remaining OpenAI policy failure hit the 15-second response-audio cap. A separate 30-second diagnostic completed with a 15.35-second policy explanation. The final shared cap is 20 seconds (960,000 PCM bytes), keeping a common bounded allowance across providers. An unpatched production-path follow-up under that final cap passed, with an 18.9-second explanation. The original batch timeout remains preserved; the whole 136-cell matrix has not been rerun after this budget change.

Cancellation previously reset the driver's overflow state, allowing an ingress audio-limit failure to be reported later as a timeout. The runner now preserves the typed overflow, fences the response and returns promptly; the API records AUDIO_BACKPRESSURE. Regression tests check no blocked audio is released and the run cannot be marked passed. Backpressure evidence reports the applicable response byte limit.

- [First-attempt results report](../reports/provider-transport-v1-2026-10-20.html)
- [First-attempt matrix](../artifacts/provider-matrix/matrix-provider-transport-v1-2026-10-20.json)
- [Recorded transport settings, hashes and tool-sequence audit](../artifacts/provider-transport-v1-2026-10-20/audit.json)
- [OpenAI 30-second diagnostic](../artifacts/provider-transport-v1-2026-10-20/openai-policy-budget-diagnostic.json)
- [OpenAI final 20-second follow-up](../artifacts/provider-transport-v1-2026-10-20/openai-policy-final20.json)

The main matrix ledger is .local/provider-transport-v1-2026-10-20/development.sqlite. Independent ASR and deterministic checks were enabled; automatic subjective judges were not configured. Some core tests overlapped the matrix on this host. This is not an isolated latency benchmark. No automatic retries replaced first attempts. Existing capability limitations remain disclosed in run bindings.

## Final validation

680 Python tests ran successfully (8 opt-in skips), including native-profile selection, explicit commits, interruption/late-ASR fencing, overflow classification and API terminal status. All 86 browser tests passed. Ruff passed across src, tests and scripts; git diff --check passed. These deterministic tests complement the 136 live first attempts and the separate bounded OpenAI follow-ups.
