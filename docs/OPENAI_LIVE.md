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

## Prompts

The backend model gets the prompt profile's text unchanged. The voice model gets
the same text after the adapter's delegation wrapper `gpt-live-delegation-v1`
(`target_prompts.LIVE_DELEGATION_WRAPPER`): "Speak natural, concise British
English. Delegate banking questions and actions to the backend. Never invent
results or claim success before the backend confirms it." Each run's
`target_prompt.delivered` records both texts, their SHA-256 and model ids, and
`target_prompts.delta` lists the wrapper as an `[adapter]` prompt delta on every
profile, so standardised and conversational bake-offs disclose it too. Its
first sentence duplicates STANDARDISED_V2's British-English line. The wording is
kept as evaluated. LEAK-01 checks speech against the voice model's text.

Orchestrator context (`send_text(respond=False)`, e.g. the cancellation
confirmation) is appended to both models' instructions (`context_channel:
instructions_append`). Live has no runtime user message, so this is
instruction-level authority. Other context-only providers get a user-level
message or contextual update. Bake-off reports disclose the difference.

## Reply boundaries

Live streams PCM16 at 24 kHz continuously, including silence. There is no native
reply-complete or history-truncate event. The adapter groups output into replies
and ends a reply (`TurnEnd`) only when all of these hold:

* at least `REPLY_SILENCE_MS` = 800 ms of low-energy output (PCM RMS ≤ 100) since the last speech;
* no delegation or backend response open (`session.delegation.created`/`response.created` until its terminal event)
  and no tool result pending;
* `CALLER_GRACE_S` = 2.5 s after the latest audible caller audio;
* if a backend response has completed, its result has started being voiced (a speech onset after the completion),
  or `RESULT_VOICE_GRACE_S` = 1.5 s has passed since the completion.

The rules are derived from the 27 Oct 2026 evaluation at `6d9769f`/`11fbaf4`: `.local/gpt-live-eval-6d9769f`, ledger
`development.sqlite` (opened read-only) and `diagnostic-trace.jsonl`. The trace holds the arrival times of transcript,
delegation and nested response events in the ledger's monotonic clock domain. There are 41 sessions, 56 caller turns
with a reply and 100 ms output chunks. Speech chunks are the ledger's `harness.pcm_speech_detected` markers:

| Interval | n | p50 | p90 | p95 | max |
|---|---|---|---|---|---|
| Caller speech end → first reply speech | 56 | 1474 ms | 2476 ms | 2716 ms | 4164 ms |
| Caller speech end → `session.delegation.created` (no speech preceded it in 29/29) | 29 | 1003 ms | 1803 ms | 2189 ms | 2200 ms |
| Acknowledgement end → delegated result speech | 27 | 3737 ms | 8361 ms | 9104 ms | 11502 ms |
| Final backend `response.completed` → result speech | 28 | 779 ms | 906 ms | 948 ms | 1099 ms |
| Pause between speech chunks, no backend work open (p99 600 ms) | 77 | 300 ms | 400 ms | 500 ms | 700 ms |
| Pause spanning backend work | 45 | 1200 ms | 6200 ms | 8500 ms | 11200 ms |

Long ack-to-result gaps are spanned by backend state, not by a timer. The silence
window has to exceed only the longest pause with no backend work open: 700 ms, so
800 ms. Replaying the ledger through each rule gives the results below. Dead time
runs from the last speech chunk to `TurnEnd`. Replies whose next caller turn
started first are censored, which leaves one (the timed-out interruption run).

| Rule | Replies split | Dead time mean | p50 | p90 | max |
|---|---|---|---|---|---|
| Before: 900 ms, +3 s after caller speech or any backend completion | 0 | 1001 ms | 948 ms | 1038 ms | 3300 ms |
| After: 800 ms, caller grace 2.5 s, result voiced or 1.5 s | 0 | 866 ms | 845 ms | 915 ms | 1800 ms |
| 700 ms silence (rejected) | 1 | 766 ms | 742 ms | 812 ms | 1800 ms |
| 600 ms silence (rejected) | 4 | 666 ms | 643 ms | 689 ms | 1800 ms |

The recorded runs themselves showed mean 1021 ms and p50 951 ms (n=54), which
matches the replay of the old rule. The change saves about 135 ms per turn on
average and 1.5 s in the worst case. The margin over the longest observed natural
pause is one 100 ms chunk, from one 40-scenario sample. Replies that split in
future runs (two units for one answer) mean `REPLY_SILENCE_MS` must go up. Dead
time does not count towards first-audio latency, which is measured from the end
of caller speech to the first audible chunk. It does count towards turn duration
and the start of the next caller turn. Providers with a reply-complete event have
almost none.

Output silence counts towards a reply only while the reply is open. Each pause,
including the closing one, is passed on in real time up to 800 ms, and the rest
is dropped. A backend wait therefore does not fill the unit, or its 20 s
`response_audio_seconds` cap, with silence that packet-streaming providers never
send. Natural pauses keep their real-time position, so the runner's model of
reply playback (when the next caller turn may start) is unchanged. Recorded units reached 17.0 s, including up to 11.2 s of wait silence. The
longest speech in any reply was 11.7 s.

`interrupt()` still raises `CapabilityUnsupported` because Live has no truncate
operation. It closes the open reply first, though, so the caller's cut-in ends that
reply, as a provider turn end would. Live may keep speaking the cut-off reply;
that speech starts a new reply. Before this change, the interrupted acknowledgement
and the later answer merged into one reply. The runner treated its single turn end
as the interrupted reply's, and the interruption scenario timed out (`TURN_TIMEOUT`).

## Input pacing

The harness streams 20 ms caller frames in real time. The adapter's clock sends them on to Live and keeps the
session clock running with silence between turns. On the evaluation ledger a caller clip took 519 ms longer to deliver
than its audio duration (median; p90 706 ms, max 1325 ms, n=56), about 90 ms per second of audio. Meanwhile the
input-transcript session clock kept to wall time (median 0 ms/s drift). The harness fed frames about 9% slower than
real time, and the old clock (`sleep(20 ms)` after every send, a zero frame whenever its queue was empty) filled the
holes with 20 ms of digital silence: about one frame in eleven inside caller speech. The clock now paces against one
monotonic deadline schedule, so per-send overhead does not accumulate. It sends caller frames as the harness supplies
them, and waits up to `INPUT_JITTER_S` = 100 ms for the next caller frame before resuming silence, so silence is
never spliced into speech. `request_turn()` emits `harness.caller_audio_flushed` when the last queued caller frame has
been sent. Its `received_ms` against the caller turn's `speech_ended_ms + 1200` (the trailing silence) is the
provider-side delivery lag.

## First audio

Latency uses the adapter's `harness.pcm_speech_detected` markers, which flag output chunks with PCM16 RMS above 100
(`audio_activity.RMS_THRESHOLD_PCM16`), rather than the continuous raw stream. The same definition applies to every
provider once native events carry the `audible` flag (`AdapterNativeEvent.audible`, from `audio_activity.audible_payload`).
`bakeoff._is_audio` uses that flag whenever it is present. Without it, packet-streaming providers are timed at their
first audio packet, which can be near-silent and slightly favours them.

## Status and limitations

These boundaries are diagnostic heuristics, not verified provider turn boundaries. Runs carry `diagnostic_unverified`
capability status. Still to be evaluated: full-duplex overlap, long pauses, delayed transcripts, and a reply that never
comes. A missing reply waits for the 45 s turn timeout, because there is no provider "no response" event.
Interruption history truncation is unsupported. Spoken transcripts and backend completions are tracked independently.
Final voice usage comes from a graceful `session.closed`; missing finalization is reported rather than treated as
complete. Voice is billed by session duration, which includes harness waits (turn boundaries, assessment holds), so its
cost is not comparable like-for-like with token-billed providers. The delegated backend `gpt-6-luna` is also the default
OpenAI judge model. `TESTED_MODELS["openai-live"]` lists both models as `components`, and a judge matching any component
is ineligible (`JUDGE_IS_TESTED_MODEL`).

Run the paid synthetic audio/tool smoke (loads `.env`, prints only event counts):

```sh
PYTHONPATH=src .venv/bin/python scripts/probe/openai_live_smoke.py
```

Implementation references:
[Live](https://developers.openai.com/api/docs/guides/live),
[WebSockets](https://developers.openai.com/api/docs/guides/voice-websockets),
[delegation](https://developers.openai.com/api/docs/guides/live-delegation),
[sessions](https://developers.openai.com/api/docs/guides/live-conversations).
