# GPT-Live 1 / Marin — matched-script voice library

Recorded 28 October 2026 using `openai-live`: voice model `gpt-live-1`, voice `marin`, managed Responses backend `gpt-6-luna`. These are six fresh single-prompt sessions, using the same six banking scripts as the 25 October matched-script library. They are representative reading samples, not excerpts or a re-run of the 720-cell bake-off.

Six clean WAVs and telephone-quality derivatives of the first two prompts are retained (eight files). Telephone versions are processed from those same outputs with the existing narrowband/noise transform, not additional provider calls. The recordings have not been regenerated for publication. Capture succeeded for all six prompts, with no failed cells in this published set.

## Reproduce

```sh
.venv/bin/python scripts/voice_naturalness.py render \
  --provider openai-live --out .local/gpt-live-voice-library-NEW-DATE
.venv/bin/python scripts/voice_naturalness.py verify \
  --out .local/gpt-live-voice-library-NEW-DATE
```

`key.json` maps each clip to its script, condition, duration, configuration, session receipt, provider transcript, exact caller text, delivered voice instructions and their SHA-256. `verify.json` contains independent ASR transcripts of the six clean outputs. The generated library manifest also records WAV SHA-256 hashes. The audio is 24 kHz, mono PCM16.

The GPT-Live adapter synthesizes the caller request and sends it as audio. An explicit “Repeat this aloud, word for word, and nothing else” request precedes each script; it is excluded from the expected assistant response. The existing GPT-Live delegation wrapper remains applied and is recorded in the metadata. The six scripts, read-aloud instructions and configured speech path are shared in intent with the old library, but the recording dates and input path differ; this is not a controlled quality-ranking experiment. No subjective ratings or automatic quality judging were run.

An earlier preflight without the explicit repetition request timed out and its batch was stopped. A one-prompt diagnostic with an explicit repetition request succeeded. Those diagnostic attempts are not included in this sample set. The six published scripts were then captured in a fresh batch. Independent transcripts should be read alongside the WAVs; punctuation and number formatting can differ without changing the spoken script.

## Listening

Open the [voice library](../../voice-library.html) for side-by-side comparison, or [rate.html](rate.html) for anonymous local rating. These are identified samples: the library and key reveal the provider. For natural call excerpts from the earlier single-model evaluation, use the [27 October listening report](../../reports/gpt-live-1-listening-2026-10-27.html).
