# Phase 3 status

## Judges (AC-10, spec section 7)

Honest state: the judging, calibration and review machinery is implemented and tested offline against scripted judges and a
synthetic holdout. **No live judge has been calibrated, so every AI finding is advisory and no AI blocking is enabled.**
Deterministic checks remain the only enforceable verdicts. A live OpenAI backend and an AWS Bedrock Converse backend exist behind
the judge interface but are opt-in (`VOICE_HARNESS_LIVE_JUDGES=openai,bedrock`) and were not exercised live here (no key in the
session environment).

Package `src/voice_harness/judges/`:

- `rubric.py`: versioned rubric `banking.assurance.v1` (evaluation version `eval-1`) with IDENTITY-01, FACT-01, CONFIRM-01, CARE-01, AUDIO-01 and
  the existing SCENARIO-PROPOSAL-01. Each criterion carries id/version, applicability, expected and prohibited behaviour, severity,
  evidence selector, anchors, `mandatory_for_assurance`, track (model behaviour vs platform enforcement) and authority. Deterministic
  evaluators read only the ledger plus the read-only fixture/scenario oracle. Applicability is whatever the scenario's
  `applicable_criteria` declares in advance (plus an explicit benchmark `extra_applicable`); nothing is excluded for missing evidence.
  FACT-01 uses integer minor units; spoken-word amounts are `untestable` (ambiguous), never an invented failure. CARE-01 is judge-authority,
  non-mandatory by default, allows `partial`, and is the only criterion with anchored scores (1.0/0.5/0.0).
- `oracle.py`: the judge's read-only snapshot (canonical JSON, hashed): events (judge/rubric/review events and provider payloads
  stripped, tool text kept as evidence data only), fixture, expected actions, policy version, criterion definitions. No database,
  engine or credential object is reachable; live backends refuse anything not classified `synthetic_fixture`. Provider names are
  masked in free text (audio fingerprints cannot be hidden; recorded as a blinding limitation). Criterion order is seeded.
- `backends.py`: `JudgeBackend` interface; `ScriptedBackend` (tests), `OpenAIBackend` (Chat Completions JSON mode, key read from
  `OPENAI_API_KEY` at call time only), `BedrockConverseBackend` (needs boto3 and ambient AWS credentials, neither is a project dependency). The
  judge prompt is separate from any assistant prompt, tells the model that event text is untrusted data, and the judge session id is distinct from the run session.
- `independence.py`: per-run eligibility recorded in `judge.independence_recorded`: the tested model cannot judge itself, judges on the
  same provider/model count once, same family/provider are disclosed, cross-family presence, blinding limitations, `execution_credentials_granted: false`,
  `judge_coverage_incomplete` when fewer than the required independent judges (2 for a material benchmark).
- `offline.py`: runs a judge and turns its output into validated findings. Timeouts, transport failures, invalid JSON, missing criteria and
  duplicate results become `evaluation_error` findings that are stored, never passes. Abstentions are stored as `untestable` with `abstained: true`.
  The online runner (`run_online`, `submit_online` thread pool) is asynchronous and off the critical path; findings carry severity, confidence,
  `evidence_event_ids`, `recommended_action`, `blocking_capability` and `blocking_enforced` (false unless a calibration gate enables it).
- `findings.py`: citation validation. Every cited event id must exist in the run ledger and be inside the oracle scope; otherwise the finding is
  `invalid`/`evaluation_error`, cited ids move to `rejected_citation_ids` (kept visible, not written as evidence references), non-untestable verdicts need
  citations and a rationale, binary criteria reject `partial`, unanchored scores are dropped (`SCORE_DROPPED_NO_ANCHOR`).
- `adjudicate.py`: deterministic adjudication. Deterministic-authority criteria keep the deterministic verdict; a judge `pass` never overturns a deterministic
  fail; a judge `fail` against a deterministic pass is advisory but queued for review; a judge `fail` against a deterministic `untestable` gives `review_required`.
  Judge-authority criteria: unanimous decisive verdicts stand, disagreement on a material/mandatory criterion is `review_required` (non-material: worst case, flagged),
  never averaged; a mandatory judge-authority criterion needs two independent judges. Overall assurance follows spec precedence over mandatory applicable
  criteria (fail > review_required > untestable > pass, `NO_ASSURANCE_SCOPE` when none), tracks reported separately; an incomplete judge panel on a material
  benchmark makes the overall result `review_required` unless a deterministic fail already decided it.
- `service.py`: `assess_run` (judge calls outside any DB transaction, results appended as closed-schema events: `rubric.evaluated`,
  `judge.independence_recorded`, `judge.offline_evaluated`, `judge.adjudicated`, `review.opened`) and `assess_online` (`judge.online_finding`,
  `judge.online_error`). Events do not touch the engine-owned `tx.state["assurance"]`.
- `review.py` + `store.py`: queue state machine `open -> in_review -> decided` (claim by one reviewer, only the claimant decides), every transition a ledger event
  (`review.opened/claimed/decided`) written in the same transaction as the `harness_review_queue` index row. Decisions are `human_adjudication` evidence
  (`independent_model_validation: false`). Effective assurance (`GET /api/v1/runs/{id}/assurance`) is replayed from the ledger. A deterministic fail stays a fail
  after review (`effect: not_applied_deterministic_failure`) unless the decision carries a `check_correction` naming that check with ledger evidence; a human `pass`/`fail`
  needs cited ledger evidence or it is `not_applied_no_supporting_evidence`; the original finding is never edited.
- `holdout.py` + `calibration.py`: holdout `holdout-v1` is generated deterministically (24 pass + 24 fail synthetic mini-ledgers per criterion, two locales, two audio profiles, two reviewer labels
  each, content-hashed) and asserted disjoint from the `tuning-v1` prompt-tuning split by id and by content. `CalibrationPolicy` declares rule/model/template versions, target population,
  sample-size rationale, per-criterion precision/recall lower bounds, minimum sample counts, abstention/error ceilings, reviewer-agreement floor, Wilson interval method and confidence,
  abstention handling, and reviewer approval. A report carries confusion matrices, precision/recall with Wilson bounds, abstentions, evaluation errors by code, coverage by locale/audio
  profile, reviewer agreement (raw and Cohen kappa), policy hash, holdout hash and the per-criterion enablement decision with every failed check. Blocking requires: stored report, approved policy
  (insert-only approval row), matching model/template/rule/holdout versions, and the locale/audio profile inside the measured population; any miss leaves the rule advisory.
- Migration id 12 (`store.py`) creates `harness_review_queue`, `harness_calibration_policies`, `harness_calibration_approvals`, `harness_calibration_reports` (SQLite and PostgreSQL types), applied
  lazily and idempotently so it does not edit `storage.py`.
- API (operator and reviewer tokens; reviewers stay read-only except `POST /api/v1/reviews/{id}/claim|decision` and `POST /api/v1/calibration/policies/{hash}/approve`):
  `GET /api/v1/rubric`, `POST /api/v1/runs/{id}/assessments` (operator), `GET /api/v1/runs/{id}/assurance`, `GET /api/v1/reviews[?state=]`, `GET /api/v1/reviews/{id}`, claim/decision,
  `GET /api/v1/calibration/policy|reports`, `POST /api/v1/calibration/evaluate` (operator). Live backends in these requests are refused (422) unless the env flag is set.

Tests: `tests/test_judges.py` (offline, 44 tests). AC-10 coverage: holdout reports errors, abstentions and fabricated citations (`test_holdout_reports_errors_abstentions_and_fabricated_citations`);
fabricated citations fail validation (`CitationValidation`); unresolved disagreement requires review (`Adjudication.test_disagreement_on_material_criterion_requires_review_no_averaging`,
`test_judge_fail_on_untestable_deterministic_needs_review`, coverage tests).

### Judges conformance audit (spec section 7)

| Requirement | State | Where |
|---|---|---|
| Online findings with severity, confidence, evidence ids, recommended action, blocking capability | Implemented, advisory | `offline.run_online`, `judge.online_finding` |
| Online judge async, not an undocumented critical-path model | Implemented (thread pool, no engine hook) | `submit_online`; not wired into the live pipeline |
| Seven online risk categories | Schema supports all seven; no judge prompt tuned or calibrated | `events.ONLINE_CATEGORIES` |
| Offline per-criterion pass/fail/partial/untestable, anchored scores only, evidence rationale, uncertainty, judge/model/template/eval version | Implemented | `findings.py`, `JudgeIdentity` |
| Two independent judges with deterministic adjudication | Implemented and tested with scripted judges | `adjudicate.py` |
| Human review for high severity and disputed outcomes | Implemented (queue, API, ledger events) | `review.py` |
| Tested model not its own judge; separate session; no execution credentials; per-run record | Implemented | `independence.py`, `oracle.py` |
| `judge_coverage_incomplete`; do not manufacture independence; absent judges not passes | Implemented | `independence.assess`, `adjudicate.overall` |
| Read-only oracle; hidden answers never reach assistant; adversarial tool text is data | Implemented for the judge side; assistant-side isolation was Phase 1 | `oracle.py`, system prompt |
| Reference facts at tool/utterance time; integer money; ambiguous amounts untestable | Implemented | `rubric.eval_fact` |
| Rubric fields, applicability fixed in advance, mandatory flag | Implemented | `rubric.Criterion`, `applicability` |
| Findings carry criterion/version, verdict, severity, evidence ids, modality, rationale, confidence source; IDs validated; timeout/invalid JSON is `evaluation_error` | Implemented | `findings.py`, `offline.py` |
| Calibration holdout with pass and fail per criterion, separate from tuning; counts, confusion, precision/recall, abstention, reviewer agreement, coverage by locale/profile | Implemented on a synthetic holdout | `holdout.py`, `calibration.py` |
| CalibrationPolicy fields; exploratory 50+50/0.95 not auto-enabling; export measured values, bounds, policy hash, decision | Implemented | `calibration.CalibrationPolicy`, report |
| Deterministic checks authoritative; human review cannot override missing evidence by assertion; append-only correction | Implemented | `adjudicate.py`, `review.decide` |
| Verdict propagation precedence and two tracks | Implemented | `adjudicate.overall` |
| Audio `unassessable` mapped to criterion `untestable` | Partly: AUDIO-01 reports `untestable` for missing playback evidence or pending client ack; the engine's `unassessable` audio state is not separately mapped | `rubric.eval_audio` |
| Randomised comparison order | Criterion presentation order is seeded and recorded; arm-order randomisation across comparisons is the comparison workstream's job | `oracle.build_oracle` |
| Evidence manifest lists judge/human results | **Not done**: `evidence.py` still lists only `judge.completed` in `deterministic_check_and_judge_results` and an empty `human_adjudication` (kept out of the diff to avoid conflicts with the signing workstream). Ledger export contains the new events, and `/assurance` derives the view. | follow-up |
| Preservation holds can target the new findings | **Not done**: holds resolve finding ids only from `judge.completed` | follow-up |

### Limits and caveats

- The holdout is synthetic and tiny (48 examples per criterion). With a perfect judge the Wilson 95% lower bound is about 0.86; the shipped policy thresholds (0.80) are exploratory, not derived from error costs.
  Fixture audio is silence with scripted transcripts, so nothing here measures ASR or audibility.
- CARE-01 labels and the reference CARE heuristic used in tests are keyword-based toy data; they prove the pipeline, not LLM quality.
- Online blocking is wired only as a flag on the finding (`blocking_enforced`); nothing in the engine consumes it yet, by design.
- PostgreSQL was not exercised in this worktree (no containers); the SQL sticks to statements already used elsewhere in the project, but `tests/test_judges.py` should be run under `make test`.
