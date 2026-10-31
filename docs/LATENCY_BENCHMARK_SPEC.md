# Latency benchmark: specification (draft, 31 October 2026)

Status: first benchmark run on 31 October 2026 (four sessions, 497 timed turns, six providers; report `reports/latency-benchmark-2026-10-31.html`).
The quality bake-offs are unchanged.

What the first run did differently from the plan below:
- Four sessions (11:19Z tool scenarios, 11:57Z plain scenarios, 14:20Z and 17:20Z both), not three, with plain and tool turns reported separately.
  Providers ran concurrently, not interleaved in random order.
- Plain-turn samples are 20 to 40 per provider, short of the 30-per-kind plan for some providers.
- Calibration (`scripts/vad_calibration.py`, three trials per setting) set OpenAI Realtime and xAI to 700 ms silence and kept Gemini at 600 ms.
  No mid-sentence-pause control was added to the benchmark itself, and one location only.
- Gemini's `voiceActivity` payload is `{type: ACTIVITY_START | ACTIVITY_END, audioOffset}` (probed live, 31 October). The ledger now records the type (`voiceActivity.ACTIVITY_END`), so the split works for new runs; the four pooled sessions predate it. A fifth Gemini-plus-OpenAI-Realtime session (`.local/latency-session-4-gemini-split-2026-10-31`, 54 cells, same settings) supplies Gemini's split: wait for end of speech about 1.06 s on plain and 1.05 s on tool turns, reply start about 0.10 s and 0.76 s. It is shown in the report marked † and is not pooled into the totals. Its medians reproduce the pooled ones (Gemini plain 1,159 against 1,166 ms; tool 1,811 against 1,810). Nova, OpenAI Realtime and xAI have the split from the pooled sessions.
- Latency is anchored on the end of the caller's voice (synthesiser tail silence trimmed), so it is not comparable with earlier bake-off latency.
- Judges and independent ASR were off.

The text below is the original proposal.

## Why a separate benchmark

The bake-off measures first audio with explicit-commit providers (Gemini, xAI, OpenAI Realtime) told the turn ended after a common
emulated 500 ms, while Nova and ElevenLabs detect the end of speech themselves and GPT-Live uses a tuned heuristic. The report
discloses this as `mixed_turn_control` and ranks without latency. At 300, 500 and 700 ms the fast end of the order changes
(OpenAI Realtime is fastest at 300 ms, Nova from 500 ms); only the slow end is stable (ElevenLabs about 2.0 s, GPT-Live behind
Nova). That is honest, but it does not answer the buyer's question: how long does a caller wait for the reply?

## What is measured

1. **Caller-felt latency, L**: from the end of the caller's speech to the first audible reply audio, each provider on its own
   server-side turn detection. Nothing emulated.
2. **Split, where the provider exposes it**: L = E + R. E is the endpointing wait (end of speech to the provider's own end-of-speech
   signal). R is the response time (that signal to first audio). E answers "which feels fastest to a caller"; R answers "which
   engine is fastest".
3. **Turn types, reported separately**: plain answer, tool-using answer (adds the tool round trip), and session start (connect to
   ready). A pooled figure hides that tool turns cost more.
4. **Safety of the setting**: false endpoints (the provider answered while the caller was still speaking) and cut-offs are counted
   from the existing interruption and barge-in records. A setting that is fast because it interrupts the caller is not a result.

## End-of-speech signals already in the ledgers (29/30 Oct standard run)

| Provider | Signal recorded | Split available |
|---|---|---|
| Nova Sonic | `userSpeechEnd` | yes |
| xAI Grok Voice | `input_audio_buffer.speech_stopped` | yes |
| Gemini Live | `voiceActivity` | probably (confirm it carries an end) |
| OpenAI Realtime | none in explicit-commit mode; `speech_stopped` exists in server-VAD mode | yes in VAD mode |
| ElevenLabs | `user_transcript` only (arrives after the fact) | no: L only |
| GPT-Live | none recorded | no: L only |

Where there is no signal, only L is reported, and the report says so.

## Configuration policy

Each provider runs on its own turn detection at the lowest-latency setting its vendor documents that does not raise false
endpoints or cut-offs in a short calibration pass. Settings are recorded in the run binding and printed in the report.

- OpenAI Realtime, xAI: `server_vad` (the adapters already accept `configure_turn_detection`); silence duration and threshold are
  the only knobs varied.
- Gemini Live: `automaticActivityDetection` (already passed through `activity_detection`).
- Nova Sonic: `endpointingSensitivity` (HIGH, MEDIUM, LOW; the harness default is MEDIUM).
- ElevenLabs, GPT-Live: fixed turn detection; measured as they come.

A second, labelled table keeps the current method (common emulated 500 ms commit) so the old and new figures can be compared.

## Sampling

- At least 30 timed turns per provider per turn type, not 3 repetitions. Interleave providers in a randomised order rather than
  running all six at once, so they do not compete for one machine and network.
- Repeat in at least three sessions across different times of day.
- Report median, p90 and p95 with bootstrap intervals. Two providers are called different only when their intervals separate.
- Use short, fixed caller clips (a one-sentence question, a balance request after verification) so audio length does not vary.
- Disclose the single location and network. Providers hosted nearby are favoured, and the report must say so.
- Judges and independent ASR are off: latency needs neither, which also removes most of the cost.

## Presentation

Show latency against quality as two axes (a scatter of composite against median L, with intervals), plus the table of L, E and R.
Do not fold latency into the composite for this view. The 0.15 responsiveness weight stays in the bake-off composite, which
already ranks without it when comparability is `mixed_turn_control`.

## Cost and effort

Estimate only, from the 29/30 Oct per-run cost (median per run: Gemini $0.004, Nova $0.005, GPT-Live $0.017, OpenAI Realtime $0.020,
xAI $0.033; ElevenLabs not shown). About 70 short sessions per provider across three sessions comes to roughly $5 to $10 in total with
judges off. Work: a latency-only runner mode (no scoring), the per-provider settings, the E/R split in `bakeoff_metrics.py`, a report
section, tests. No change to existing results.

## Order of work

1. No cost: confirm Gemini's `voiceActivity` carries an end-of-speech marker; add E/R extraction to the metrics for the providers with
   a signal, and test it against the existing ledgers.
2. Small spend: a calibration pass per provider to pick the fastest setting with no false endpoints.
3. Main run, then a report section and a sentence on the business page.

## Open questions

- Should the benchmark add a no-speech-gap control (a clip with a deliberate mid-sentence pause) to measure false endpointing
  directly? It would make "safe" measurable rather than inferred, at the price of a few more clips.
- Is one location enough, or do you want a second region for the providers that offer one?
