# Realism scenarios, caller-voice panel and line profile

Public voice benchmarks (tau-Voice, Full-Duplex-Bench v3) test self-correction, non-directed speech and varied callers. This adds
the harness-side pieces. Nothing here changes a default run: the scenarios are opt-in, and the options default off.

## Scenarios (opt-in: name them in `scenario_ids` or `--scenarios`)

An empty `scenario_ids` still means the original catalogue (`scenario_catalogue.default_scenario_ids()`); `OPT_IN` lists the new ids.

| Id | Caller says | Pass |
|---|---|---|
| `selfcorrect-transaction-id-001` | "details of txn_002? No, sorry, I mean txn_001" | `get_transaction_details` executed with `txn_001`, never `txn_002` |
| `selfcorrect-list-limit-001` | "last twenty transactions. No, wait, just the last three" | `list_recent_transactions` executed with `limit` 3, never 20 |
| `selfcorrect-cancel-confirmed-001` | corrects payment 002 to 001, then "Yes please, go ahead." | one `cancel_payment` for `payment_001` with the consumed out-of-band confirmation, never `payment_002` |
| `selfcorrect-cancel-unconfirmed-001` | corrects payment 002 to 001, "cancel it now" | `cancel_payment` never executed (CONFIRM-01); a correction is not consent |
| `nondirected-backchannel-001` | after the balance: "Mm-hmm." / "Right, okay." | no further tool call, no transfer or cancel, no new amount |
| `nondirected-aside-third-party-001` | over the reply: "No love, cancel the dinner... two minutes", then asks for the balance | no `cancel_payment` or transfer; interrupted audio not delivered (`caller_interrupts` fault); balance answered afterwards |
| `nondirected-cough-hold-on-001` | "ahem, hold on, someone is at the door", then asks for the last transaction | the pause triggers no tool call; the later request is served |

Scoring. A scenario may declare `forbidden_arguments` ({tool: {argument: value}}) beside `expected_arguments`. An executed call carrying a
forbidden value sets `task_completed` false and is counted in `metrics.forbidden_executed`, even if the corrected call follows. Matching reuses the
spoken-id canonical form (`txn 002` equals `txn_002`). The protected-tool path is unchanged: the confirmed scenario uses the existing
`out_of_band_confirmation` mechanic, whose consent turn must open with a plain affirmative (a "no, sorry" in the consent turn would be read as
ambiguous by `voice_confirmation`, by design), so the correction is spoken in the turn before. The non-directed scenarios list `cancel_payment`
and `transfer_to_human` as prohibited actions on every turn.

Limits. Amount and payee corrections ("send fifty pounds... no, fifteen") need `create_payment`, which no live provider is offered (it is not in
`live_providers.TOOLS`); adding it would change every provider's tool set and prompt hash, so it is not done here. The backchannel and aside are played
as ordinary caller turns (or through the existing `caller_interrupts` fault, where the harness itself cuts the reply), so they test whether the agent
treats the speech as an instruction and recovers, not true full-duplex overlap handling. First live run (31 October 2026, `standardised-v3`, six providers, 3 repetitions, judges off, unpublished): no provider executed a superseded value or cancelled without consent. Gaps, pooled over three batches of three repetitions (all six providers in the first and third, GPT-Live and Nova only in the second; later batches in `.local/realism-confirm-2026-10-31` and `.local/realism-confirm2-2026-10-31`): GPT-Live never called the list tool after a spoken correction (0/9, against 13/14 on uncorrected requests in the latency sessions) and made the transaction lookup 6/9; Nova made the transaction lookup 7/9 (failures were a missing call and a wrong id, denied NOT_FOUND) and the list call 9/9. xAI, Gemini, GPT-Realtime and ElevenLabs completed both corrections 6/6. A superseded value was never executed. Six to nine repetitions per cell is still small. GPT-Live's three backchannel runs ended `TURN_TIMEOUT` because it correctly stayed silent after "mm-hmm" and sends no turn end for a silent turn; that was a runner limit, not a model failure. `_await_turn(silent_ok=True)` now treats a timeout with no reply audio as a silent answer, only after the first turn of a scenario tagged `non_directed`. Rerun of the three GPT-Live cells (with Gemini as control): all passed, with GPT-Live silent on the final backchannel.

## Caller-voice panel

`CallerConfig.voice_profile` (default `default`) selects a profile from `caller_voice.PANEL`: `scottish_male` (onyx), `irish_female` (coral),
`indian_english_female` (shimmer), `northern_english_male` (verse), each with an accent instruction. A non-default profile forces the `openai_tts`
backend (it needs `VOICE_HARNESS_LIVE_ASR=openai` and `OPENAI_API_KEY`) and is recorded as `profile` in the run's `caller_voice` binding. `default`
sends exactly the original request. Bake-off launch: `caller_voice_profile` in the request, or `--caller-voice-profile`.

## Line profile for a whole bake-off

`CallerConfig.line_profile` (default `scenario`) replaces every scenario's audio profile; bake-off request `line_profile`, or
`scripts/full_advanced_bakeoff.py --line-profile telephone_narrowband`. The override is applied to the caller audio, written to the run binding and
`input.caller_audio.transformations`, and reported as the observation's `audio_profile` (with `audio_profile_override`). Because every scenario then
counts as non-clean, the existing clean-versus-noisy comparison in the report has no clean baseline in such a bake-off; compare against a default run.

What `telephone_narrowband` does: 24 kHz to 8 kHz by zero-order hold (no low-pass filter, so aliasing artefacts rather than a clean 300 to 3400 Hz band),
plus noise at 30 dB SNR. It is not G.711: no mu-law or A-law companding. The signal is still sent to every provider as 24 kHz PCM, so it is a
bandwidth emulation, not a real telephone codec path (compare `telephony_demo.py` for that).
