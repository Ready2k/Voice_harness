# Delegate telephone pilot

This CLI pilot implements the first voice/DTMF transport experiment from [the delegate spec](DELEGATE_AGENT_SPEC.md). It does not expose the harness's operator API or change existing banking evaluations. It is not yet the number/scenario setup UI or a general login agent.

## What the first proof does

The agent listens to the automated test line, selects when to send the bound routing ID (and optional supplied sub-test ID), can request the balance using a controlled spoken phrase, and reports a quoted finding. Twilio call updates send the digits and reconnect bidirectional audio on the same CallSid. The delegate session persists across stream generations. See the [live proof report](../reports/delegate-phone-proof-2026-10-21.md) for observed results and retained failures.

The pilot uses the existing OpenAI realtime adapter, independent ASR backend, speech synthesiser, resampler and hash-chained Ledger. It does not reuse the banking engine's account/identity verdicts: their roles and facts are inappropriate for a customer-side delegate. Its separate diagnostic speech policy allows only four fixed phrases whose synthesised audio has passed independent ASR. Native model audio is withheld. Arbitrary speech, credentials and account changes are unavailable. This is a deliberately limited first proof; general adaptive speech and the full signed evaluation export need subsequent integration.

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
