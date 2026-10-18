# Caller voice synthesiser (portable live runs)

Caller speech for live runs is now pluggable (`src/voice_harness/caller_voice.py`), chosen by `VOICE_HARNESS_CALLER_VOICE`:

- unset or `macos`: macOS `say`/`afconvert` (unchanged default); unavailable elsewhere -> `NO_CALLER_VOICE_SYNTH`.
- `openai_tts`: opt-in. POSTs to `https://api.openai.com/v1/audio/speech` with `response_format=pcm` (24 kHz s16le mono, so no resampling) using httpx. Requires `VOICE_HARNESS_LIVE_ASR=openai` and `OPENAI_API_KEY`, otherwise `CALLER_VOICE_TTS_NOT_CONFIGURED`. Optional `VOICE_HARNESS_CALLER_TTS_MODEL` (default `gpt-4o-mini-tts`) and `VOICE_HARNESS_CALLER_TTS_VOICE` (default `alloy`).
- any other value: `CALLER_VOICE_UNKNOWN_BACKEND`.

`live_providers.availability` surfaces these reason codes. `synth_caller_pcm` keeps its signature and padding and dispatches on the backend; TTS errors raise `CallerVoiceUnavailable` (a `RuntimeError`) with a code (`CALLER_VOICE_TTS_HTTP_<n>`, `..._TRANSPORT_ERROR`, `..._EMPTY`).

Recording: `run.live_binding.provider.caller_voice` = `{backend, model_id, voice, sample_rate}` (identifiers only). `provider` is a free-form field so the event contract is unchanged.

Caveat: TTS caller audio is a different voice from macOS `say`, so ASR/interruption results are not directly comparable across backends; the recorded backend lets exports say which was used. TTS calls are billed by OpenAI and are not yet in the cost recorder.
