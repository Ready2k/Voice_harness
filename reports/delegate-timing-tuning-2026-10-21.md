# Delegate response timing — live tuning, 21 October 2026

**Result: shorter observed generated-response processing, but no completed wallet task.** Two bounded calls used `0161 768 3827`, DTMF `770222 / 01` and the worried-wallet brief. DTMF was selected because spoken initial routing failed in the previous test. Both calls were closed with cleanup confirmed.

## Configuration and safeguards

- Controller quiet wait reduced from 1500 to 250 ms.
- Provider VAD silence reduced from 900 to 500 ms.
- Semantic answer review runs alongside speculative TTS and independent ASR. Playback requires both to pass and the prompt/stream to remain current. No native realtime-model audio is played.
- Per-stage timing is now recorded. Synthesis and ASR remain sequential within their worker; their combined duration overlaps the separate semantic review.

These settings are opt-in; defaults remain unchanged. Offline tests verify concurrent work, no playback before both pass, draining failed workers, routing-announcement handling and unsupported access-denial rejection. The relevant offline suite has 64 passing tests; static checks pass.

## Calls

| Attempt | Call SID | Duration | Outcome |
| --- | --- | --- | --- |
| [1](recordings/timing-tuned-2026-10-21-attempt-1/README.md) | `CA49eee292ff10a106e5c661dc31ac4953` | 23 s | Premature model response to fragmented routing announcement; controller withheld speech, then accepted an incorrect model access-denial report |
| [2](recordings/timing-tuned-2026-10-21-attempt-2/README.md) | `CAa1a74cdfac7b692b1cdf36f14c414cb5` | 81 s | Both routing steps submitted; generated speech released; service reprompted, acknowledged, then fallback/end; no card guidance |

Between calls, the controller was changed to ignore neutral announcements until a routing request and reject access_denied reports unsupported by the latest remote prompt. This let the second call reach the conversational prompt. Its recordings and original manifests are retained unchanged.

## Measured response stages in attempt 2

For “Please say in a few words what you're calling about today,” the model proposed “I can't find my wallet and need guidance about the cards that were in it.”

| Stage | Observed duration |
| --- | ---: |
| Final service transcript to inference request | 0.25 s |
| Inference request to proposed reply | 2.57 s |
| Separate semantic review | 0.64 s |
| Speech synthesis | 1.04 s |
| Independent speech transcription | 0.69 s |
| Final service transcript to speech release | **4.57 s** |

Review overlaps synthesis/transcription, so the durations must not all be added. The earlier untuned test's debit-card response took **6.38 s**, and its first generated reason-for-calling proposal took approximately **6.67 s** before being withheld as stale. These are different turns and model samples, not a controlled same-response benchmark or proof of a repeatable improvement.

The tuned call's second release followed the partial transcript “Sorry, I didn't catch” by **5.20 s**. About 1.8 s elapsed before inference could be requested, including waiting for the previous playback action's lock. This remains a bottleneck. The remote service's actual speech end, recognition and physical audio delivery are not measured by these local event offsets; audible delays may be longer.

## Remaining issues

The model paraphrased the prepared opening on the actual reason-for-calling prompt, so fresh synthesis/transcription was needed. It also tried to respond to intent metadata; that earlier proposal was correctly withheld after new service speech arrived. Intent-metadata suppression was added after attempt 2 and passed offline testing, but was not tested in a third live call. A denied access-denial proposal also left a stale blocking_question in the recorded summary; code now assigns that field only after the blocked report is accepted. Historical evidence is unchanged.

The service still reprompted and ultimately refused handoff. It did not reach the banking-card questions. Timing improvements have not established conversational success, naturalness or the cause of the service fallback. Further work should address reliable turn segmentation, adherence to the prepared opening, playback-lock delay during interruptions, and service-side timing/recognition logs. No guardrail rejection caused the delay of the first released generated response; both checks allowed it.

[Download both recordings](timing-tuned-tests-2026-10-21.zip), extract and open each `index.html`. Source and derived playback manifests verify; manifests are unsigned. No credentials or tunnel logs are included.
