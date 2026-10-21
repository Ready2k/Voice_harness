# Adaptive, goal-based telephone delegate — next implementation scope

User clarification, 21 October 2026: the delegate must pursue a goal and adapt to the conversation without asking the operator to prescribe each answer. Sharing the present recordings does not establish that this behaviour is implemented.

## Current limitation

The transport pilot proves outbound calls, two-way audio and guarded keypad navigation. Its model chooses from a fixed phrase list. Adding each new answer as a CLI option, repeating a product description, or escalating ordinary preferences to the operator is not the intended agent experience. It is a diagnostic transport experiment, not the finished delegate.

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

The [shareable recording package](../reports/recordings/lost-wallet-2026-10-21/README.md) contains both audio tracks, a playback page, independent remote transcript, local manifest and event ledger. Its events demonstrate the transport and the fixed-phrase limitation. This document records the next implementation requirement; it does not mark the adaptive response path, generalised mandate or pause/resume facility complete.
