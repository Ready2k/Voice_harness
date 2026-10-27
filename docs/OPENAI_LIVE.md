# GPT-Live 1

Select **OpenAI GPT-Live 1** (`openai-live`) in manual sessions, scenario runs,
bake-offs, or the telephone demo. Existing OpenAI Realtime remains a separate
provider. Uses the server's `OPENAI_API_KEY`; strict assessment also requires
`VOICE_HARNESS_LIVE_ASR=openai`. Typed caller input uses the configured caller
speech synthesizer, because Live has no runtime user-text message command.

The voice model is `gpt-live-1`, voice `marin`, with managed Responses delegation
to `gpt-6-luna`. Custom tools still pass through the harness gateway and its
verification and confirmation checks. Voice duration and delegated backend token
usage are recorded separately; costs remain unknown until matching tariffs are
configured. The adapter configuration accepts `backend_model_id` for controlled
backend experiments.

Live streams PCM16 at 24 kHz continuously, including silence. There is no native
reply-complete or history-truncate event. The adapter groups replies after 900 ms
of low-energy output PCM, while no backend work or tool results are pending.
These boundaries are diagnostic heuristics, not verified provider turn boundaries.
Runs carry `diagnostic_unverified` capability status. Latency uses the adapter’s
`harness.pcm_speech_detected` markers (PCM RMS above 100), rather than the
continuous raw stream; this is a labelled speech-activity estimate. Full-duplex overlap, long
pauses, interruptions and delayed transcripts still need evaluation; interruption
history truncation is explicitly unsupported. Spoken transcripts and backend
completions are tracked independently. Final voice usage comes from a graceful
`session.closed`; missing finalization is reported rather than treated as complete.

Run the paid synthetic audio/tool smoke (loads `.env`, prints only event counts):

```sh
PYTHONPATH=src .venv/bin/python scripts/probe/openai_live_smoke.py
```

Implementation references:
[Live](https://developers.openai.com/api/docs/guides/live),
[WebSockets](https://developers.openai.com/api/docs/guides/voice-websockets),
[delegation](https://developers.openai.com/api/docs/guides/live-delegation),
[sessions](https://developers.openai.com/api/docs/guides/live-conversations).
