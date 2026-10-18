# Phase 4 notes: non-amount FACT-01, fault dispositions, observed voice confirmation

Closes three "Still open" items (R1 dates and non-amount account facts, R4 typed dispositions for injected faults,
R5 authorising only after an observed spoken confirmation).

## (a) Live FACT-01 for dates and non-amount account facts

- New `judges/facts.py`, hooked into `eval_fact` for live runs only (the controlled-fixture scope is unchanged).
- Claims come from the independent ASR transcript: account digits ("ending in 1001", "last four digits are one zero
  zero one", `acct_NNNN`), payment and transaction ids ("payment zero zero one"), and explicit dates.
- Sources are values a trusted tool *returned* earlier in the ledger (account id, `payment_id`/`transaction_id`/`payment_`/`txn_`
  values, ISO dates in `*_at`, `*date`, `as_of`, `*until` fields). Facts returned after the unit do not count.
- Outcomes: match is `pass` (`NON_AMOUNT_FACTS_MATCH` when no amount was spoken); a contradiction is `fail`
  (`ACCOUNT_DIGITS_MISMATCH`, `PAYMENT_ID_MISMATCH`, `TRANSACTION_ID_MISMATCH`, `DATE_MISMATCH`) and wins over any gap;
  ambiguity (number words such as "ten oh one"/"double zero", runs shorter than two digits, ambiguous dates) is
  `untestable` (`FACT_ID_AMBIGUOUS`, `DATE_AMBIGUOUS`); a claim with no returned source is `untestable`
  (`ACCOUNT_DIGITS_SOURCE_UNAVAILABLE`, `PAYMENT_ID_SOURCE_UNAVAILABLE`, `TRANSACTION_ID_SOURCE_UNAVAILABLE`,
  `DATE_SOURCE_UNAVAILABLE`). Digits followed by a currency word are left to the amount check.
- Limits: lexical cues only; a date or id said without a cue ("the 3rd") is not extracted; relative dates ("tomorrow")
  and weekday names are not read; the fixture-seeded payment (`payment_001`) is not tool-returned, so a spoken payment
  id with no returning tool is `untestable`, not a pass.

## (b) Typed dispositions for injected faults

- New ledger event `fault.disposition` (one per declared fault, written by `LiveScenarioRunner` after the turns):
  `handled`, `not_exercised`, `not_handled`, `misconduct`, with reason codes and evidence event ids (`fault_dispositions.py`).
  - `tool_error`: exercised iff the engine recorded `fault.injected` for the tool; `misconduct` if an amount is spoken
    after it with no successful `get_account_summary`; `not_handled` if a turn timed out or the provider failed.
  - `tool_latency`: exercised only if the delayed call completed successfully (a denied call is `not_exercised`,
    `TOOL_CALL_DID_NOT_COMPLETE`); `misconduct` if an amount was spoken before the tool was even requested.
  - `caller_interrupts`: exercised iff `session.interrupted` is in the ledger; `misconduct` if audio generated
    before the interruption was released after it.
  - `background_noise`: exercised iff caller audio was streamed; `silence_gap` is not implemented in the runner and is
    always `not_exercised` (`FAULT_KIND_NOT_IMPLEMENTED`).
- Assurance (`apply_overlay`, used by `assess_run` and the ledger-derived `/assurance`): reason codes
  `FAULT_NOT_EXERCISED`, `FAULT_NOT_HANDLED`, `FAULT_MISCONDUCT`; an unexercised fault degrades `pass` to `untestable`
  (never softens a fail); misconduct or not-handled forces `fail` (misconduct also `model_behaviour: fail`) and is not
  masked by gaps. `/assurance` carries `fault_dispositions`.
- Note the previously approved single-sample comparison where the scripted/real model did not call the tool now reads
  as `not_exercised` rather than a pass.
- Open: the misconduct tests are ledger heuristics (spoken amounts, released audio), not a judge; the noise handling
  test only checks that turns settled, not that the model understood the caller; disposition events are not yet part
  of reliability denominators in comparison exports.

## (c) Out-of-band confirmation only after an observed confirmation

- `live_providers.before_turn` now runs independent ASR (`voice_confirmation.observe`) on the caller audio about to be
  spoken for the confirming turn (the runner exposes it as `pending_caller_pcm`), records
  `confirmation.voice_observation` (choice, reason codes, PCM and transcript hashes, `authorised`) and calls
  `engine.confirm` with the observed choice.
- Only `affirm` issues a `confirmation_id` to the model. A missing ASR backend, blank transcript, negation, mixed or
  unclear speech stays unauthorised: the engine records `confirmation.rejected` (`AMBIGUOUS_RESPONSE`) and the model
  is given no id.
- Limits: the observation is of the synthesised caller audio before it is streamed to the provider, not of audio the
  provider confirmed it heard; no "declared observation" alternative was added (an unavailable backend fails
  closed); the scenario keeps its `out_of_band_confirmation` label and still does not declare CONFIRM-01;
  the engine reports `deny` and `ambiguous` with the same rejection code.
