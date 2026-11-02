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

## Follow-up run, 1 November 2026 (five providers, Nova pending)

Unpublished, standardised-v3, judges off, ASR `openai`; data in `.local/followup-2026-10-31/`. GPT-Live, OpenAI Realtime, xAI, Gemini and ElevenLabs only: Nova needs a fresh AWS session and is not yet run.

- **`commit-cancel-confirmed-001` (v3), 3 repeats each:** 15 of 15 completed both expected tools with the correct `payment_001`. Nothing was left out of task completion.
- **`happy-handoff-001` after the runner fix (3 repeats each):** OpenAI Realtime, xAI, Gemini and ElevenLabs called `transfer_to_human` exactly once in all 12 runs (OpenAI Realtime used to call it twice in two of three runs). GPT-Live never transferred: 0 of 3 here and 0 of 6 more in the accent and telephone runs (0 of 9). Its replies offer to help with the account or to "look at transfer options", and it still does not transfer when the caller asks a second time. Genuine behaviour, not a harness fault. The carried-over GPT-Live row in the 29/30 Oct report came from a different prompt and may not reflect this.
- **Accent panel and telephone profile** (4 scenarios, one repeat each, five providers; a default-voice baseline alongside): no degradation under `telephone_narrowband`. Across the four accent voices there were three transaction-lookup misses, none of them reproduced by the other voices: xAI wrong identifier (Irish, Northern English), ElevenLabs no call (Irish), GPT-Live no call (Indian English). With one repeat per cell this is a signal to repeat, not a ranking. GPT-Live also missed `happy-recent-transactions-001` once in the default-voice baseline, so a miss is not specific to accents.
- Runner fix: after a successful `transfer_to_human` the runner does not speak the remaining caller turns (recorded as `stopped_by: session_transferred`). The scenario definition and hash are unchanged.

### Nova and the confirmation probe (1 November 2026)

- **Probe (live, not a ranking):** Nova Sonic has no silent way to receive the out-of-band confirmation. A mid-session SYSTEM text is rejected by Bedrock (connection reset); a non-interactive USER text is accepted but the model does not use it (asked for the word it had been given, it said it had none); an interactive USER text triggers a spoken reply. The `interactive_turn` confirmation delivery therefore stays, with its disclosure.
- **Nova, same scenarios (one provider, small n):** `commit-cancel-confirmed-001` verified identity but never called `cancel_payment` in 3 of 3 runs (the other five providers: 15 of 15 completed). `happy-handoff-001`: transferred 2 of 3. `happy-transaction-details-001`: wrong identifier in both the default-voice and telephone runs. Balance and recent transactions fine. The cause of the missed cancel is not established: it may be the confirmation arriving as a user turn, or Nova's own behaviour; the reply text was not retained.
- **Nova accent panel (one repeat, 4 scenarios, 16 cells, after an OpenAI credit top-up):** 13 of 16 passed. Misses: handoff not called (Scottish, Northern English), transaction lookup not called (Northern English). Nova's own default-voice and telephone runs missed the handoff once and gave a wrong transaction identifier twice, so its misses are not specific to an accent. One repeat each: a signal to repeat, not a ranking.


## Regional context page and recovered response excerpts

[Regional caller understanding](../artifacts/regional-caller-2026-11-01/index.html) presents the four-task accent panel separately from the 144-conversation transcript review. It includes 96 regional cells and 24 default-voice baseline cells across six provider configurations, plus 20 Gemini control cells from the Nova batches, shown separately. Counts use `observation.metrics.task_completed`, not the assurance `outcome`: an assurance pass does not guarantee task completion.

The source is `.local/followup-2026-10-31/`: `baseline-five`, `baseline-nova`, the four `accent-*-five` folders and the four completed `accent-*-nova2` folders. Preflight checks and credit-blocked earlier `accent-*-nova` batches are excluded. Each primary cell has one planned scored repeat; the Gemini Indian-English balance cell timed out and was retried successfully. The table uses final cell observations and discloses both attempts. This is an exploratory synthetic-speaker panel; speaker identity, accent instructions and synthesis backend are confounded, and misses cannot be attributed to accent alone.

The original caller audio was not retained. The exporter recovered 312 assistant response excerpts across 132 of the 140 included runs from saved approval-unit PCM. These are not complete calls or proof of actual playback. Eight runs have no recovered response audio; other runs may have partial coverage. The report retains these cases in its denominators. Latest approval revisions are selected per output event/hash to avoid duplicating pre- and post-ASR units. Raw PCM hashes, byte counts and formats are checked, and FLAC output is decoded and compared byte-for-byte before export.

Rebuild with `python3 scripts/build_regional_context.py` (requires the local evidence databases and the `flac` executable). No model/API calls are made. Public `data.json` contains allowlisted result fields, source batch payload hashes, run IDs, response text and clip hashes; databases, credentials and session metadata remain local. The generated page and clips are self-contained for the existing Pages archive build. The conversation-review and assessment pages link to this context without merging scores.


### Reconstructed callers and future retention

The regional page now also provides 24 **new reconstructed caller clips** (six utterances × four regional profiles). `scripts/reconstruct_regional_callers.py` verifies each script against the original `source_text_sha256`, uses the retained TTS model/voice and the repository's accent instructions, and deduplicates paid speech calls. `caller/reconstruction.json` records generation times, hashes, settings and original-run bindings. These clips are not original recordings or evidence of what a provider heard; the original waveform hashes and historical results remain unchanged. The default-voice baseline has not been reconstructed. No historical assistant response has been regenerated.

For new runs, `RunConfig.retain_audio` defaults to true and the launch/compare forms start checked. Fixed and interactive callers save their prepared PCM before streaming. Received assistant frames are archived after each response-wait cycle, including partial audio on handled timeout, cancellation or provider error; microphone sessions save accepted input frames and received output. Capture-index artifacts identify roles, turns, formats and hashes. The original caller event still distinguishes prepared audio length from streamed length. Generated/received audio is not proof of playback; muted/dropped microphone frames are not captured. An abrupt process/machine failure can still lose the current buffered assistant turn.

Full archives use `expires_at: null` and survive the automatic TTL purge until explicitly removed; the existing bounded disputed/spot-check mechanism remains separate. This increases local disk use. Explicit `retain_audio: false` still opts out of full capture; existing run configurations and artifacts are unchanged. Telephony demos already store their sent/received WAV recordings separately. No new voice-provider comparison calls were made for this change; only the 24 reconstruction speech calls were made.
