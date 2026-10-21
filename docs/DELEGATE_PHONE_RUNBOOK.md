# Delegate telephone pilot

This CLI pilot implements the first voice/DTMF transport experiment from [the delegate spec](DELEGATE_AGENT_SPEC.md). It does not expose the harness's operator API or change existing banking evaluations. It is not yet the number/scenario setup UI or a general login agent.

## What the first proof does

The agent listens to the automated test line, selects when to send the bound routing ID (and optional supplied sub-test ID), can request the balance using a controlled spoken phrase, and reports a quoted finding. Twilio call updates send the digits and reconnect bidirectional audio on the same CallSid. The delegate session persists across stream generations. See the [live proof report](../reports/delegate-phone-proof-2026-10-21.md) for observed results and retained failures.

The pilot uses the existing OpenAI realtime adapter, independent ASR backend, speech synthesiser, resampler and hash-chained Ledger. It does not reuse the banking engine's account/identity verdicts: their roles and facts are inappropriate for a customer-side delegate. Without a brief it retains its fixed-phrase diagnostic path. Supplying a demo brief enables generated natural replies, reviewed against the scenario before synthesis and checked again through independent ASR. Native realtime-model audio is withheld in both modes. Credentials and account changes remain unavailable. The full signed evaluation export still needs subsequent integration.

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

Before sending each reply, a separate model reviews the proposed text, item references, brief and recent remote turns. It rejects unsupported personal facts, wrong card types, role reversal and unauthorised action consent. TTS then uses the chosen `worried` or `calm` delivery style; an independent ASR pass must match the approved wording. Common contractions are normalised without removing negation. New remote speech, a newer prompt or an interruption invalidates an in-flight reply. Each prompt can release once; repeated replies and total actions are bounded. Raw realtime-model audio cannot bypass this path.

An optional short `opening` in the brief is synthesised and independently transcribed before dialling. On the first reason-for-calling prompt, the model uses that wording so the controller can reuse its exact same-run assessed PCM, with a fresh contextual semantic review. Later replies remain generated. This avoids waiting for TTS and ASR inside the initial IVR response deadline. Cross-run speech-cache hits still undergo fresh preflight ASR. Do not add unprompted permission disclaimers to ordinary replies; boundaries are enforced internally and explained when relevant.

The review model defaults to the repository's existing `gpt-4.1-mini` choice, configurable via `VOICE_HARNESS_DELEGATE_REVIEW_MODEL`. It uses the existing OpenAI key and adds a separate model request per speech/result proposal. TTS supports tone through its [speech instructions](https://developers.openai.com/api/docs/guides/text-to-speech); scenario, tone and conversation flow follow the [official voice prompting guidance](https://developers.openai.com/api/docs/guides/voice-prompting). Review, TTS and ASR add response delay; live latency and naturalness need a recorded call evaluation.

Routine dialogue and card ordering need no approval. Missing personal/security details or permission to freeze, cancel, replace or otherwise change a card/account still stop the call with the question preserved. There is no pause/resume facility or authority to make such changes in this version. The semantic reviewer is another model judgement, not a guarantee or external platform oracle. Offline tests exercise enforcement with injected proposals; they do not establish live model accuracy, vocal concern, completion or IVR compatibility.

## Requirements

- A Full Twilio account, a Voice-enabled caller number, destination calling permission and an authorised automated demo destination.
- Restricted key permissions for Calls create/read/update (list for reconciliation). The account Auth Token is used only for incoming signature validation.
- OpenAI API access for the existing realtime model, TTS and independent transcription. Credentials stay in `.env`/the process environment and are not printed.
- A public HTTPS/WSS origin routing exclusively to this ingress, or an explicitly supplied official `cloudflared` binary for a temporary tunnel. No global install is required.
- Explicit permission for the demo audio to pass through the chosen tunnel and AI/ASR services and to be retained locally. Do not supply production personal/account data to this pilot.

Canonical environment names are `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, `TWILIO_AUTH_TOKEN` and `OPENAI_API_KEY`. The loader also recognises the existing lowercase `twilio_account_sid`, `twilio_sid`, `twilio_sid_secret` and `twilio_auth_token`. It never executes `.env` as shell code.

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

New calls automatically create combined playback after source evidence finalisation. Remote frame positions use Twilio stream presentation timestamps anchored to the first packet's local receive clock; sent frames use local send times. This aligns transport capture, not unknown remote acoustic latency. Twilio describes its timestamp in the [Media Streams message contract](https://www.twilio.com/docs/voice/media-streams/websocket-messages).

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
