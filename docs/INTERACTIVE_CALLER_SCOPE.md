# Fixed and live AI callers — proposed scope

Status: interactive pilot implemented, 19 October 2026. Later phases below remain proposed.

## Using the pilot

In Runs → New evaluation, choose **Balance after verification · clean studio**, a target provider, and **Live AI caller**. Select calm, anxious or frustrated behaviour, a voice, and turn/duration bounds. Enable **Retain audio in evidence** to review both speakers using **Load caller conversation recordings** in the run detail. The caller uses a separate OpenAI Realtime session, regardless of the target provider. Requires `OPENAI_API_KEY` and `VOICE_HARNESS_LIVE_ASR=openai`; live caller mode does not require macOS speech synthesis.

The live customer defaults to **Cedar (male)**. The OpenAI agent explicitly uses **Marin**; if the customer is changed to Marin, the agent uses Coral to keep the two speakers distinct. Both requested voice choices are bound to the run evidence. Existing runs retain their recorded voice configuration.

Fixed caller remains the default. Its voice can be the server default, macOS speech, or OpenAI TTS. The existing human microphone flow remains on Manual test. Other scenarios explicitly reject live AI caller mode during this pilot.

Example authenticated `POST /api/v1/runs` body (also supply an `Idempotency-Key` header):

```json
{
  "scenario_id": "happy-balance-clean-001",
  "provider_config_id": "openai-realtime-live",
  "retain_audio": true,
  "caller": {
    "mode": "live_ai",
    "persona": "anxious",
    "voice": "cedar",
    "max_turns": 6,
    "max_duration_seconds": 120
  }
}
```

Implemented: adaptive caller audio, versioned brief hashes, independent target assessment before delivery, separate caller usage, retained clips under existing retention controls, operator cancellation of both sessions, duration/turn limits, caller error classification and comparison warnings. Duration includes setup; session cleanup may take up to ten additional seconds. Monetary budgets and natural barge-in are not implemented. Caller fidelity is explicitly `not_independently_assessed`; caller transcripts are provider-reported. Missing or blocked target audio is never forwarded to the caller. A released, independently transcribed balance matching the trusted summary stops the conversation; independent rubric results still determine the target verdict.

The target adapters retain their existing capability-report bindings. The caller role is an experimental integration, not a newly verified provider capability. Dialogue and latency must be validated in live trials; offline tests establish orchestration mechanics.

The initial live trial on 19 October was blocked by OpenAI's `credit_balance_exhausted` response before any conversation audio was generated. Voice quality remains unverified. Browser visual inspection was also blocked by the browser permission gate; browser logic and WAV construction are covered by automated tests. Restart the host server with `make serve-live` to load the implementation.

On 20 October, after credit was restored, the OpenAI-to-OpenAI pilot completed verification and delivered the correct spoken £1,234.56 balance. Both sides' five audio clips were retained, caller/target/ASR usage was recorded separately, and the signed bundle verified. See `artifacts/interactive-caller-pilot-2026-10-20.json` and the accompanying demo WAV. The WAV concatenates recorded turns with short gaps; generation and assessment waits are excluded. Naturalness still requires listening review; this is one functional sample, not a quality benchmark or validation of the other target providers.

Live testing exposed two timing issues, now fixed: response continuations must remain attached to their originating response while ASR runs asynchronously, and natural pauses must not split an AI caller's utterance. The pilot explicitly commits complete caller turns to OpenAI, xAI and Gemini. Nova uses continuous audio with patient automatic endpointing (`LOW`); this difference is recorded as `provider_vad_low`, never claimed as manual control. The end condition requires a released, independently transcribed balance matching the trusted summary result; a spoken acknowledgement after a tool call is insufficient.

Validation after these changes: the full local suite ran 664 tests successfully (8 skipped); all 67 targeted PostgreSQL caller/scenario/concurrency tests, 79 browser logic tests, contract consistency checks, Ruff and Bandit checks passed.

## Four-provider listening samples

All four target providers were exercised on 20 October with the same Cedar customer voice, anxious persona, clean balance scenario and fixture seed 42. OpenAI, xAI and Gemini delivered the balance. Nova verified the caller, but its final reply was withheld with `UNSUPPORTED_CURRENCY` from the independent assessment. Its earlier released replies are retained. All four evidence bundles verified.

Open `artifacts/voice-provider-listening-2026-10-20/listen.html` for the portable listening report, or the four WAV files beside it. Recorded turns are joined with short gaps; assessment/generation waits are excluded. Transcripts show customer-provider text and independently assessed released agent text. These are single adaptive samples with different dialogue wording, not provider rankings.

To repeat the batch (incurs voice-provider and ASR usage):

```bash
PYTHONPATH=src .venv/bin/python scripts/interactive_voice_samples.py \
  --output artifacts/voice-provider-listening-2026-10-20 \
  --data-dir .local/voice-provider-listening-2026-10-20
```

The runner loads local `.env` settings without printing them, preserves signed bundles in the data directory, and writes the report after each completed provider. Nova uses its existing AWS session file; that session must be current. Its patient automatic endpointing follows the [AWS turn-taking controls](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-turn-taking.html).

Validation: final PostgreSQL container suite ran 661 tests successfully (6 skipped); all 78 browser logic tests passed; generated-contract consistency, Ruff and Bandit checks passed. The caller-specific tests cover released-audio isolation, adaptive exchange, recording, separate usage, provider/credit failures, duration limits and cancellation. OpenAI adapter regressions cover disconnect and exhausted credit during setup. Default fixed configurations preserve their previous serialized form and idempotency hashes.

## Product outcome

An operator can run the same supported scenario with either a **Fixed caller** (scripted speech) or a **Live AI caller** (a simulated customer who listens and responds). Voice quality and caller behaviour are separate settings. A fixed caller can use a natural AI voice; live means adaptive behaviour, not microphone input or merely a live provider connection.

Keep the existing human microphone path labelled **Human caller**. Existing configurations continue to select fixed behaviour by default.

| Setting | Fixed caller | Live AI caller |
| --- | --- | --- |
| What to say | Versioned scenario lines | Generated from persona, facts and objective |
| Response to the tested agent | Continues the defined script | Responds to what it actually hears |
| Voice | Existing synthesiser selection | Caller model and voice selection |
| Primary use | Regression and controlled comparisons | Adaptive conversation and behaviour exploration |
| Repeatability | Fixed text; retain audio for exact input replay | Variable dialogue; retain evidence and run repetitions |

## Operator experience

Run setup presents scenario, agent under test, and caller mode. Fixed mode shows voice selection and script preview. Live AI mode shows caller provider/model/voice, persona and a plain-language preview of the caller brief. Show estimated budget only where supported by established prices; otherwise say unavailable.

The live view identifies both speakers, shows audio/transcript progress, elapsed time, caller state and stop reason, and offers stop. Results and exports always label caller mode, voice/model, scenario version and delivery mode. Comparisons default to matching caller mode and warn when configurations differ.

## Caller brief

Add a separately versioned caller profile bound to a scenario. Keep authoritative evaluation expectations outside the caller prompt.

- Objective and opening situation.
- Synthetic facts the caller knows, facts it may reveal only when asked, and facts it does not know.
- Persona, language, emotional starting point, patience and communication style.
- Behaviour rules: clarification, correction, hesitation and response to reassurance or repeated questions.
- Permitted variations and forbidden departures from the scenario.
- Completion, handoff, maximum turns, wall-clock duration, silence and spend limits.

Use a small set of curated profiles initially: calm/cooperative, anxious/uncertain, frustrated/impatient. Treat moods as bounded behaviour, not a promise of human realism. Do not add random fillers as a substitute for listening and relevant replies.

## Runtime design

Introduce a caller-session boundary for fixed and interactive implementations. Its responsibilities are to start, receive delivered agent audio, produce caller audio/events, handle interruption, stop and close usage. Keep provider-specific events behind adapters.

The orchestrator owns two distinct sessions: caller and agent under test. Route caller audio through existing input profiles to the tested agent. Route only the tested agent's delivered audio to the caller. Preserve real-time pacing and bounded queues, avoid feeding either speaker its own output, and cancel both sessions on stop or fatal failure. Tool access, fixtures, guardrails and scoring remain harness-owned; the caller gets no bank tools, hidden tool results or evaluator answers.

Reuse the current target-provider adapters, assessment path and audio transformations where practical. A target adapter's existence does not establish caller-role support; validate role instructions, session lifecycle and metering separately.

### Delivery and interruption

Default to the existing gated delivery: whole-response assessment precedes release. Feed released PCM to the caller at playback pace and record the actual delivered position. This adds assessment delay and limits how spontaneous the conversation feels.

The existing human session also supports streamed delivery with post-hoc assessment. Any future AI caller use of that mode must preserve its explicit unassessed-delivery evidence and existing AUDIO-01 restrictions. Do not silently change delivery mode to achieve lower latency.

Begin with alternating turns. A later phase adds controlled barge-in during delivered audio, playback-position accounting, cancellation and target-history truncation. Distinguish interruption of audible playback from cancellation of generation that was still held.

## Evidence and assessment

Bind caller mode, provider/model/voice, profile and prompt versions/hashes, scenario hash, delivery mode, limits and audio transformations to each run. Record caller and target usage separately, plus turns, delivered-audio timing, interruptions, errors and termination reason.

Retain both sides' audio and transcripts according to the existing retention policy, with explicit omissions and integrity hashes. Current scripted input evidence records hashes without retaining caller PCM; recording review therefore needs deliberate artifact storage work. Exact input replay requires retained PCM and timing; it does not guarantee an identical target response.

Keep caller fidelity separate from target quality. Detect scenario departures, fabricated caller facts, looping, silence and premature endings. A caller failure makes affected criteria invalid or untestable with a reason; it is not automatically a target failure. Target policy violations with sufficient independent evidence remain reportable. The caller's declaration that its goal succeeded is not a verdict.

Preserve deterministic policy checks and independent ASR. Adapt checks tied to fixed turn indexes or exact tool sequences before declaring a scenario compatible. Bind trusted confirmations to the observed utterance and action, never to an assumed script position. Measure harness hold delay separately from provider and conversational latency.

Live-mode provider comparisons need repeated runs with matched profiles and visible sample counts; generated dialogues are not identical stimuli. Report results by mode. Establish repeat counts after a pilot measures variability.

## Delivery phases

1. **Fixed-mode foundation:** explicit caller-mode configuration/API/UI; preserve defaults; allow natural TTS selection per run; record complete caller configuration. Existing runs continue to work.
2. **Interactive pilot:** one caller provider behind the new boundary; one compatible, multi-turn synthetic banking scenario; curated profiles; gated alternating turns; recording, limits, separate usage, stop controls and caller-fidelity outcomes. Start with a balance enquiry that can clarify and complete verification. Label unsupported scenarios.
3. **Conversational behaviour:** playback-aware barge-in, bounded silence/repetition recovery and broader scenario support. Consider explicitly labelled streamed delivery only as a separate choice.
4. **Evaluation breadth:** repeated-run summaries, retained-input replay, more caller providers and validated cross-provider combinations.

## Acceptance criteria for the pilot

- The operator selects fixed or live AI mode without changing the agent under test.
- Fixed mode preserves the script; the live caller gives relevant different replies when the target asks different follow-up questions.
- The caller uses only its brief and delivered conversation, and cannot see unreleased speech or hidden evaluator state.
- Both speakers' retained audio can be reviewed with aligned transcripts and explicit evidence gaps.
- Duration/turn/budget bounds and stop controls close both sessions; unavailable pricing does not masquerade as a working monetary cap. Use hard duration/token bounds where spend cannot be measured reliably.
- Caller failure, target failure and infrastructure failure are distinguishable; there is no silent fallback to scripted mode.
- Existing guardrail and trusted-confirmation guarantees remain intact; live mode does not claim exact reproducibility.
- Listening review confirms natural voice delivery, responsive clarification and coherent persona over several dialogues. Establish measurable latency thresholds from the pilot before claiming conversational performance.

## Implementation touchpoints

- `caller_voice.py`: retain fixed synthesis; move selection toward per-run configuration.
- `live_scenario.py`: currently synthesises `turn["customer"]` sequentially; extract caller orchestration while preserving fault and assessment evidence.
- `live_providers.py`: caller availability, creation, metadata and lifecycle independent of target selection.
- `manual_session.py`: reuse concepts for delivered playback, acknowledgements and interruption; do not equate an AI caller with a browser microphone.
- `scenario_defs.py` and catalogue: versioned interactive profiles and explicit compatibility; audit turn-based checks and confirmations.
- API, run setup, event contracts, recording, exports and cost recorder: mode selection and evidence for two model sessions.

## Boundaries

The initial scope excludes real customer data, voice cloning, telephone dialling, automatic conversion of every existing scenario, and changes to the harness's speech-release policy. Choose the pilot caller model/voice through a short listening and latency trial; provider support and access must be checked at implementation time. This proposal makes no delivery-date or cost estimate before that trial.
