# Accent panel with ElevenLabs caller voices — 3 November 2026: run notes

Five bake-offs, each 6 providers × 6 tasks × 3 repetitions = 108 cells, `standardised-v3`, judges off, OpenAI ASR:
`control-default` (default caller voice, OpenAI speech) and four ElevenLabs library voices selected with `--caller-voice-profile`
(`el_scottish_male` Graeme, `el_irish_female` Jordan, `el_yorkshire_male` Ashley, `el_indian_english_female` Vanishree; model
`eleven_multilingual_v2`, `pcm_24000`). Tasks: balance, transaction details, recent transactions, handoff, `selfcorrect-list-limit-001`,
`commit-cancel-confirmed-001`. The caller text is the scenario script verbatim; lines were synthesised once per voice and cached so every
provider heard the same recording.

**Caveats.** Accent tags are the ElevenLabs library's labels, unverified. The control uses a different speech engine, so default-versus-voice
differences are confounded with the engine. Three repeats per cell.

**Pause.** The Irish batch paused once with `PROVIDER_QUOTA_EXCEEDED` on the ElevenLabs agent (credits). It was resumed after a top-up with
`--resume` and a fresh pre-flight; the quota-failed cell was retried and the batch completed with 108 cells.

Rebuild the page with `scripts/build_accent_elevenlabs.py`.
