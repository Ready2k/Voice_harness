# Delegate telephone pilot

Telephone now offers **Gated** (the default) and **Native S2S** speech delivery. The examples below retain the default gated delivery; see **Native S2S versus gated speech** for the new mode and its evidence limitations.

This CLI pilot implements the first voice/DTMF transport experiment from [the delegate spec](DELEGATE_AGENT_SPEC.md). It does not expose the harness's operator API or change existing banking evaluations. The operator GUI now wraps this runner; it is not a general login agent.

## What the first proof does

The agent listens to the automated test line, selects when to send the bound routing ID (and optional supplied sub-test ID), can request the balance using a controlled spoken phrase, and reports a quoted finding. Twilio call updates send the digits and reconnect bidirectional audio on the same CallSid. The delegate session persists across stream generations. See the [live proof report](../reports/delegate-phone-proof-2026-10-21.md) for observed results and retained failures.

The pilot uses the existing OpenAI realtime adapter, independent ASR backend, speech synthesiser, resampler and hash-chained Ledger. It does not reuse the banking engine's account/identity verdicts: their roles and facts are inappropriate for a customer-side delegate. Without a brief it retains its fixed-phrase diagnostic path. Supplying a demo brief enables generated natural replies, reviewed against the scenario before synthesis and checked again through independent ASR. Gated delivery is the default and withholds native realtime-model audio. The explicit native option described below streams provider speech without waiting for output approval. Legacy briefs do not carry credentials. The GUI can now bind operator-supplied account/verification details through the separate detail tool described below; account changes remain unavailable. The full signed evaluation export still needs subsequent integration.

## Natural, goal-based wallet demo

The supplied [worried caller brief](../scenarios/delegate/lost-wallet-worried.json) describes an uncertain missing wallet, possibly left at work, a Barclaycard credit card, a Barclays current-account debit card and a Tesco Clubcard. These replace the older Premier/business-card facts for this scenario. The goal is to get guidance for both banking cards; the Clubcard is background context.

```bash
PYTHONPATH=src .venv/bin/python scripts/delegate_phone_demo.py \
  --to 01617683827 --caller-id 01483694454 --route-id 770222 --subtest-id 01 \
  --scenario lost-wallet --brief scenarios/delegate/lost-wallet-worried.json \
  --max-seconds 180 --tunnel-binary /absolute/path/to/cloudflared
```

This is a chargeable live-call command, not a rehearsal. Keep the routing values separate from the brief. Do not combine `--brief` with legacy `--wallet-card-included` or `--wallet-card-description` flags; the brief supplies the goal instead of `--task`. `mode` must be `demo`; only non-secret facts belong in this file. No personal identifiers, passcodes or generated credentials are supported.

The caller generates its own short replies, expresses concern without inventing events, answers the latest question and chooses a card to discuss first without operator input. The controller tracks items actually mentioned in transport-acknowledged speech and supplies that state alongside the conversation history. Mentioning a card is not proof that guidance for it is complete. The result gate requires both target cards to have been discussed, a quote heard from the service and a separate semantic review of whether the summary is supported for both targets.

Before sending each reply, a separate model reviews the proposed text, item references, brief and recent remote turns. It rejects unsupported personal facts, wrong card types, role reversal and unauthorised action consent. TTS then uses the chosen `worried` or `calm` delivery style; an independent ASR pass must match the approved wording. Common contractions are normalised without removing negation. New remote speech, a newer prompt or an interruption invalidates an in-flight reply. Each prompt can release once; repeated replies and total actions are bounded. In gated delivery, raw realtime-model audio cannot bypass this path.

An optional short `opening` in the brief is synthesised and independently transcribed before dialling. On the first reason-for-calling prompt, the model uses that wording so the controller can reuse its exact same-run assessed PCM, with a fresh contextual semantic review. Later replies remain generated. This avoids waiting for TTS and ASR inside the initial IVR response deadline. Cross-run speech-cache hits still undergo fresh preflight ASR. Do not add unprompted permission disclaimers to ordinary replies; boundaries are enforced internally and explained when relevant.

The review model defaults to `gpt-6-luna` (OpenAI's named replacement for `gpt-4.1-mini`, which shuts down on 2027-01-14), configurable via `VOICE_HARNESS_DELEGATE_REVIEW_MODEL`. The request asks for no reasoning (`reasoning_effort: none`) to keep the call short; if the model rejects that parameter the call is retried once without it. The same default and fallback apply to the model simulator's OpenAI chooser (`src/voice_harness/openai_chat.py`). It uses the existing OpenAI key and adds a separate model request per speech/result proposal. TTS supports tone through its [speech instructions](https://developers.openai.com/api/docs/guides/text-to-speech); scenario, tone and conversation flow follow the [official voice prompting guidance](https://developers.openai.com/api/docs/guides/voice-prompting). Review, TTS and ASR add response delay; live latency and naturalness need a recorded call evaluation.

For an explicitly tuned run, add `--turn-quiet-ms 250 --vad-silence-ms 500 --parallel-speech-checks`. Defaults remain 1500 ms controller quiet wait, 900 ms provider VAD silence and sequential checking. The shorter wait risks reacting to a partial turn; stale-prompt/remote-speech checks remain active. Parallel checking performs speculative synthesis and independent ASR while text review runs. It drains both workers and releases nothing unless both pass in the same still-current context; rejected speech can be synthesised internally but is never played. Same-run pre-assessed openings still receive fresh contextual review.

The ledger records `turn.response_requested` after the controller wait, `dialogue.review.duration_ms`, and `speech.assessment_timing` with synthesis/cache and ASR durations. Compare `speech.remote_transcript` to `dialogue.proposed` and `speech.release` for the total observed response delay. Concurrent durations overlap and must not be added as if sequential. These are local event timings, not verified remote acoustic end-to-end latency. A single call comparison is diagnostic, not a latency percentile or guarantee.

Routine dialogue and card ordering need no approval. Missing personal/security details or permission to freeze, cancel, replace or otherwise change a card/account still stop the call with the question preserved. There is no pause/resume facility or authority to make such changes in this version. The semantic reviewer is another model judgement, not a guarantee or external platform oracle. Offline tests exercise enforcement with injected proposals; they do not establish live model accuracy, vocal concern, completion or IVR compatibility.

## Requirements

- A Full Twilio account, a Voice-enabled caller number, destination calling permission and an authorised automated demo destination.
- Restricted key permissions for Calls create/read/update (list for reconciliation). The runner uses the account Auth Token for incoming signature validation. The GUI also uses it for a read-only phone-number lookup if the restricted key lacks number-list permission.
- OpenAI API access for the existing realtime model, TTS and independent transcription. Credentials stay in `.env`/the process environment and are not printed.
- A public HTTPS/WSS origin routing exclusively to this ingress, or an explicitly supplied official `cloudflared` binary for a temporary tunnel. No global install is required.
- Explicit permission for the demo audio to pass through the chosen tunnel and AI/ASR services and to be retained locally. Do not supply production personal/account data to this pilot.

Canonical environment names are `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, `TWILIO_AUTH_TOKEN` and `OPENAI_API_KEY`. The loader also recognises the existing lowercase `twilio_account_sid`, `twilio_sid`, `twilio_sid_secret` and `twilio_auth_token`. It never executes `.env` as shell code.

`--routing-mode voice` speaks the bound test ID and sub-test ID digit by digit instead of sending keypad tones. Both code recordings are synthesised and independently transcribed before dialling; transcription must match the exact bound digits, including leading zero. The model chooses a value reference, not arbitrary spoken credentials. The controller checks the latest prompt purpose, routing order and single-use limit. Voice mode disables the DTMF tool and does not reconnect the media stream to submit a code. It never silently falls back to keypad tones; an IVR requiring DTMF may reject voice input. This mode covers only the supplied demo routing values, not general account authentication. `summary.json` records routing mode and consumed references separately from actual DTMF use.

## Run one test

From the repository root, after authorising the live test:

```bash
PYTHONPATH=src .venv/bin/python scripts/delegate_phone_demo.py \
  --to 01617683858 --caller-id 01483694454 --route-id 770022 \
  --max-seconds 90 --tunnel-binary /absolute/path/to/cloudflared
```

Alternatively pass `--public-url https://your-telephony-ingress.example` when routing is already configured to the selected local port (default 8770). The service listens on loopback. The public app has only `/media` and `/status`, with no docs, operator controls or artifact downloads. It verifies Twilio signatures using the configured external URL and rejects wrong account/call bindings and stale or replayed streams.

For a replay of the older fixed-phrase lost-wallet test, the supplied route is `770222`, with sub-test `01`:

```bash
PYTHONPATH=src .venv/bin/python scripts/delegate_phone_demo.py \
  --to 01617683827 --caller-id 01483694454 --route-id 770222 --subtest-id 01 \
  --scenario lost-wallet --wallet-card-included yes --max-seconds 180 \
  --wallet-card-description "It's my Premier and business banking card." \
  --tunnel-binary /absolute/path/to/cloudflared
```

The lost-wallet profile replaces the balance phrases with “I've lost my wallet”, clarification/guidance requests, delegate disclosure and goodbye. `--wallet-card-included yes` (or `no`) adds the operator's explicitly supplied answer about whether the wallet contained their Barclays card; omit it when unknown. The example's `yes` was expressly confirmed by this operator and must not be assumed for other callers. It requires both routing steps before speaking. It preserves the latest question when personal information, an unknown answer or approval for an account-changing action is required. It cannot invent account details or approve card blocking. Native model audio remains withheld. This is a bounded scripted-speech pilot, not unrestricted conversational speech.

The optional `--wallet-card-description` binds the operator's supplied product description as an approved spoken phrase, including it in the card-in-wallet confirmation. The example uses this operator's exact intended wording; it does not infer card numbers or whether there are multiple accounts. This demo description field rejects numeric credentials/card numbers and requires an explicitly confirmed card-in-wallet fact.

Observed attempts on the original `0161 768 3858` destination did not route the lost-wallet test. The operator corrected the destination to **0161 768 3827**, which accepted **770222 / 01** and reached the AI-agent conversation. See [the lost-wallet attempt report](../reports/delegate-lost-wallet-2026-10-21.md) for recordings, preserved failures and the exact question at which the first correct-destination attempt stopped.

The number normaliser converts `0161…`, `+44 (0)161…` and `0044 0161…` to `+44161…`. Start triggers a chargeable call only after speech, ASR, provider and ingress preflight. Each bound keypad value can be sent once, with optional `#` when the observed prompt requires it. No uncertain call creation or digit send is automatically retried. The `770022` route has now been observed to request a sub-test number after accepting the routing ID: supply `--subtest-id VALUE` only when the operator provides that value. Without it, the pilot stops on the sub-test prompt rather than guessing or repeating speech.

Press Ctrl-C to stop. The runner attempts API hangup on every exit. A provider-side call time limit also bounds the call if the process dies. The runner shuts down its ingress and temporary tunnel. An unresolved dial-create response is explicitly uncertain and must be reconciled in Twilio before another attempt; no unknown CallSid can be hung up locally.

## Inspect the proof

Each attempt gets a private directory under `.local/delegate-calls/` (git-ignored):

| File | Meaning |
| --- | --- |
| `remote.wav` | Actual remote audio received over the media WebSocket, 8 kHz PCM; intervals around keypad transitions may be missing |
| `sent.wav` | The approved speech chunks sent toward Twilio, not proof of remote audibility |
| `remote-transcript.txt` | Independent post-call transcription of the received segments |
| `events.jsonl` | Durable hash-chained call, stream, prompt, tool, speech, gap and cleanup events |
| `summary.json` | Model finding, evidence availability, cleanup and explicit limits |
| `manifest.json` | Local artifact hashes and ledger tail binding; unsigned and not an external trust root |
| `tunnel.log` | Temporary tunnel diagnostics; excluded from the evidence manifest because it may still be written during shutdown |
| `audio-timing.json` | Per-chunk source frame offsets and a common monotonic transport clock; included in the original manifest for new calls |
| `index.html` | Combined playback, original tracks and evidence views; works from disk |
| `conversation.wav` / `conversation-stereo.wav` | One mixed playback, or service-left/delegate-right stereo on the same timeline |
| `alignment.json` / `playback-manifest.json` | Derived sample placement, timing-quality label and source/output hash bindings |

New calls automatically create combined playback after source evidence finalisation. Remote frame positions use Twilio stream presentation timestamps anchored to the first packet's local receive clock. Each delegate reply uses its first local send time as its start, then preserves its original PCM continuously: Twilio buffers outbound chunks sequentially, so local packet scheduling jitter must not become tiny silences inside speech. Reply boundaries and inter-reply gaps remain on the recorded timeline. This aligns transport capture, not unknown remote acoustic latency. Twilio describes its timestamp in the [Media Streams message contract](https://www.twilio.com/docs/voice/media-streams/websocket-messages).

Older recordings without per-frame capture timing can be reconstructed from stream and speech events with an explicit **Estimated alignment** label. Their lost audio cannot be recovered. To render/re-render a review page:

```bash
PYTHONPATH=src .venv/bin/python scripts/render_call_review.py PATH_TO_EVIDENCE_DIRECTORY
```

For the first reconstruction, `--speech-cache .local/delegate-speech-cache` can recover utterance lengths from approved PCM hashes. The saved alignment allows subsequent rendering without the cache. Rendering preserves the source WAVs, ledger and original manifest and creates a separate derived manifest. The [shared recording package](../reports/recordings/lost-wallet-2026-10-21/README.md) includes this additional view.

The runner verifies the local evidence binding at exit. To verify again:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from pathlib import Path
from voice_harness.telephony_demo import verify_output
print(verify_output(Path('.local/delegate-calls/YOUR_RUN_DIRECTORY')))
PY
```

A completed call is not a task pass. Evidence of success needs: inbound prompt/audio; the model's prompted DTMF action; reconnection on the same call; the subsequent balance response; and a finding supported by the independent transcript. If the IVR requires a spoken reason, also inspect the speech release and uncleared playback acknowledgement. Mark acknowledgements describe transport playback, not physical audibility. A cleared mark must not count as played.

`source_quote_supported_by_independent_asr` checks only that the model's quote occurs in the independent transcript. It does not establish that its extracted amount/currency is correct. There is no platform oracle yet: results remain **reported by the called service, not independently verified**. Review the actual audio and transcript before describing the demo as successful.

The first attempt uses `770022` (automatic balance). `770004` is the separately authorised open-question route, for a later explicit voice-path test. Preserve failed attempts as well as successes. Delete local audio when its demo-review purpose is complete; this pilot has no automated retention service.

## Offline checks

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_telephony_demo -v
```

These cover number formatting, mu-law codec values, signature tampering, wrong/stale stream binding, hidden operator/artifact routes, out-of-scope tool denial, single-use DTMF, uncertain-send containment, evidence failure before an external action, cleanup and artifact tampering. They do not establish live account/region/IVR support.

## Operator GUI

Open `/client/telephone.html` on the host API and connect with the operator token. Enter an authorised destination, Twilio caller number and supplied routing IDs; choose the balance diagnostic or supplied adaptive wallet brief, keypad/spoken routing and timing settings. **Start chargeable call** runs the existing preflight and bounded CLI runner. Stop requests its normal hangup and evidence cleanup. History, summaries, audio and evidence downloads stay behind operator authentication. Findings remain diagnostic rather than independent platform verification.

Configure `VOICE_HARNESS_TUNNEL_BINARY` with the absolute path to the existing official cloudflared executable (or put it on PATH), or set `VOICE_HARNESS_TELEPHONE_PUBLIC_URL` for an origin already forwarding to port 8770. The public ingress still exposes only signed Twilio endpoints. Credentials use the existing environment/`.env` loader; setup status shows missing names without values. The host must contain the repository runner and its dependencies.

GUI call artifacts are stored under `HARNESS_DATA/telephone-calls/`. One GUI call may run per API process; do not run a separate CLI call or multiple operator API workers against the same ingress port. A server interruption can leave a call outcome uncertain: reconcile it in Twilio before retrying. The Twilio time limit still bounds the call. Existing CLI attempt folders remain separate.

The GUI fetches voice-capable caller numbers from Twilio IncomingPhoneNumbers through the authenticated `/api/v1/telephone/configuration` endpoint. It prepopulates the caller number and local scenario routing defaults without overwriting edited fields on refresh. No account credentials are returned to the browser.

### Response mode and agent selection

The Telephone page offers **Static · fixed spoken phrases** or **AI agent · generated replies**, plus OpenAI Realtime, Google Gemini, Amazon Nova Sonic, xAI Grok Voice or ElevenLabs Agents. Static mode still uses the selected agent to choose routing/phrase actions; it is not a deterministic scripted IVR. Both wallet and balance scenarios accept generated replies using the editable goal and supplied facts, or operator-defined static phrases. Missing provider configuration disables its choice, and the API rejects unavailable selections without falling back.

Each run forwards `--provider-config-id` to the runner and records it in the start event, provider connection receipt and final summary. CLI runs can select the same provider with `--provider-config-id gemini-live` or `--provider-config-id nova-sonic-live`. `--brief` enables generated wallet replies; omitting it retains fixed phrases. In gated delivery, all providers use shared OpenAI speech synthesis, independent ASR and (for generated replies) semantic review, so agent selection changes reasoning rather than the audible TTS voice. Native delivery instead forwards the selected provider’s own speech without pre-playback approval.

Gemini and Nova telephone integrations are experimental. They retain provider-controlled audio endpointing; Nova uses LOW sensitivity rather than the OpenAI millisecond VAD control. The controller still supplies current prompt IDs and enforces routing, speech and action bounds. Their native tool-result interfaces automatically continue model turns and cannot honour the OpenAI `respond=False` tool-result flag. Native model audio remains withheld in gated delivery. Offline adapter tests do not establish live telephone compatibility or conversational quality; each provider needs a recorded authorised call evaluation.

### Editable goals and supplied details

Both response modes now accept an editable goal, situation/background, opening, delivery tone and detail rows. The scenario picker loads a starting template; it does not invent account or verification details. Generated replies now work for balance calls as well as lost-card/wallet calls. General scenario facts such as card brand/type are included in the caller brief. The result must satisfy the supplied goal with a quote from the called service.

Fixed mode also accepts **When asked about / Say exactly** rows. These define the available spoken phrases, with an AI controller selecting between them. The opening is an additional fixed phrase. An unanswered question still requires a supplied response/detail or stops as missing information.

For account IDs, identity answers or passcodes, choose the corresponding detail type, specify **When asked for** (for example `account number` or `passcode`) and speech or keypad delivery. Keypad values must be digits. Values retain leading zeros and are bound to one call. The agent sees references, labels and purposes, not the underlying values in its initial prompt. `phone_provide_detail` can submit only an existing bound reference for the latest matching explicit request after test routing; it consumes the detail before transmission and never retries an uncertain send. Hash terminators require a matching remote instruction. These checks are conservative lexical matching, not a semantic proof of the remote party's intent.

The GUI sends the validated setup to the child runner through a private standard-input pipe (`--call-setup-stdin`), with no credential values in command arguments or `setup.json` history. Spoken identifier/passcode audio is independently assessed before calling and bypasses the shared speech cache. Detail values are redacted from controller event text; raw retained call audio and independent transcripts can still contain spoken or echoed values. Use only test details authorised for the chosen destination. Fixed phrases, goals and background are ordinary visible scenario data; enter verification values in the dedicated detail rows.

Calls still cannot approve card/account changes, payments or transfers. This is a bounded demo delegate, not a general banking login service.


## Native speech versus gated speech

The Telephone page now has a **Speech delivery** selector independent of the AI/static response selector. API requests accept `audio_mode: "gated"` (default) or `"native"`; the CLI accepts `--audio-mode native`. Native delivery requires AI responses and an adaptive brief (or AI call setup). Static/native combinations are rejected before dialling.

Gated delivery retains semantic review, shared speech synthesis and independent output ASR before playback. Native delivery uses the selected adapter's own PCM audio, resampled continuously and encoded as 8 kHz G.711 mu-law for Twilio. It does not call the shared synthesiser for conversational replies and does not wait for semantic speech approval or independent output ASR. Native outgoing speech is **unassessed**: instructions still prohibit consent and invented facts, but those semantic boundaries cannot be guaranteed before playback. This is a diagnostic comparison on an authorised demo destination.

Routing stays under controller tools; native audio is withheld until all bound routing steps have been submitted. Destination, signed stream binding, time/action limits, DTMF restrictions, credential value isolation and single-use bound-detail checks remain active. Spoken routing codes and operator-bound identifiers retain their separately checked controlled delivery, which may use shared synthesis. No account-changing tool is added. Native result proposals still require a heard remote quote and the independent result reviewer; unassessed provider transcripts are supplied as context, never as proof of delivery or completed card actions. Gated discussed-item tracking remains based on approved replies; native transcripts do not populate that approved ledger.

Native playback has a bounded local queue (20 seconds of 20 ms frames, at most 160 KB of encoded audio) and a total audio limit. This tolerates normal faster-than-playback provider bursts while the sender paces audio in real time. Overflow stops the run explicitly. On provider interruption, pending local audio is discarded, Twilio receives `clear`, the resampler resets and late chunks for retired output items are ignored. Stream generation/epoch checks prevent queued audio reaching a replacement stream. Controlled speech clears pending native playback before starting. Cancellation/truncation of provider conversation history is **not** implemented by the telephone transport: Gemini, Nova and ElevenLabs do not expose truncate-at-offset through these adapters; provider barge-in signals drive local clearing. Provider timing remains provider-specific, so this path is native provider audio over the existing controller, not an assertion of identical turn timing across providers.

`run.started`, `dialogue.configured` and `summary.json` identify `audio_mode` and `speech_policy`. Native summaries say `native_speech_assessment: "not_performed"` and `speech_authority_enforced_before_playback: false`. `speech.native_audio_sent` identifies unapproved transport bytes; `speech.native_transcript` is provider transcription, not independent output ASR. The normal `sent.wav` recording and timing ledger preserve exactly the mu-law-decoded bytes sent to Twilio; physical audibility remains unknown. No live call is required to run the offline tests.


Native output identity is checked against the existing adapter normalisation: OpenAI and xAI preserve `response.output_audio.delta.item_id`; Nova preserves `audioOutput.contentId`; Gemini and ElevenLabs generate monotonic `turn_N` IDs. Offline tests exercise each raw event shape and verify that its normalised audio is accepted. The common `AudioOut` schema permits a missing ID, and older sanitised Nova probe fixtures omit IDs; those events are explicitly unsupported by native Telephone playback and stop with `INVALID_NATIVE_AUDIO` before forwarding. No identity is guessed and no shared adapter is altered to manufacture one. These offline shape checks do not establish that every deployed provider version always includes its expected ID.
