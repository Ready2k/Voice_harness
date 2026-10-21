# Adaptive, goal-based telephone delegate — scope and implementation status

User clarification, 21 October 2026: the delegate must pursue a goal and adapt to the conversation without asking the operator to prescribe each answer. Sharing the present recordings does not establish that this behaviour is implemented.

## Implementation update

An opt-in adaptive demo path is now implemented through `--brief`; see the [runbook](DELEGATE_PHONE_RUNBOOK.md#natural-goal-based-wallet-demo) and [worried wallet brief](../scenarios/delegate/lost-wallet-worried.json). It replaces phrase selection with natural generated text, separate semantic review, style-directed TTS and independent ASR before release. It tracks cards mentioned in acknowledged speech, lets the model choose ordinary question order and withholds stale/replayed replies. It requires both target cards plus a source-supported, reviewed summary before accepting a model-reported result.

The new brief uses the operator's revised facts: Barclaycard credit card, Barclays current-account debit card and Tesco Clubcard; the wallet might be at work or genuinely missing. These are demo scenario facts, not a claim about a real lost wallet. The caller should sound worried and uncertain, without inventing personal information or events.

Offline tests cover the enforcement path using injected reply/reviewer doubles, including free-form replies, card ordering, remaining items, unavailable/denying review, ASR mismatch, stale assessment, replay, protected-action prompts and result evidence. They do not prove live model behaviour or vocal naturalness. A recorded live evaluation with varied questions remains outstanding. Dynamic authentication/keypad values, protected-action approval, persistent per-card resolution, pause/resume, and a setup UI remain outside this implementation. No new live call was placed for this change.

## Limitation observed in the earlier recordings

The recorded transport pilot proves outbound calls, two-way audio and guarded keypad navigation. Its model chose from a fixed phrase list. Adding each new answer as a CLI option, repeating a product description, or escalating ordinary preferences to the operator is not the intended agent experience. That recording is a diagnostic transport experiment, not evidence of the new adaptive path.

The latest recordings show that the service understood the wallet and Premier/business banking description, then asked which card to sort first. A goal-based delegate with authority over routine conversational choices should choose an order, explain it if useful, and continue toward handling both cards. It should not require an operator preference for that routine ordering decision.

## Run brief and authority

Keep the operator input simple: number, goal/scenario, supporting facts and permitted outcomes. Before the run, establish:

- Goal and observable completion criteria, including all target cards/accounts/items.
- Supplied facts, identity/credential references and facts genuinely unknown.
- Delegated choices: ordinary question order, which item to discuss first, clarification, retry and summarisation. These should normally be enabled.
- Permitted actions and material boundaries. Routine dialogue needs no further approval; a real protected action outside the mandate still does.
- Demo-data policy: use supplied synthetic data; generate a demo persona only when that mode is explicitly enabled and label generated facts as synthetic. Never invent facts about the actual user or fabricate authentication credentials.
- Time, retry and spending limits; stop and escalation conditions.

For the wallet example, the brief includes the lost wallet, the supplied Premier/business banking description, confirmation that the Barclays card was included, the test routing values and the desired demo outcome. A routine order choice does not alter the goal. Merely mentioning a card does not authorise a real card/account change; demo action permissions belong in the run brief.

## Runtime behaviour

1. Maintain task state: facts learned, pending questions, items covered, channel state, permitted next actions and unresolved requirements.
2. Listen for complete remote turns; coalesce partial transcripts, ignore acknowledgements and handle interruption without inventing what was heard.
3. Generate context-sensitive responses from the goal, supplied/observed facts and delegated choices. Replace per-answer phrase flags with a general response path.
4. Keep action and disclosure checks outside the model. Dynamic DTMF remains bound to an allowed recipient and purpose; it is not restricted to a particular conversation script.
5. Preserve independent speech assessment before release. Apply a task/mandate-aware policy to generated speech instead of equating safety with membership in a fixed phrase list. Do not reuse banking-agent identity rules with delegate roles reversed.
6. Continue through routine branches autonomously. If asked which of two authorised items to handle first, choose and track the other item for later.
7. Ask the operator only for genuinely missing authentication/account facts, conflicting objectives, or a material action beyond the mandate. Explain the exact missing input. An ordinary menu preference is not an escalation reason.
8. Detect loops, failed handoffs, retries, service refusal and completion. Report partial progress accurately rather than treating phone completion as task success.

If an operator response is necessary, prefer a bounded pause/resume mechanism when the remote service tolerates it; expose the pending question without publishing an unauthenticated control endpoint. Otherwise end cleanly and preserve enough state for an explicit retry. Do not describe a terminated call as paused.

## Acceptance before calling it adaptive

- With a previously unseen wording of a known question, produce an appropriate response without changing code or adding a phrase flag.
- Given both card types, handle “Which first?” without contacting the operator, within the mandate.
- Keep the second item on the task list after choosing the first.
- Complete multi-step voice and keypad journeys under the configured limits, including clarification and a failed service handoff.
- Distinguish a routine delegated choice from missing credentials and from an unauthorised protected action.
- Never claim to know unsupported personal details; preserve the synthetic provenance of generated demo facts.
- Bind generated, assessed, released and actually sent audio to the evidence ledger, with accurate interruption/gap dispositions.
- Repeat the scenario with varied remote questions and publish failures as well as successes. A recording of fixed phrases alone cannot satisfy these checks.

## Current evidence and delivery boundary

The [shareable recording package](../reports/recordings/lost-wallet-2026-10-21/README.md) contains both audio tracks, a playback page, independent remote transcript, local manifest and event ledger. Its events demonstrate the transport and the old fixed-phrase limitation. The implementation update above does not change that historical evidence or mark the complete acceptance list, generalised mandate or pause/resume facility complete.
