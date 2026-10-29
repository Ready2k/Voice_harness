# Phase 6 S2: transport study design note (29 October 2026)

Stage S2 of `PHASE6_SPEC.md`. Design only: nothing is built, no provider is called. Scope follows `PHASE6_NOTES_transport_survey.md`:
candidate providers are OpenAI (two configs) and ElevenLabs. Library names are candidates, not decisions.

## 1. Where the media leg terminates

The harness terminates the provider's WebRTC connection inside the adapter, as a second transport behind the same adapter
interface. Nothing downstream of the adapter changes.

```
caller clip -> harness pacer -> [adapter: WebSocket | WebRTC peer] <-> provider
provider audio -> [adapter] -> adapter.native_event -> approval buffer -> ASR/assessment -> playback gate -> browser
```

- A new adapter-side transport (for example `adapters/webrtc_peer.py`) owns the peer connection, the outbound track, the
  inbound track and the events data channel. Adapters choose a transport at `open_session` from a binding field.
- The browser leg is untouched: no WebRTC to the browser, no change to `playback.py` or `apps/web/` (spec out of scope).
  Platform rule "no WebRTC track may bypass the gate" holds because the peer only ever delivers decoded PCM to the adapter.
- Transport is a run-binding field (`harness_transport.media_transport`: `websocket` or `webrtc`, plus codec, sample rate,
  jitter-buffer setting). Study runs carry `study = "transport"`.

## 2. How audio reaches the approval buffer

1. Inbound RTP is decoded to PCM by the peer (Opus is likely; the codec is negotiated by SDP and recorded, not assumed).
2. PCM is resampled to the harness's common 24 kHz output and cut into the same units the WebSocket path produces.
3. The adapter emits the same `adapter.native_event` records (with `audio_bytes`) that `bakeoff_metrics.is_audio` already
   recognises, then the existing approval-unit code takes over. The approval buffer cannot tell the transports apart.

Failure rule is unchanged: if evidence cannot be written, audio is not released. A peer that drops or stalls surfaces as the
existing typed timeout/backpressure outcomes, never as a silent skip.

## 3. One measurement basis for both transports

Today first-audio latency is `first audio-bearing native_event.received_ms - speech_ended_ms` on one process clock
(`bakeoff_metrics.speech_anchor`, `bakeoff.py`). The study keeps exactly that definition and adds three rules so the two
transports cannot diverge.

**3.1 One function, one clock.** Both arms compute latency through a single shared function (`transport_study.first_audio_ms`)
from the same two fields. There is no per-transport latency code. Both stamps use the harness process monotonic clock.
Provider timestamps are never used, so no cross-host clock sync is involved and none of the clock-sync caveats apply.

**3.2 Anchor.** `speech_ended_ms` is stamped by the harness when the last caller speech frame is handed to the adapter
send call, for both transports, same pacer, same clip bytes. Encode and packetisation delay on WebRTC (Opus framing, RTP) is
therefore inside the measurement. That is intended: it is part of what a caller on that transport experiences. It is
disclosed, not hidden.

**3.3 First audio is content, not packet arrival.** A WebRTC audio track can carry continuous RTP (silence or comfort noise)
before the model speaks, so "first packet" would be stamped early and make WebRTC look artificially fast. The study defines
first audio as the first decoded 20 ms frame whose energy exceeds a fixed threshold, applied by the same detector to both
transports (on the WebSocket arm, to the PCM in the first audio event). This is a study-only definition. The bake-off metric
is not changed, and the two definitions are not compared across studies.

**3.4 Arrival stamp point.** The stamp is taken where the harness first holds the bytes for that frame, and two stamps are
recorded on both arms:
- `arrival_ms`: socket read (WebSocket) or RTP packet receipt before the jitter buffer (WebRTC);
- `playout_ms`: after decode and jitter-buffer release (WebRTC) or after message parse (WebSocket).

The primary result uses `playout_ms`, the moment a decoded frame is available to be released, which is the closest match to
what a listener would hear and is identical in meaning on both arms. `arrival_ms` is reported alongside so the jitter-buffer
contribution is visible rather than absorbed.

**3.5 Matched turn control.** Both arms use the same `turn_commit` mode and the same `commit_delay_ms`. If a provider's
WebRTC session cannot use the mode the WebSocket arm uses (for example explicit commit), the pair is dropped for that
provider, not mixed, because the endpointing delay would then differ by transport. This is a stage-gate check in S3.

**3.6 Content equivalence.** Caller audio goes through Opus on WebRTC but not on WebSocket. The study records independent ASR
of what each arm received back and flags turns where the provider's response differs in kind (for example a tool call on one
arm only). First-audio time on mismatched turns is excluded from the paired analysis and counted in the report.

## 4. Clock and jitter-buffer treatment

- Single host, single process clock; nothing to synchronise. Network delay between harness and provider is real and is the
  quantity under study.
- The jitter-buffer is set to the peer library's minimum or a fixed value and the setting is recorded in the binding. Its
  default behaviour in a chosen library is not assumed here; S3 must read it from that library's documentation and verify it
  with the injected network model. A larger buffer would add latency only on WebRTC, so the setting is part of the result.
- Paired runs are interleaved (WebSocket, WebRTC, WebSocket, ...) with randomised order per scenario so drift in provider load
  or network over the session does not align with one arm.
- Warm-up: the first turn of each session is reported separately (existing `first_turn` / `later_turns` split), since connection
  setup differs by transport.

## 5. Statistics

- Unit: the paired difference (WebRTC minus WebSocket) per scenario-turn, same caller audio.
- Report median paired difference with a bootstrap confidence interval resampled by scenario (turns within a scenario are not
  independent), and the count of excluded turns. No ranking, no significance language for a single sample.
- One host and network, so the report states the result as a lower bound for real callers (spec open question 2).

## 6. Isolation from the bake-off (AC-P6-5)

Study runs are tagged `study = "transport"` at creation. Bake-off scorecard, head-to-head, coverage and benchmark queries
exclude tagged runs by default, and a test asserts that a tagged run never appears in each of those outputs. Study results are
stored and rendered by a separate report path.

## 7. Tests the design requires (for S3, AC-P6-2 and AC-P6-3)

1. Both arms call the same latency function (a test that patches it and fails if either arm bypasses it).
2. The same recorded events produce the same latency regardless of the `media_transport` label.
3. Mutation checks: shifting `speech_ended_ms` or the first-audio stamp by a known amount on one arm only changes the paired
   difference by exactly that amount; a swapped basis makes a test fail.
4. Silence-before-speech on the WebRTC arm does not move first audio.
5. A planted 40 ms transport difference is recovered within tolerance; a null run reports no difference.
6. Mismatched `turn_commit` between arms is rejected.
7. A tagged study run never reaches scorecard, coverage or head-to-head output.

## 8. Risks and open questions

- A server-side WebRTC peer for OpenAI and ElevenLabs is undocumented (survey). If S3 cannot complete either handshake, that
  provider leaves the study.
- ElevenLabs WebRTC fixes 48 kHz PCM (survey), so resampling differs between arms for that provider; disclose it.
- Opus on the caller leg could alter ASR and so response content; handled by 3.6, not eliminated.
- A pure-Python peer library may add its own processing latency on the receive path. S3's loopback rig with injected delay
  quantifies the peer's own overhead and is subtracted or reported, not ignored.
- S4 budget is for the operator to set after reviewing the survey and existing per-run cost exports.
