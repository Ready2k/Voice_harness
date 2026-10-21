# Lost-wallet telephone attempt — 21 October 2026

The operator requested sharing the latest demo recording. A portable, repository-tracked [recording package](recordings/lost-wallet-2026-10-21/README.md) now includes received and sent WAVs, transcript, events, manifest and a local playback page. The user also clarified that the finished delegate must be adaptive and goal-based; see [the next implementation scope](../docs/DELEGATE_ADAPTIVE_SCOPE.md). The recorded pilot is still fixed-phrase speech and does not satisfy that requirement.

## Premier / Business Banking description supplied

The operator next supplied **“It's my Premier and business banking card.”** Call `CA7b5b4110b561f43a873baa5ddbab7215` used that approved description on `0161 768 3827`, with `770222 / 01`. It completed after **106 seconds**. Routing succeeded, the delegate said the wallet statement and confirmed the card with the supplied description. The remote AI explicitly responded:

> Thanks. So you've lost both your Premier and business banking cards. Which would you like to sort first?

The remote AI interpreted the description as two cards. The operator has not yet supplied which to handle first. The delegate repeated the description rather than choosing a priority, and the service subsequently ended with its “cannot put you through” message. No priority or card/account change is claimed. The pilot now stops explicitly on an unprovided card-priority question rather than repeating the description; that new stop rule is offline-tested, not yet live-tested.

Evidence: `.local/delegate-calls/20261021T151234849587Z/` — [received recording](../.local/delegate-calls/20261021T151234849587Z/remote.wav), [sent recording](../.local/delegate-calls/20261021T151234849587Z/sent.wav), [independent remote transcript](../.local/delegate-calls/20261021T151234849587Z/remote-transcript.txt), [summary](../.local/delegate-calls/20261021T151234849587Z/summary.json). The run captured **102.06 seconds** of received audio and sent **7.8 seconds** of approved speech. Call cleanup was confirmed and the temporary tunnel stopped.

An earlier preparation attempt, `20261021T150955305389Z`, failed before dialling due to speech-service unavailability. The subsequent run retried transient synthesis errors before dialling. Synthesis clips are now cached locally to avoid regenerating them, while every cached clip still passes a fresh independent ASR check before release. The original failed attempt is retained.

## Latest outcome: two-way lost-wallet exchange demonstrated

The latest call, `CA03c861598bae7247cb01a20fde5724c8`, used **0161 768 3827**, auto-test **770222**, sub-test **01**, and the operator's confirmed card-in-wallet answer. It completed after **81 seconds**. Both keypad steps succeeded on one CallSid, with stream generations 0, 1 and 2. The delegate waited for the complete reason-for-call prompt, spoke the wallet statement, heard the follow-up question, and answered it with the supplied fact.

Remote transcript excerpt:

> Please say in a few words what you're calling about today. Got it, thanks. Just to check, your card was in that wallet, yes? Thanks. I am afraid I cannot put you through right now. Sorry about this. Please try us again later.

An independent post-run transcription of the **actual encoded/sent audio recording**, not merely the planned phrase text, returned:

> I've lost my wallet. Yes, my Barclays card was in the wallet.

The remote service ended after acknowledging the answer. There was no further question. This demonstrates routing, keypad input, two-way audio and a model-selected follow-up answer from operator-supplied facts. It does **not** establish completion of a lost-card service task, downstream routing, account authentication or any card/account change. The cause of the remote “cannot put you through” ending is not established.

The run captured **77.84 seconds** of received audio and sent **5.05 seconds** of approved speech. Call cleanup was confirmed and the temporary tunnel was stopped. The original manifest/ledger binding verified; it remains unsigned and has no independent platform oracle.

Latest evidence: `.local/delegate-calls/20261021T150351746772Z/`.

- [Received audio](../.local/delegate-calls/20261021T150351746772Z/remote.wav)
- [Sent audio](../.local/delegate-calls/20261021T150351746772Z/sent.wav)
- [Remote independent transcript](../.local/delegate-calls/20261021T150351746772Z/remote-transcript.txt)
- [Derived sent-audio review](../.local/delegate-calls/20261021T150351746772Z/sent-review.json)
- [Summary](../.local/delegate-calls/20261021T150351746772Z/summary.json)
- [Manifest](../.local/delegate-calls/20261021T150351746772Z/manifest.json)

The derived sent-audio review includes its source artifact SHA-256. It was produced after finalisation and is not part of the original manifest; the source `sent.wav` itself is manifest-bound. All prior attempts and limitations below are retained.

Operator request: call `+441617683858`, enter auto-test **77022**, sub-test **01**, say **“I've lost my wallet”**, then follow the questions. This new five-digit ID was used exactly; it was not silently replaced with the earlier six-digit `770022`.

## Correct destination: 0161 768 3827

The operator changed the destination to **+441617683827**, retaining auto-test **770222** and sub-test **01**. Call `CA05be546aa282f1aee007f466f6c849c3` reached the automated AI-agent module and completed after **67 seconds**:

1. The IVR accepted `770222`, announced the open-question AI-agent module and requested a sub-test.
2. The delegate sent the bound `01`. The service announced that its test-harness Lambda call succeeded and an open-question flow was selected.
3. The delegate sent the approved “I've lost my wallet” audio. The remote AI subsequently said, “Right, so your wallet is lost. Does that include your Barclays card?” This corroborates semantic receipt of the wallet statement, beyond a transport acknowledgement.
4. The delegate stopped with `TASK_BLOCKED:missing_answer` rather than inventing whether a Barclays card was included. API hangup/cleanup was confirmed.

The run captured **62.86 seconds** of remote audio and sent **6.5 seconds** of approved speech. Both routing values were consumed, and stream generations 0, 1 and 2 remained on one CallSid. The local evidence binding verified.

Evidence directory: `.local/delegate-calls/20261021T145554299312Z/`.

- [Correct-destination recording](../.local/delegate-calls/20261021T145554299312Z/remote.wav)
- [Independent transcript](../.local/delegate-calls/20261021T145554299312Z/remote-transcript.txt)
- [Summary](../.local/delegate-calls/20261021T145554299312Z/summary.json)

The operator then confirmed **yes, the Barclays card was in the wallet**. A subsequent attempt is authorised with that additional bound demo fact. This does not authorise card blocking, charges or account changes.

### First retry with the card-in-wallet answer

Call `CA9df9aa347d15dc9f873678220db29d6f` used the same correct destination and routing plus the confirmed card-in-wallet fact. It completed after **80 seconds**. Routing succeeded, but the delegate reacted to fragmented speech/acknowledgements (“Got it”, “Of course”, “Thanks”), repeatedly requesting clarification. The service eventually ended the call. The task did not complete.

Evidence: `.local/delegate-calls/20261021T150011233105Z/`, including [recording](../.local/delegate-calls/20261021T150011233105Z/remote.wav) and [independent transcript](../.local/delegate-calls/20261021T150011233105Z/remote-transcript.txt). Local evidence verification passed and cleanup was confirmed.

The subsequent turn-handling change waits for a quiet interval, retires superseded partial prompts and ignores standalone acknowledgements/diagnostic announcements. Offline tests exercise these behaviours before another live attempt.

## Retry with corrected ID 770222

The operator then corrected the auto-test ID to **770222**. Call `CAdd0397650189ba0ce2965281be55f0ab` used that exact value on the same destination, with sub-test `01` bound for the next step. Twilio reported `completed`, duration **16 seconds**. The agent requested the routing DTMF action and audio reconnected on the same CallSid in **2,771.3 ms**. The service again ended without asking for the sub-test:

> Please enter your auto-test number. I am afraid I cannot put you through right now. Sorry about this. Please try us again later.

No sub-test digits or wallet phrase were sent. The response does not establish whether the ID is invalid, unavailable or rejected for another reason. No alternate ID was guessed or retried automatically. The earlier `770022` remains the only identifier observed to reach a sub-test prompt.

Evidence directory: `.local/delegate-calls/20261021T145149477076Z/`.

- [Corrected-ID recording](../.local/delegate-calls/20261021T145149477076Z/remote.wav)
- [Independent transcript](../.local/delegate-calls/20261021T145149477076Z/remote-transcript.txt)
- [Summary](../.local/delegate-calls/20261021T145149477076Z/summary.json)

Local evidence verification passed and call cleanup was confirmed. The temporary tunnel was shut down by the runner.

## Result

Call `CA01dcff7a4a18d971852eeb52802a2c3b` completed after **23 seconds**. The agent heard the routing prompt and sent the bound `77022` keypad value. Twilio applied the update and reconnected audio, but the remote service did not progress to a sub-test prompt. Independent transcription of the received audio:

> Please enter your auto-test number. I am afraid I cannot put you through right now. Sorry about this. Please try us again later.

The sub-test was **not sent** and the wallet phrase was **not spoken**. No follow-up questions were reached. This response alone does not prove why routing failed. The earlier six-digit `770022` test did reach a sub-test prompt, so the operator was asked whether to retry that identifier with sub-test `01`.

Cleanup was confirmed. The managed temporary tunnel and local ingress were shut down. The local ledger/artifact binding verified; the bundle remains unsigned and does not establish an independently verified platform outcome.

## Evidence

Directory: `.local/delegate-calls/20261021T144902375369Z/` (private and git-ignored).

- [Received audio](../.local/delegate-calls/20261021T144902375369Z/remote.wav)
- [Independent transcript](../.local/delegate-calls/20261021T144902375369Z/remote-transcript.txt)
- [Summary](../.local/delegate-calls/20261021T144902375369Z/summary.json)
- [Events](../.local/delegate-calls/20261021T144902375369Z/events.jsonl)

An earlier setup attempt, `20261021T144434885630Z`, stopped before dialling because of a new scenario-preflight variable-name bug. That bug was corrected and a full offline scenario-setup regression added before retrying. No telephone call was placed by that first attempt.

## Implementation changes

- Accept five- or six-digit operator-bound test IDs and a separate sub-test ID.
- Add a bounded lost-wallet speech profile that includes the requested opening phrase.
- Keep independent ASR checks before any phrase can be played; normalise the equivalent contractions “I've” and “I have” without removing negation.
- Require routing/sub-test actions before speaking in this profile.
- Preserve the current question and stop when unknown personal details or additional action approval are needed; do not invent answers.
- Prepare independent speech phrases concurrently, with a bounded worker count, before dialling.
- Wait for Cloudflare connection registration before probing a new tunnel to reduce transient DNS failures.

The lost-wallet profile is a fixed-phrase diagnostic pilot, not unrestricted conversational speech. Subsequent questions may require more operator-supplied facts or a later adaptive speech policy.
