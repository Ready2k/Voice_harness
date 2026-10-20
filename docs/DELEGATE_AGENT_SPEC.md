# Delegate agent demo — voice, DTMF, API and browser

Status: proposed implementation specification v0.2, 19 October 2026. No runtime integration is implemented by this document.

## First build: number + scenario + supporting details

The first deliverable is a telephone demo. The operator enters a number, writes a task in plain language, supplies supporting demo details (including account/passcode when needed), then presses Start. The harness dials that number, connects the AI delegate, and lets it listen, speak and choose keypad digits dynamically. The operator sees progress, can stop the call, and receives the result and redacted evidence afterward.

The normal form requires only **Phone number**, **Task/scenario**, and **Supporting details**. Sensitive values use separately masked fields. A scenario is not restricted to the two templates below: an ad hoc task is saved as a versioned per-run brief. Voice and keypad are enabled by default. Provider, caller ID, recording, limits and disclosure settings have configured defaults under Advanced; the operator reviews the task and permitted disclosures on Start. A supplied destination is validated and bound to this run by Start, without requiring a developer to edit a destination registry. Deployment-level destination restrictions still apply.

The delegate discovers menus from the call; pre-entered menu maps are optional hints. No platform API, browser integration, platform source changes or expected-answer dataset is required to place a call and attempt a task. Without an independent reference, report the result as **reported by the called service; not independently verified**. The agent's completion claim must not be presented as an independently verified pass.

Build milestones M0–M2 plus telephone evidence, cleanup and runbook work from M5 constitute this first deliverable. API, browser and combined journeys (M3–M4) are later optional extensions; their acceptance checks do not block the telephone demo. Start implementation with a controlled IVR and fixture data; the operator's chosen number and credentials are needed for the subsequent live rehearsal, not for building the core.

## 1. Outcome

Demonstrate that a user can give an AI delegate a task, demo account data and bounded authority; the delegate then accesses a demo platform on their behalf, obtains the requested information, and reports it with verifiable evidence.

Initial tasks:

1. Sign in to a demo account and check a gift card balance, including currency and observation time.
2. Sign in to a demo account and check whether a specified return has been processed, distinguishing received, inspected, refund initiated and refund completed according to the platform's actual statuses.

Access options are independently configurable: telephone with voice and keypad; platform API; web browser. Combined runs can use more than one channel. A scenario declares which combinations are meaningful. The operator supplies the scenario and demo data; templates make the two initial tasks easy to configure.

Success requires the correct account, correct object, authorised access and a result matching platform evidence. A convincing conversation alone is insufficient. The delegate identifies itself as an AI assistant acting with the user's permission where it speaks to a person or the scenario requires disclosure. It must not claim to be the human account holder.

## 2. Scope and relationship to the current harness

The existing harness evaluates a voice agent serving a synthetic customer. This extension introduces a separate `delegate_demo` run type: the model is the customer's delegate and the external demo platform is the service being accessed. Existing scenario roles and banking verdicts must not be silently reused with reversed meanings.

Reuse provider adapters, policy enforcement, approval infrastructure, independent speech assessment, evidence storage, exports and cost accounting where their contracts fit. Extract a transport-neutral continuous session boundary from the manual-session approach rather than impersonating a browser microphone or fabricating browser playback acknowledgements.

The original evaluation baseline excludes production telephony and real customer data. This proposal adds opt-in outbound demo telephony and demo platform connectors in a separate profile; the original evaluation profile remains valid. The default configuration cannot dial or reach arbitrary external platforms. Demo credentials may be genuine credentials for the isolated demo environment, but must not be production account credentials.

Included: outbound dialling, adaptive voice conversation, dynamic outbound DTMF, inbound DTMF events where exposed, API and browser access, user approval, cancellation, fault handling, evidence and deterministic scenario checks.

Deferred: real financial transactions, purchases, changing account security, bypassing MFA/CAPTCHA, voice cloning, automatic production enablement, warm human call transfer and multi-party conferences. Initial tasks are read-only. Return-status lookup does not authorise creating a return or requesting a refund.

## 3. Run setup and operator experience

The setup form exposes:

| Setting | Behaviour |
| --- | --- |
| Scenario | Gift card balance, return status, or a versioned custom task |
| Task and expected output | Plain-language objective plus structured fields the result must contain |
| Demo subject | Account reference and target gift card/return/order reference |
| Voice telephone | Toggle; allowlisted E.164 number, caller ID, language and voice |
| Keypad | Toggle within telephone; permitted menu actions and credential references |
| Platform API | Toggle; connector, environment, allowed operations and credential binding |
| Web browser | Toggle; demo origin, login flow and permitted pages/actions |
| Channel strategy | Fixed sequence or agent-selected among enabled channels |
| Cross-channel fallback | Explicit toggle with permitted transitions and reasons |
| Model | Provider/model/voice supported by current probes; capability failures shown before start |
| Authority | Allowed reads, allowed credential disclosures, expiry and approval rules |
| Limits | Call duration, hold timeout, action count, login attempts and monetary budget |
| Evidence | Recording/transcript policy, retention and reviewer access |

At least one channel is required. Keypad without telephone is invalid. A voice-only run must be rejected before dialling if its scenario requires keypad-only authentication. Enabling API/browser does not implicitly permit their use when a scenario requires proving telephone access. An agent-selected strategy records every selection and fallback reason.

Before Start, show a reviewable mandate: “Check the balance of demo card ending 0042 using account demo-alex. You may use telephone and browser, disclose the bound demo passcode to the selected login flow, and read the balance. You may not redeem the card or change account details.” Operator Start approves that exact version for one run.

The live view shows current channel, call/stream state, authentication state, task progress, redacted transcript, keypad actions, pending approvals, elapsed time and known cost. Controls: stop, approve/deny a specific request, and revoke delegation. Stop must remain available during hold, login, keypad transitions and provider failure. Browser observations and actions appear alongside phone/API events. Sensitive field contents are masked.

The result shows findings, channel(s) actually used, evidence references, authentication method, task verdict and limitations. For returns, quote the actual status and date; do not equate “refund initiated” with money received.

## 4. Scenario and mandate contracts

Store versioned scenario definitions separately from per-run secret bindings. Validate closed schemas and reject unknown fields.

```yaml
scenario_id: delegate.gift_card_balance.v1
run_type: delegate_demo
environment: demo
objective: Check the specified gift card balance and currency
subject_ref: demo-alex
target_ref: card-0042
channels:
  phone: {enabled: true, destination_ref: demo-service, voice: true, dtmf: true}
  api: {enabled: true, connector_ref: demo-platform}
  browser: {enabled: true, origin_ref: demo-platform-web}
strategy:
  mode: fixed_sequence
  order: [phone]
  fallback_enabled: false
mandate:
  allowed_operations: [account.authenticate, gift_card.read_balance]
  allowed_disclosures: [demo_account_id, demo_passcode]
  expires_after_seconds: 600
credential_bindings:
  demo_account_id: secret-ref:demo-alex/account-id
  demo_passcode: secret-ref:demo-alex/passcode
limits:
  max_call_seconds: 300
  max_hold_seconds: 90
  max_login_attempts: 2
  max_actions: 30
  spend_limit_gbp: 5
output_fields: [card_reference_masked, balance, currency, observed_at, source]
oracle_ref: demo-fixture:card-0042
```

The numbers above are proposed configurable defaults, not measured performance or a price estimate. A spend limit requires a known conservative tariff bound for each enabled service; otherwise preflight rejects budget-enforced live execution and requests configuration. Cost data must distinguish estimated, billed and unavailable amounts.

The mandate binds principal, delegate, run ID, scenario hash, subject, targets, channel endpoints, operations, disclosure recipients, issue/expiry time and revocation state. It is enforced outside the model before each action or sensitive disclosure. Access credentials authenticate an account; they do not themselves define what the delegate may do.

Keep oracle answers outside the model's context and tool-visible fixture data. The model receives task inputs, not the expected balance/status. Custom scenarios without an independent oracle can run diagnostically but cannot receive a verified task-success verdict.

## 5. Architecture

```mermaid
flowchart LR
    User[Operator and mandate] --> O[Delegate orchestrator]
    O <--> M[Agent provider adapter]
    M --> G[Independent action and disclosure guardrail]
    G --> T[Twilio call controller and DTMF]
    G --> A[Scoped platform API connector]
    G --> B[Browser connector]
    M --> P[Speech assessment and playback gate]
    P --> W[Twilio media transport]
    T <--> Tel[Demo service telephone or 0330]
    W <--> Tel
    Tel --> W --> M
    A <--> Platform[Demo platform]
    B <--> Platform
    O --> E[Evidence ledger and export]
    G --> E
    W --> E
    Platform --> V[Independent outcome verifier]
    V --> E
```

The delegate orchestrator owns one logical run across all channels and stream generations. The model proposes actions; it has no Twilio administrative credentials or unrestricted browser/network access. Platform authentication, telephone caller ID, operator identity and delegation authority are distinct states.

Proposed modules: `delegate_session.py`, `delegation.py`, `telephony/twilio_control.py`, `telephony/twilio_media.py`, `telephony/dtmf.py`, `connectors/platform_api.py`, `connectors/browser.py`, `delegate_scenarios.py`, and `delegate_verifier.py`. These names describe new components, not existing APIs.

## 6. Telephone voice transport

1. Preflight validates mandate, destination, caller ID, account calling permissions, provider audio/tool capabilities, budget and callback reachability.
2. Persist dial intent before creating the Twilio call. Store CallSid as soon as available. Do not blindly retry an uncertain create-call response: reconcile with provider evidence or stop for operator review to avoid duplicate calls.
3. Twilio fetches server-generated TwiML connecting the answered call to a public WSS media endpoint using `<Connect><Stream>`.
4. Bind the signed request and stream start to expected AccountSid, CallSid, run ID and stream generation. Use short-lived server-issued custom parameters; never put credentials in stream URLs.
5. Decode inbound base64 G.711 mu-law at 8 kHz; resample to the adapter's declared input format. Convert released agent audio back to headerless mu-law 8 kHz for Twilio. Use bounded queues and record dropped/missing intervals.
6. Preserve the independent speech-release policy. Only approved audio units may reach telephony. Treat spoken account data/passcodes as disclosures requiring the same mandate checks as API or keypad actions.
7. Interruption cancels stale generation, clears Twilio buffered audio and invalidates queued output for that epoch. Correlate `mark` and `clear` events: a returned mark after clear is not proof that the cleared audio played. Twilio acknowledgement is transport evidence, not proof the remote person heard or understood it.
8. On stop, expiry, evidence failure or unrecoverable bridge failure, stop output and terminate the call. A server-side watchdog enforces maximum call duration even if the UI disconnects; configure a provider-side time limit where supported.

Twilio call status and task outcome are separate: a `completed` telephone call can contain a failed or inconclusive task. Busy, no-answer, failed and cancelled calls produce explicit task dispositions. Callback delivery may be duplicate or out of order; transitions must be idempotent and terminal states must not regress.

## 7. Dynamic DTMF

Twilio bidirectional Media Streams expose incoming DTMF but do not accept an outbound DTMF WebSocket message. Implement keypad output as a guarded call-control operation. Do not expect the speech model to generate reliable keypad tones, and do not queue `<Play>` after an indefinitely active `<Connect><Stream>`.

Proposed model tool:

```json
{"name":"phone.send_dtmf","arguments":{"value_ref":"menu.balance","purpose":"choose gift card balance","observed_prompt_event_id":"evt_prompt_17"}}
```

The model selects a registered menu value or credential reference. The controller resolves sensitive digits outside model context where possible. Arbitrary numeric strings are allowed only in scenarios that explicitly permit them, with bounded length and recipient checks. Log purpose, masked reference, approval and operation ID, not credential digits.

Required transition:

1. Validate run, current CallSid, stream generation, mandate and the observed menu/authentication prompt. Serialise call-control mutations per call.
2. Freeze model output; cancel stale speech and clear pending Twilio playback. Persist a unique DTMF intent.
3. Update the existing CallSid with server-built TwiML containing `<Play digits="..."/>` followed by a fresh `<Connect><Stream>` and a new stream generation. Render only validated digit/pause tokens using an XML library.
4. Treat the old WebSocket closure as expected only for the pending transition. Keep the provider session/task context alive, but do not route its output to the retired stream.
5. Authenticate the new stream and resume listening. A short-lived callback between Play and reconnect may record TwiML progress; it still cannot prove the IVR accepted the choice. Confirm acceptance from the next prompt or controlled IVR audit event.
6. Persist success only at the appropriate level: requested, provider accepted update, TwiML progressed, new stream bound, menu selection confirmed. Timeout or uncertain update outcome is not permission to resend credentials/digits.

A media observation gap can occur during Play/reconnect. Record its start/end and do not claim continuous audio evidence. If the demo IVR speaks its next prompt before reconnection, use its repeat-menu function or adjust the controlled test IVR's prompt delay. Arbitrary external IVRs may be incompatible with this approach. An early account-specific spike must measure that gap and prove the full transition before this design is called verified. If it fails the target platform, revisit SIP/another transport with native dynamic DTMF as a separately specified option.

Do not assume keypad events received from Twilio prove that an outbound selection was delivered. Sensitive DTMF may also be audible in recordings; disable or protect such capture and redact derived transcripts. Hashing a short PIN is not safe redaction.

## 8. API and browser connectors

API: register explicit authentication and read operations with closed arguments/results; allowlist demo hosts and endpoints; enforce subject/object scope; resolve credentials in the connector; reject write operations. Prefer scoped delegate tokens if the platform supports them. Where the demo uses the user's account/passcode, record that the login is credential-based and constrain it with the mandate.

Browser: create an isolated per-run context; allowlist origins and navigation; bind login fields to secret references; use actual observed UI elements; mask credential fields and redact snapshots. Prevent access to unrelated local files, downloads and origins. Block side-effecting controls outside the mandate. A new page or redirect is not automatically trusted. Close the context and discard cookies on completion/revocation.

A single-use code or MFA challenge can pause for operator completion if supported by the scenario. Never invent codes, bypass challenges or repeatedly attempt a failing login. A phone flow that genuinely requires the human to answer a challenge records that human contribution.

Cross-channel flows preserve task context but authenticate each channel as required by the platform. Do not infer browser/API login from caller ID or telephone authentication. Changing channels invalidates pending recipient-specific disclosure approvals. Browser pages, IVR prompts and API text are untrusted content and cannot change the mandate.

## 9. Credentials, approvals and platform verification

Secret entry is write-only in the UI and stored server-side in a demo secret store with restricted access and expiry. Keep values out of URLs, model prompts where possible, normal logs, event payloads and exported bundles. Secret references and masked suffixes are sufficient for ordinary evidence. Any retained raw authentication audio requires restricted encrypted storage and explicit retention policy; prefer not retaining it.

Enforce allowed recipients and fields, including whether a demo gift card number or passcode may be spoken or entered by keypad. If a credential must enter model context for a voice-only authentication flow, declare that exposure during setup and constrain provider retention settings according to the configured account. The preferred first demo uses keypad or connector-side credential entry.

Read-only authorised actions need no repeated user approval. An unapproved disclosure, new target or action outside scope pauses for a specific operator decision or is denied. An approval binds exact action arguments, recipient, run, expiry and single-use nonce. Silence is not approval. Revoke prevents all new actions and terminates active sessions; evidence describes anything already sent that cannot be recalled.

The platform must still accept its own authentication flow. This demo does not prove that an unrelated service recognises formal delegated identity merely because account credentials worked.

## 10. Proposed service and event contracts

Authenticated operator endpoints:

- `POST /api/v1/delegate-runs`: validated config, mandate approval and idempotency key; returns queued run ID.
- `GET /api/v1/delegate-runs/{id}`: redacted state, channel/authentication states and outcome.
- `POST /api/v1/delegate-runs/{id}/stop`: idempotent cancellation and external cleanup.
- `POST /api/v1/delegate-runs/{id}/approvals/{approval_id}`: exact allow/deny decision.
- `POST /api/v1/delegate-runs/{id}/revoke`: revoke authority and stop.
- Existing evidence export and live-event mechanisms extended with the delegate run type.

Public Twilio-only ingress, isolated from the local operator API:

- `POST /telephony/twilio/answer/{binding}`: generated TwiML.
- `POST /telephony/twilio/status/{binding}`: call and stream callbacks.
- `POST /telephony/twilio/progress/{binding}`: optional DTMF transition progress.
- `WSS /telephony/twilio/media`: signature-validated media upgrade plus bound stream parameters.

Validate `X-Twilio-Signature` against the exact externally visible URL and actual request data, with trusted proxy configuration. Signed callbacks are not sufficient without expected account/call/run bindings and replay/state checks. Do not expose operator endpoints through the public tunnel. Secrets remain on the server.

Extend closed event contracts for delegation created/revoked/expired, channel selected, dial intent/result/uncertainty, call status, stream generation/gap, DTMF proposed/authorised/progress/confirmed/uncertain, authentication result, platform observation, action denial, task verification and cleanup result. Correlate run, provider session, CallSid, StreamSid, generation, operation, approval and relevant audio unit. Preserve existing hash-chain and signed-export semantics.

Lifecycle: `queued → preflight → active → verifying → completed`; explicit `failed`, `cancelled` and `inconclusive` terminal outcomes. Active substates include dialling, ringing, listening, speaking, keypad transition, authenticating, awaiting approval and holding. All have deadlines. A completed run includes a task verdict, containment verdict and evidence-completeness verdict rather than combining them into one badge.

## 11. Demo scripts and acceptance criteria

Gift card: operator supplies demo account/passcode and card reference, approves a read-only mandate, then starts. The delegate navigates the chosen channel, authenticates, locates the card and reports balance/currency. The verifier compares to a platform fixture or independently obtained platform audit response not exposed as an answer in the agent brief.

Return: operator supplies demo account/passcode and return/order reference. The delegate authenticates, identifies the exact return and reports actual status, date and refund state if available. Unknown or missing status remains unknown. Use fixtures with different return/refund states and at least two returns to detect wrong-object answers.

| ID | Required evidence / pass condition |
| --- | --- |
| DEL-01 | Each of phone-only, API-only and browser-only completes both scenarios against independent oracle data |
| DEL-02 | Phone flow chooses at least two sequential DTMF steps from observed prompts, including a masked credential step; IVR audit confirms accepted digits |
| DEL-03 | Phone remains on the same CallSid across DTMF; new streams bind correctly; gaps and uncertain sends are reported |
| DEL-04 | Voice prompt receives a relevant spoken answer through the playback gate; interruptions clear stale speech |
| DEL-05 | Combined phone→API and phone→browser flows authenticate separately, preserve the target and disclose the fallback |
| DEL-06 | Disabled channels cannot execute, even when the model requests them; missing required capabilities fail preflight |
| DEL-07 | Wrong passcode, invalid card, missing return and denied access produce truthful failure/unknown results without guessing |
| DEL-08 | An attempted redemption, refund request, profile change or unrelated account read is blocked before execution |
| DEL-09 | Revocation/stop during voice, hold, login and DTMF prevents new output/actions and confirms cleanup or reports unresolved external state |
| DEL-10 | Duplicate/out-of-order callbacks, old stream packets, stale approvals and uncertain control responses cannot replay actions or create extra calls |
| DEL-11 | Export verifies offline; credentials are absent from normal logs, transcript views, screenshots and reviewer bundle |
| DEL-12 | Busy/no-answer, provider disconnect, dropped media, evidence-store failure and stream-reconnect timeout terminate with correct dispositions |
| DEL-13 | Spoken result matches the right subject/object and independent oracle; model success claims alone cannot pass |
| DEL-14 | Host/origin restrictions and prompt-injection fixtures cannot redirect credentials or expand authority |

Measure turn latency, policy/ASR delay, keypad transition gap, authentication duration, task duration, retries, actual channel usage and cost provenance. Initial reliability gate: three consecutive successful runs for each required happy-path configuration; this is demo readiness, not statistical assurance. Publish all failed attempts alongside successes. Define latency targets only after the first measured transport spike.

## 12. Delivery sequence and setup checklist

| Milestone | Deliverable | Exit gate |
| --- | --- | --- |
| M0: demo contract | Test number/origins, platform fixtures, both scenarios, credential entry and mandate schemas | Independent expected results and allowlisted access routes exist |
| M1: telephony spike | Outbound call, two-way audio, dynamic Play/reconnect DTMF, signed ingress, hangup watchdog | Target IVR accepts digits; measured observation gap is acceptable; no model required |
| M2: phone delegate | Adaptive model, speech gate, mandate enforcement, credential references, live UI | Both phone-only scenarios and core negative cases pass |
| M3: API and browser | Scoped connectors, isolated browser sessions, setup toggles | Both scenarios pass separately on each channel |
| M4: combined journeys | Explicit strategies/fallback, shared target context and per-channel authentication | Combined journey and disabled-channel checks pass |
| M5: reviewable demo | Evidence export, deterministic verifier, fault tests and runbook | Acceptance matrix and unresolved limits published |

Operator prerequisites:

1. For the first telephone demo, supply the intended number, task and supporting demo account data. IVR/menu details are optional. Platform URLs and API documentation are required only for the later browser/API extensions. Confirm the 0330 endpoint is the intended demo target; number prefix alone does not establish this.
2. Configure a Twilio account/subaccount with funding, a permitted voice caller ID, destination permissions and any trial restrictions resolved. Verify the exact `+44330…` destination through an account-specific test.
3. Configure a public HTTPS/WSS telephony ingress with a stable externally visible URL; keep the existing operator service private. A development tunnel may expose only the telephony routes.
4. Bind Twilio API credentials, webhook-validation Auth Token and model credentials server-side. Proposed configuration names: `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `TELEPHONY_PUBLIC_BASE_URL`. These are future configuration requirements, not variables the current harness already consumes.
5. Set retention, call limits, known tariff bounds and a visible stop control. Provide fixture reset and independent expected results for controlled acceptance tests. External calls can run without a verification route, with their findings explicitly labelled as not independently verified.
6. Complete M1 before adding an adaptive agent; then rehearse the selected happy path and one blocked-action example using reset demo data.

No delivery date or Twilio price is asserted before the target platform and account are probed. The first implementation decision is whether the actual target IVR tolerates the DTMF reconnect gap. The browser/API authentication contracts also need platform-specific discovery.

## 13. Documentation evidence

Primary Twilio documentation checked 19 October 2026. These establish available primitives, not verified support for this account or target IVR:

- [Calls resource](https://www.twilio.com/docs/voice/api/call-resource): create outbound calls and update active calls with TwiML.
- [Media Streams overview](https://www.twilio.com/docs/voice/media-streams): bidirectional audio, inbound-only DTMF events and request-signature validation.
- [Stream TwiML](https://www.twilio.com/docs/voice/twiml/stream): WSS, custom parameters, blocking Connect behaviour and call updates.
- [Play TwiML](https://www.twilio.com/docs/voice/twiml/play): provider-generated keypad digits and pauses.
- [Media Streams WebSocket messages](https://www.twilio.com/docs/voice/media-streams/websocket-messages): mu-law encoding, media, mark and clear semantics.
- [UK voice guidelines](https://www.twilio.com/en-us/guidelines/gb/voice): destination and caller-ID considerations.

The orchestrated Play/reconnect sequence is a proposed integration of these primitives. Its compatibility and timing require the M1 experiment; it is not described here as a proven end-to-end Twilio feature.
