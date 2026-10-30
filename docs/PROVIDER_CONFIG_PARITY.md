# Provider configuration parity

Policy and disclosure for settings that differ between providers in a bake-off (bias review 2026-10-27, finding set E).
Every live run records these settings in its binding (`run.live_binding.provider`), and the bake-off report lists them
under `limits.provider_disclosures` (table "Provider configuration", caveats), so an X-over-Y statement can be read
against them. `≠` in the report marks a setting that is not equal across the compared providers.

## Policy

* **Sampling: `provider_default`.** The harness sends no temperature, top-p or similar override to any provider. A
  common sampling profile may be added later only if every provider in a bake-off supports it; none is defined yet.
  Recorded as `sampling` (`policy`, `overrides_sent`, `output_limits`, `conforms`).
* **Output voices target British female** (native: Nova `amy`, ElevenLabs Amelia, Gemini Aoede + `en-GB`; OpenAI `marin` and xAI `ara` are female but have no British option, so the accent is not guaranteed). Recorded as `output_voice`.
* **Independent ASR is unchanged** (OpenAI `gpt-transcribe`). Recorded as `asr` with `relation`
  `SAME_VENDOR_ASR` / `INDEPENDENT_VENDOR_ASR` / `NO_LIVE_ASR`.
* Turn control, commit delay, input rate, truncation and confirmation delivery were already recorded
  (`harness_transport`, `confirmation_delivery`; see `PROVIDER_TRANSPORT_REVIEW_2026-10-20.md`).

## Per provider (fixed synthetic caller)

| Setting | OpenAI Realtime | GPT-Live 1 | Nova Sonic | xAI Grok Voice | Gemini Live | ElevenLabs Agents | Equal? / disclosed as |
|---|---|---|---|---|---|---|---|
| Turn control | explicit commit | provider VAD | provider VAD (endpointing MEDIUM) | explicit commit | explicit commit | provider VAD | **No**: `turn_commit`, latency comparability |
| Caller turn-taking | waits for reply playback + 300 ms | same | same | same | same | same | Yes (`provider-transport-v3`): `caller_turn_gap_ms`, `input.caller_audio.caller_waited_ms`; stale provider interruptions ignored and recorded (`interruption.stale_ignored`) |
| Commit delay | 500 ms | n/a | n/a | 500 ms | 500 ms | n/a | **No**: VAD providers hear 1.2 s trailing silence and pay their own endpointing; `commit_delay_ms` |
| Provider input rate | 24 kHz | 24 kHz | 16 kHz | 24 kHz | 16 kHz | 24 kHz | **No**: `provider_input_rate` |
| Sampling sent | none | none | none (`maxTokens` 1024 output ceiling) | none | none | none per session; stored agent: see below | Yes after this change; `sampling`, `output_limits` ≠ |
| Output voice | marin | marin | amy (en-GB) | ara | Aoede, en-GB | Amelia (en-GB) | **No**: `output_voice`, report caveat |
| LLM | the speech model | speech model + delegated backend (`backend_model_id`) | the speech model | the speech model | the speech model | agent LLM (default `gemini-3.5-flash`) | **No**: `agent_llm`, `agent_llm_vendor`; report names the LLM |
| Client truncation | yes | no | no | yes | no | no | **No**: `client_truncation` |
| Confirmation delivery | context only (user message) | context only (appended instructions) | interactive USER turn | context only (user message) | context only (user turn) | context only (contextual update) | **No**: `confirmation_delivery`, `context_channel` |
| ASR vs tested vendor | **same vendor** | **same vendor** | independent | independent | independent | independent (same if agent LLM is OpenAI) | **No**: `asr.relation`, report limitation |
| Prompt | advanced-v1: + pacing lines | every profile: + adapter delegation wrapper to the voice model (`[adapter]` delta, `target_prompt.delivered`); advanced-v1: + delegation line | + "direct answer first" | restructured sections | + concern line | section headings | **No**: `prompt_delta` per provider; standardised/conversational profile text is identical, GPT-Live adds its wrapper |

## Decisions

* **Nova `maxTokens` 1024 is kept.** Every working Nova `sessionStart` we have (this adapter, the probe, the sibling
  Voice_S2S project at 1024-2048) sends `inferenceConfiguration.maxTokens`; whether Bedrock accepts its omission is
  unverified, so it stays. It is a per-response output ceiling, not a sampling setting: 1024 tokens is several times
  the text of the longest reply the harness accepts (20 s of speech, `LIVE_LIMITS`, about 60-70 words). `topP` 0.9 and
  `temperature` 0.2 are removed. Not yet verified live: that Nova accepts `inferenceConfiguration` with only
  `maxTokens` (one smoke run needed).
* **ElevenLabs temperature 0 is removed from `agent_body`.** The stored agent keeps temperature 0 until
  `python -m voice_harness.adapters.elevenlabs_agent setup` is rerun (it writes to ElevenLabs). The state file now
  records `config_fingerprint` and `temperature`; each run records `agent_config_current` and
  `sampling.stored_agent_temperature`, and an agent provisioned before this change is reported as outside the policy
  (`conforms: false`) rather than refused. `ELEVENLABS_AGENT_ID` pointing at another agent records the LLM and
  temperature as `unknown`.
* **Gemini first audio is exact on new ledgers.** Native events record `audio_bytes` (decoded output audio, from the
  event's shape: Gemini `serverContent.modelTurn.parts[].inlineData`, OpenAI/xAI audio deltas, Nova `audioOutput`,
  ElevenLabs `audio`). Older Gemini ledgers keep the 2000-byte payload heuristic and stay flagged `estimated`.
