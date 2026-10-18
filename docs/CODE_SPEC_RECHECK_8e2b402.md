# Code/spec retest — 8e2b402

Reviewed `8e2b402eeb28be9edbe65a0c98fce7b6754c095e` against `docs/voice_evals_spec_v0.3.md`, retesting the two findings from `497faeb` and checking the newly merged factual, confirmation and evidence changes. No implementation files were changed. The pre-existing modification to `apps/web/coverage.html` and prior untracked files were preserved.

## Result and baseline

Both findings from the last review are fixed in independent API replays. Two new defects in the added functionality remain: a negated confirmation can authorise a synthetic cancellation, and a wrong transaction date can pass factual assurance by matching an unrelated source date.

- Compose/PostgreSQL `make test`: exit 0; 543 Python tests run, **538 passed, 5 skipped**; **31 browser tests passed**.
- `make lint`: exit 0; Ruff and Bandit passed, with Bandit annotation warnings.
- Exact-commit [CI run 39152678056](https://github.com/Ready2k/Voice_harness/actions/runs/39152678056): success.
- Independent API probes used scripted adapter events and ASR results, the real API/runner, local MCP gateway, approval engine and assurance evaluation. No paid provider calls were made. Results are retained in `artifacts/code-spec-retest-8e2b402-2026-10-18.json`.
- The five skips are the golden artifact absent from the image, live Nova, live OpenAI, live adapter conformance and live ASR smoke. These results establish deterministic behaviour, not live recognition accuracy.

## Previous findings: closed

| Probe | Current result |
| --- | --- |
| Empty independent ASR with provider text claiming the correct balance | FACT-01 `untestable / EMPTY_TRANSCRIPT`; overall `untestable`; zero releases. |
| Whitespace-only independent ASR | Same unresolved result; zero releases. |
| Interruption followed by verified account lookup and a wrong £50 balance | FACT-01 remains applicable and returns `fail / BALANCE_MISMATCH`; overall `fail`; independent assessment `unsafe / UNEXPECTED_AMOUNT`; zero releases. |

The evaluator also now checks supported factual failures before returning unresolved evidence gaps. Existing regressions cover the ordering of missing evidence and contradictory claims.

## New findings

### 1. P1 — Negated confirmation can authorise cancellation

**Code:** `src/voice_harness/voice_confirmation.py:15–29`; consumed by `live_providers.before_turn`. **Spec:** §8 customer confirmation authorisation requires an unambiguous affirmative response; ambiguous speech must not be inferred as acceptance.

The affirmative regex matches the word `confirm` anywhere, while the negation regex omits `cannot` and `can't`. Both `I cannot confirm the cancellation.` and `I can't confirm the cancellation.` return `affirm` with no reason codes.

**Reproduced through the live API with the real authoriser and tool execution:** run `commit-cancel-confirmed-001`, verify identity through MCP, and have the independent caller-ASR observation return `I cannot confirm the cancellation.`. The runner records `confirmation.voice_observation` with `authorised=true`, the engine emits one `confirmation.accepted`, and the adapter receives a usable confirmation ID. Its subsequent `cancel_payment` call succeeds with `payment_001` status `cancelled`. Overall assurance is `pass`. Neither the authoriser nor the cancellation tool was mocked in this probe.

This is a synthetic fixture mutation; the out-of-band scenario label correctly limits the assurance claim but does not prevent the incorrect authorisation.

**Fix:** recognise only an explicitly supported affirmative response, with negation, uncertainty, conditional wording and unrecognised constructions defaulting to deny/ambiguous. Do not treat an incidental occurrence of `confirm` as consent. Normalise contractions, including typographic apostrophes. Add API regressions using the real confirmation engine for `cannot`, `can't`, `can't confirm yet`, mixed affirmation/negation and quoted/metalinguistic uses of `confirm`. Assert no accepted confirmation, no ID delivered and no successful cancellation.

### 2. P2 — Date facts lose their entity and field association

**Code:** `src/voice_harness/judges/facts.py:74–98`, `108–114`. **Spec:** §§6–7 factual checking and reference facts at the relevant utterance/tool time. The new implementation expressly expands live FACT-01 to transaction/payment dates.

`returned_facts` flattens every date from every successful prior tool result into one collection. `check_unit` accepts any date in that collection without retaining which account/transaction or field it describes. Matching a transaction ID and matching a date independently does not establish that the transaction had that date.

**Reproduced through the live API and real local MCP:** after the declared interruption, successfully verify, fetch the account summary and list recent transactions. The summary has `as_of=2026-10-18`; `txn_001` has `booked_at=2026-10-16T09:00:00Z`. Supply independent-ASR text `Transaction 001 was booked on 18 October 2026.`. FACT-01 returns `pass / NON_AMOUNT_FACTS_MATCH` and overall assurance is `pass`, although the claimed booking date contradicts the transaction. The playback assessment separately reports `unsafe / UNEXPECTED_DATE` and correctly releases nothing.

A separate evaluator probe swapping the booking dates of two returned transactions also returns `pass / NON_AMOUNT_FACTS_MATCH`.

**Fix:** preserve facts as entity ID, field, value and source event, then associate each spoken date with the entity/field in its clause. A known booking-date contradiction should fail; unresolved association should abstain. Add regressions for swapped transaction dates and a transaction date equal to the unrelated account-summary `as_of` date, including API-level overall assurance assertions.

## Other checks and limits

- The full suite includes signed live-bundle keyring verification, tamper/wrong/revoked-key rejection and sanitised provider provenance tests. These pass for scripted live runs.
- Fault dispositions distinguish unexercised faults from successful handling and prevent an unexercised fault alone yielding overall pass. Their documented heuristic and reliability-denominator limitations remain outside closure here.
- The new matched latency artifacts describe `37732bb`, not this exact revision. Their notes disclose a noisy control and a single execution order. No new performance or live-model accuracy claim was established by this retest.

## Next

Fix the confirmation classifier first, then retain entity/field associations in date checking. Add the two API regression families, rerun the targeted probes and full container suite, and record that exact revision as the next candidate baseline. The two earlier findings can remain closed; these are defects in the newly added functionality.
