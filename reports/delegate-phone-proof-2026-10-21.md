# Delegate telephone transport proof — 21 October 2026

## Finding

Live telephone audio, agent-driven DTMF, reconnection and approved spoken output were exercised against the operator-authorised automated test line. The balance lookup is **incomplete**, because after accepting `770022` the IVR requires an additional sub-test number that the operator has not yet supplied. No balance was obtained or invented.

The user explicitly approved the temporary Cloudflare tunnel, OpenAI realtime/transcription processing and local audio retention before these live tests.

## Strongest attempt

- CallSid: `CAd90dd2c3b989a375b669b3d4193e67c5`.
- From `+441483694454`, to `+441617683858`.
- Twilio final state: `completed`, duration **39 seconds**; API cleanup confirmed.
- **35.9 seconds** of remote audio captured; **5.3 seconds** of approved speech sent.
- Two stream generations bound to the same CallSid.
- DTMF tool selected by the model after the observed “Please enter your AutoTest number” prompt.
- Exact permitted keypad value: `770022`, no terminator.
- Call-update-to-reconnect interval: **2,949.7 ms**. This is a measured control/reconnect interval, not a precise acoustic loss measurement.
- IVR response after reconnection: “Test number 770022. Please enter your sub test number.” This corroborates that the destination accepted the digits; it is stronger evidence than an accepted API update alone.
- Two preapproved “Please repeat the balance and currency” utterances sent; both received uncleared Twilio playback marks. The IVR continued asking for the sub-test number, so these utterances were not an appropriate solution to its keypad prompt. Transport acknowledgement is not proof of physical audibility or comprehension.

Independent post-call transcript:

> Please enter your auto test number. Test number 770022. Please enter your sub test number. Sorry, I didn't get that. Please enter your sub test number. Sorry, I didn't get that. Sorry, retries exceeded. Ending call.

The test ended after the IVR exhausted retries. The implementation now explicitly stops on a missing sub-test prompt and supports a separately supplied, single-use `--subtest-id`. This subsequent behaviour has offline tests but has not yet been exercised live.

## Local evidence

Private, git-ignored directory: `.local/delegate-calls/20261021T073243018863Z/`.

- [Received audio](../.local/delegate-calls/20261021T073243018863Z/remote.wav)
- [Sent audio](../.local/delegate-calls/20261021T073243018863Z/sent.wav)
- [Independent transcript](../.local/delegate-calls/20261021T073243018863Z/remote-transcript.txt)
- [Events](../.local/delegate-calls/20261021T073243018863Z/events.jsonl)
- [Summary](../.local/delegate-calls/20261021T073243018863Z/summary.json)
- [Manifest](../.local/delegate-calls/20261021T073243018863Z/manifest.json)

The local artifact hashes and hash-chained event ledger verified. The manifest is unsigned; these checks detect alterations relative to the local manifest, not external authenticity. There is no platform oracle, and the reported-balance task did not pass. Received audio segments are concatenated; stream-transition gaps are recorded separately.

## All attempts retained

| Local run directory | Result |
| --- | --- |
| `20261021T072231561003Z` | Tunnel reachability preflight failed; no call |
| `20261021T072424657754Z` | Call connected for 1 second; WSS signature-scheme bug prevented media; fixed and regression-tested |
| `20261021T072607711018Z` | Speech-ASR preflight mismatch; no call; bounded re-synthesis added without loosening the exact speech check |
| `20261021T072723651646Z` | Remote prompt received and independently transcribed; agent did not submit ID before timeout; prompt/action scheduling fixed |
| `20261021T072949614024Z` | Agent selected DTMF; premature old-stream closure hung up before call update; contained and cleaned up |
| `20261021T073119483134Z` | Same race captured as Twilio `21220`; fixed by letting Twilio retire the old stream after applying replacement TwiML |
| `20261021T073243018863Z` | DTMF accepted, audio reconnected, spoken output acknowledged; task blocked on missing sub-test number |

Five calls were made during these bridge experiments; two other attempts stopped before dialling. Provider prices were not available in immediate final-call responses. These development attempts do not establish a reliability rate.

## Next live step

Offline verification: all 16 telephony protocol/containment tests pass. These include WSS signatures, stale stream rejection, keeping the retiring stream open until the TwiML update, single-use routing/sub-test values, missing-sub-test stop behaviour, evidence failure containment and local artifact tamper detection.

Obtain the sub-test number from the operator. Rerun with `--route-id 770022 --subtest-id OPERATOR_SUPPLIED_VALUE`, inspect the resulting balance audio/transcript, and compare the model's extraction to that evidence. Test the separately authorised `770004` spoken-reason route only as an explicit later scenario. Full arbitrary-scenario UI, credential handling, adaptive speech policy and signed evaluation integration remain outside this CLI pilot.
