# Review of fix commit 31ccddd — 18 October 2026

Reviewed `31ccddd50090de8baf487ef44b8a12c0665aaa4e` against the outstanding specification v0.3 findings. No implementation changes were made. Existing uncommitted documentation, baseline artifacts and other user files were preserved.

The ordinary timeout and single-interruption cases are improved and reproduced successfully. The preserved approval-unit timestamp also closes the previously reproduced deadline-reset defect. Remaining live safety problems occur at overlapping response boundaries and where assessment still depends on mutable shared state. Queue bounds and typed factual verification remain partial.

## Validation

- Initial `make test` against the reused PostgreSQL database: 444 Python tests, 5 skipped, **one failure** in `test_HD04_unresolved_hold_listing_and_expiry_notice_event`. The test assumes both newly created holds appear on the first 100-item page of the cross-run register. The unchanged database has accumulated holds from prior tests. See F5.
- The retention module alone subsequently passed all 15 tests against that same PostgreSQL database. The failure is sensitive to randomly generated hold IDs and paging, rather than a consistently failing retention operation.
- Browser tests were run separately because `make test` stopped at the Python failure: all 28 passed.
- `make lint`: ruff and bandit successful, with existing Bandit comment/suppression warnings.
- An additional full run against a newly created isolated PostgreSQL database passed: **444 Python tests, 5 skipped**. The temporary database was removed after validation. The reused-database failure remains a reproducibility issue in the pagination test, not a demonstrated retention regression.
- Temporary credential-free probes verified both fixed behaviours and the remaining failures below. No live provider/ASR calls or new performance benchmark were run. The previous baseline remains tied to `7d173f5`, not this revision.

## Fixes confirmed

| Previous issue | Current result |
|---|---|
| Slow ASR succeeds after a turn timeout | The original 200 ms ASR / 30 ms timeout probe now returns `turn_timeout` and produces zero releases after the worker finishes. |
| One interruption arrives during ASR | The original queued-interruption probe now returns `superseded`, applies the interruption and produces zero releases. |
| ASR revision renews the five-second deadline | Replacement units retain the original `created_at`; the new six-second fake-clock regression test prevents approval. |
| Numeric negatives `-10 pounds` and `£-10` pass | Both exact cases now abstain; new regression assertions cover them. |
| Provenance absent from comparison GET/export | It is now included, with an integrity hash and a test detecting an unmodified-hash provenance alteration. Run existence still does not establish measurement provenance. |
| Native metadata accumulates without a limit | Metadata flushes every 100 records, bounding that list. Sanitised native payload retention/correlation and complete run configuration binding remain open. |

## Findings

### F1 — P1: A second response end blocks interruption intake again

**Code:** `src/voice_harness/live_scenario.py:236–244`, especially the inline `await task` at line 240. **Spec:** §§6/13, AC-03/14.

The new pump consumes events concurrently while one assessment runs, but when another `turn_end` arrives it waits for the first assessment inline. It stops consuming interruption/error controls during that wait. The first worker can release audio before the queued interruption is handled. The second end event has already been consumed and can also be lost when the first outcome settles the turn.

**Reproduced:** start a 200 ms scripted ASR for response A, then queue a second `TurnEnd` followed by `Interrupted(item_id="audio-a")`. The runner returns `approved`; the ledger has one release, zero interruptions have been applied, and the interruption remains queued. This reproduces the original failure through the newly introduced second-end branch.

**Fix:** treat response-end events as keyed work items; never await their assessment inline in the control/event intake loop. Maintain a pending response/job map and process interrupt/error controls continuously. Associate every end with its provider response/item and retain any end already consumed when another response completes.

**Regression check:** response A end -> response B/duplicate end -> interruption during A's ASR must apply the interruption and release zero stale samples. Include both distinct-response and duplicate-end cases, with no loss of the second end.

### F2 — P1: Assessment reset forgets the item that timeout must retire

**Code:** `src/voice_harness/live_assessment.py:269–278,301–305`. **Spec:** §§5/6/13, stale-generation rejection.

`_finish` captures text/turn/timing but not item identity, then `_reset` clears `_item_id` before ASR. If timeout/client cancellation happens during that assessment, `abandon` calls `_mark_dead` after the active identity has already been forgotten. `_dead_responses` stays empty. The fence stops the old worker, but late audio for the same provider item is accepted into a fresh unit in the next engine epoch.

**Reproduced with a real item ID:** start assessment of `audio-a`, wait until ASR has begun, call `abandon`, and deliver another `AudioOut(item_id="audio-a")`. It buffers 960 bytes and the dead-item set is empty. After the old worker returns `superseded`, send that old item's end: the new assessment returns `approved` and the ledger records one release. The existing late-item test interrupts before assessment resets the identity, so it misses this ordering.

**Fix:** retain response/item identity and captured generation in an immutable active-job record until the response is retired. Timeout/interruption must tombstone all affected buffered and assessing item IDs. Check event identity/generation before accepting audio/transcripts/end events, not only whether an item happens to be in a set derived from the current buffer. Missing item IDs need an explicit fallback or quarantine policy.

**Regression check:** abandon during ASR -> late frame/end for that same item produces zero new buffered bytes and zero release, while a genuinely new response remains usable.

### F3 — P1: Later tool facts can change an assessment already in progress

**Code:** `src/voice_harness/live_assessment.py:325` and `IndependentAssessor.assess`; `src/voice_harness/live_scenario.py` (`_tool`, extension of `expected_claims`). **Spec:** §§6–7, immutable context/approval evidence and point-in-time reference facts.

The worker receives the live `self.expected_claims` dictionary, not a frozen per-response context. The concurrent pump can dispatch another tool and append allowed amounts while the old worker is in ASR. The old speech is then checked against those later results. Snapshotting PCM/text/metrics does not snapshot authorisation facts. Flat numeric membership also continues to confuse balance, available balance, transaction amount and account ownership.

**Reproduced:** start ASR for `Your balance is ten pounds.` with no authorised amounts; append 1,000 minor units to the driver's expected facts while ASR runs. The assessment returns `approved` and releases. Separately, a balance claim passes allowed values `[1000,123456]` without identifying which value is the balance.

**Fix:** capture a deep immutable context at the response's assessment boundary, bind its hash to the unit, and pass that snapshot to the worker. Facts must carry account/entity, field, signed amount/currency, source event and effective time. Later tools may trigger a new explicit assessment/revision; they must not silently rewrite an in-flight job's evidence.

**Regression check:** adding/changing tool facts or starting another customer turn during ASR cannot alter the old job's result. Wrong-field/account and stale-source claims cannot receive strict approval.

### F4 — P2: Adapter limits still omit final transcripts and byte/duration budgets

**Code:** `src/voice_harness/adapters/openai_realtime.py:307–311`; equivalent Nova `_emit`. **Spec:** §§6/13/18, bounded audio/event queues.

The cap now covers `AudioOut` and `TranscriptDelta`; `TranscriptFinal`, tool calls, errors and other events still enter an unbounded `asyncio.Queue`. Each accepted audio entry also counts as one regardless of its size/duration. The 100-record native-metadata flush improves one separate buffer, not the adapter's total memory bound.

**Reproduced:** enqueue 2,000 assistant `TranscriptFinal` events: queue size is 2,000 and `maxsize=0`.

**Fix:** one explicit budget covering decoded bytes/duration and event bytes/count, with a small bounded control reserve and a defined overload terminal action. Validate individual frame sizes before allocation/buffering. Preserve evidence of discontinuity and prevent later approval of incomplete audio.

### F5 — P2: Retention test assumes all relevant records are on page one

**Code:** `tests/test_retention_exports.py:261–267`; `src/voice_harness/review_api.py:13–43`.

The register correctly advertises cursor pagination. The test requests its default first page, filters that page for its own random hold IDs, then asserts both are present. Once more than 100 unreleased holds exist, either new hold may sort onto a later page. This makes repeated `make test` runs against the project's persistent PostgreSQL volume fail nondeterministically.

**Observed:** the full run failed for one absent new hold; the same test passed during the subsequent 15-test retention-module rerun. The test itself already acknowledges that other runs share the register, but does not follow its cursor.

**Fix:** follow `next_cursor` until the test's records have been found, or use an isolated database/explicit scoped query for that assertion. Keep a separate deterministic fixture with more than 100 holds to test register pagination. Do not erase the persistent development database to make the assertion pass.

## Remaining specification scope

Live API/UI/CLI execution and exact provider configuration binding are still absent; native events remain hash-only rather than replayable payload evidence; comparison observations remain caller-authored; per-run live metering and speech-boundary metrics remain incomplete. These are known remaining requirements, not new regressions introduced by this commit. No whole-spec conformance or live assurance pass should be inferred from the confirmed fixes.

## Recommended next change

Address F1–F3 together with immutable per-response jobs containing item/response IDs, generation/assessment epochs, original deadline and frozen fact/context hash. Keep intake/control handling separate from worker completion. Workers should return assessment data, with approval/release applied by a single orchestration path after a transactional validity check. This avoids relying on shared buffer resets and special-case waits for each new event ordering.

Then complete total queue budgets and repair the pagination test. Add runner-level regressions for the reproduced orderings; direct driver calls alone do not test the new pump's concurrency behaviour.
