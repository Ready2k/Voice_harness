# Live advanced comparison — unblinded run notes

Completed 2026-10-26 13:00 UTC.

**115 complete three-turn conversations; 5 failed attempts out of 120 planned cells.** All five providers were exercised in both prompt conditions. Human ratings have not been collected, so there is no human-likeness score or winner yet.

Completed review audio: 79.0 minutes. Format: stereo 24 kHz, 16-bit WAV, caller left / assistant right. The supplied caller audio was cached once and reused across conditions and providers. Completed files and retained prior audio were checked against their SHA-256 values.

## Failures

All five scored failures exceeded the configured 30-second assistant-audio response limit. They remain failures and were not retried to improve results. Their partial recordings remain in clips/ but are excluded from the blind page. Two early failure records contain only the exception type; their received-audio counters exceed 30 seconds. Later records include the explicit RESPONSE_AUDIO_LIMIT code.

- openai-realtime-live / common / failed-payment / repetition 1: `ed3882f8ad01e722`
- openai-realtime-live / common / bereavement / repetition 2: `dc683b33d0f8c8a5`
- openai-realtime-live / common / bereavement / repetition 1: `4ac748c7d9e2037e`
- openai-realtime-live / common / lost-card / repetition 2: `9c5d681a4dd1a573`
- xai-voice-live / common / lost-card / repetition 2: `a962a2069d773c2c`

## Setup correction and retained attempts

An early integration check found that an empty session tool list did not remove the tools stored on the shared ElevenLabs harness agent. Nine affected setup attempts were invalidated, regardless of whether they completed. Their original results and available audio are retained in setup-attempts/ and referenced in previous_attempts.

A separate ElevenLabs agent was created and verified to have no tools. It uses the existing configured gemini-2.5-flash LLM and George voice; the shared harness agent was left unchanged. All 24 scored ElevenLabs cells were recorded with the separate agent. Four other-provider sessions interrupted by the setup correction were resumed; their previous attempt markers are retained. No prompts or scenario facts were changed during scoring.

## Timing and interpretation

The first two connectivity recordings ran sequentially (execution_concurrency=1); subsequent recordings ran with up to five providers concurrently, with only one session per provider. This can introduce local scheduling/network contention, so timings should not be treated as isolated provider service latency.

First-audio time measures provider PCM arrival after the final supplied caller frame; the exported audio preserves gaps on a simulated playback timeline. The original transport metadata includes shared adapter defaults; configuration.runtime_limits records the actual runner limits (45-second turn timeout, 30-second response audio limit, no additional trailing silence in the caller cache). Actual provider VAD silence pumps are implemented by the Nova and ElevenLabs adapters.

Timing medians in report.md include only complete conversations; read them with failure counts. This run does not independently verify accuracy, live interruptions, natural endpointing across providers, or recognition of vocal emotion. Human reviewers should rate the anonymised full exchanges. No automated read-aloud judge was reused for these conversations.

Frozen plan SHA-256: `0e677fb5d8fd20d8ca6a59ef63d7bd7617ec4b4105c559d92ffc483ad65854ba`.
