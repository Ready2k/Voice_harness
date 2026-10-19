# Phase 3 status

Merged 2026-10-17: manifest signing (AC-08), cost accounting (AC-12, AC-19), judges/calibration/human review (AC-10), comparison reports and independent-ASR assessment (AC-11, AC-14 live). At merge, Compose on PostgreSQL: 381 Python tests OK, 28 Node tests OK; ruff, bandit and pip-audit clean. The sections below were written at merge; where Phase 4 has since changed the position, this summary says so.

**Position as of 2026-10-19 (read before claiming Phase 3 complete):**
- Judge calibration has been run once each for an OpenAI judge (`gpt-4.1-mini`) and a Bedrock Claude Haiku 4.5 judge on the synthetic holdout (see `PHASE4_STATUS.md` and `PHASE4_NOTES_bedrock_calibration.md`). Only CARE-01 clears the numeric thresholds; no calibration policy has been approved, so all AI findings remain advisory and blocking is off. The holdout is tiny and synthetic, so the 0.80 thresholds are exploratory.
- Live independent ASR (`gpt-4o-mini-transcribe`) and a live OpenAI judge have been exercised on live adapter runs (Phase 4). The latest benchmark rerun (`BENCHMARK_5350E4A_2026-10-19.md`) was limited by exhausted ASR credits (HTTP 429), so strict speech approval depends on ASR quota.
- The shipped pricing table still has null prices for every real meter, so default exports say `unknown`. A vendor list-price table (`artifacts/pricing/vendor-pricing-2026-10-19.json`, loaded with `HARNESS_PRICING_TABLE` and by default in `make serve-live`) prices OpenAI, Nova, xAI and Gemini 3.8 meters (Gemini at the Developer API rate, assumed for Vertex); live runs record provider usage through `UsageRecorder`, and bake-off reports show voice cost per run. Mock runs still export no cost lines.
- Comparison reports still take caller-supplied observations. Live provider comparisons now run through the bake-off (`bakeoff.py`, `/client/bakeoff.html`), which computes its report from the ledgers; see `GEMINI_3_8_BENCHMARK_2026-10-19.md` and `BENCHMARK_5350E4A_2026-10-19.md`.
- Signature trust root is distributed out of band; revocation is not time-scoped; private key is on disk.

## Signing (AC-08 signature gate)

- **What is signed:** the evidence manifest, as RFC 8785 canonical bytes (`canonical.canonical_json`), with Ed25519 (`cryptography`). The manifest's own `signature` section (`{algorithm, key_id, envelope}`) and `signature_status: signed_ed25519` are written before signing, so the key choice is covered. The detached envelope `{algorithm, key_id, payload_sha256, value}` travels in the bundle as `signature` (and as a `signature` NDJSON record). `manifest_hash` is still `digest(manifest)`. `provider_coverage` and retention tombstones are inside the manifest and therefore signed.
- **Reviewer derived views** are signed as their own manifest (`signature`); the source manifest's envelope is carried as `source_signature`. Verification requires both to be valid and trusted, plus the existing `derived_from` hash link. The role-restricted standard view (`restrict_bundle`) drops the signature: it is a different manifest and is not a release export.
- **Key management** (`src/voice_harness/signing.py`, `KeyStore`): key generated on first start under `<data>/signing/keys/<key_id>.pem` (mode 0600); `key_id = ed25519-<sha256(raw pubkey)[:32]>`. `<data>/signing/key-history.jsonl` is an append-only, hash-chained record of generated/retired/revoked events (corruption raises `KEY_HISTORY_CORRUPT`). Rotation generates a new key, retires the old and deletes its private half (public half kept so old bundles verify). Revocation is permanent; revoking the active key first rotates. `public_keys()` is the distributable key ring (`keys`, `revoked`, `active_key_id`, `history_head`).
- **API:** `GET /api/v1/signing/public-keys`, `GET /api/v1/signing/key-history` (any authenticated role); `POST /api/v1/signing/rotate` and `/revoke {key_id, reason}` (operator only; the live signer is swapped immediately). CLI for a data dir: `python -m voice_harness signing-keys public|rotate|revoke|history --data-dir DIR`.
- **Offline verification:** `python -m voice_harness verify-bundle BUNDLE --public-key KEY [--public-key ...] [--revoked KEY_ID] [--allow-unsigned]`. `--public-key` takes a key-ring JSON (as served by the API), a PEM public key or a base64 raw key. Without `--public-key` or `--allow-unsigned` it exits 2. Failure codes: `UNSIGNED_BUNDLE`, `SIGNATURE_MISSING`, `SIGNATURE_INVALID`, `KEY_UNKNOWN`, `KEY_REVOKED`, `SIGNATURE_ALGORITHM_UNSUPPORTED`, `SIGNATURE_MANIFEST_MISMATCH`, `NO_TRUSTED_KEY`, `SOURCE_*` (derived view). `--allow-unsigned` accepts only a bundle that declares `unsigned_local` and carries no signature; an attached signature is always verified and a "signed" bundle without an envelope always fails. Python: `verify_bundle(bundle, keyring, allow_unsigned=...)`; with neither argument it stays integrity-only (legacy local use).
- **Tests:** `tests/test_signing.py` (altered manifest incl. recomputed hash, swapped/corrupt signature, unknown/revoked key, algorithm confusion, unsigned policy, NDJSON round trip, derived view, rotation, revocation, history tamper, key file mode, CLI, API).
- **Caveats:** the verifier's key ring is the trust root and must be obtained out of band; the demo fetches it from the same API, which proves mechanics, not independence. Revocation is not time-scoped: every signature by a revoked key fails, including ones made before compromise. A copy of the data volume exposes the active private key (no HSM/KMS). Old unsigned bundles remain verifiable only with `--allow-unsigned`. Retention purge changes the manifest (new tombstones), so a re-export is re-signed; earlier signed bundles stay valid for their own content. Committed artifacts (`artifacts/golden-run.json`, baselines) are not bundles and are unchanged.

## Cost accounting

Spec section 11 (cost export contract and reconciliation), section 12 `cost_lines`, AC-12 and AC-19. Tests: `tests/test_cost.py` (30 tests, SQLite; PostgreSQL path not executed here).

**What exists**
- `pricing.py` + `src/voice_harness/data/pricing_table.json`: versioned, hash-addressed pricing table (closed `Tariff` schema: provider/service/model/region, component, meter, unit, billing model, exclusivity group and priority, effective dates, currency, rate unit quantity, unit price, cached billing mode and price, quantity increment/rounding, minimum billed quantity, amount scale/rounding, source status, evidence ids). Unknown price = `null` plus `unknown_reason` (enforced by the schema). Overlapping effective ranges are rejected. `HARNESS_PRICING_TABLE` points at an operator-attested table.
- **No real tariff is established.** The shipped table carries OpenAI Realtime and Nova Sonic meters with `unit_price: null` (`PRICING_NOT_ESTABLISHED_OPERATOR_ATTESTATION_REQUIRED` / `BEDROCK_PRICING_NOT_ESTABLISHED`). The OpenAI per-1M-token figure in `docs/phase0/DOC_EVIDENCE.md` is an unverified documentation excerpt and was deliberately not entered; Nova 2.5 pricing, regions and quotas are not documented at all. Test tariffs live in `tests/fixtures/pricing_fixture.json` and are labelled `FIXTURE-NOT-A-VENDOR-PRICE`.
- Ledger events `cost.usage_reported` (provider-reported or labelled estimated usage; closed schema; estimated needs `estimation_method`, unknown quantity needs `unknown_reason`, cached is a subset of total, decimals are strings, floats rejected) and `cost.expectation_declared` (expected components for completeness). `cost.record_usage`, `cost.declare_expected_components`, and `cost.reports_from_adapter_usage` (OpenAI per-response and Nova cumulative-session `Usage`; modality meters only when the provider gave a consistent breakdown, otherwise `*_unclassified` meters which price as unknown).
- `cost.py`: lines are derived deterministically from ledger reports + pricing table + invoice allocations using `Decimal` (precision 60, tariff scale and rounding mode, quantity increment and minimum). Each line carries the spec fields (scope, meter, interval, provenance, usage source, quantity/unit, rate, currency, amount string or null, tariff id/hash/dates, usage event ids, invoice reference, estimation method/assumptions, unknown reason, calculation inputs). Supersession (never summed): later revision of a report, declared correction (`corrects_report_id`), cumulative report over incrementals, higher provenance of the same meter/scope (invoice > reported > estimated > unknown), declared covering scopes, and exclusivity groups (one alternative per component/group/scope, chosen by provenance then tariff priority). Exact duplicate reports are ignored and listed in `anomalies`; conflicting duplicates are flagged.
- Summaries per currency and provenance with `known_subtotal`, `by_provenance`, `summary_label`, `completeness` (`complete` only with declared expected components all known, no unknown lines and known metering scopes; a partial sum has `total_cost: null`), `unknown_line_count`, `unknown_components`, and a same-scope `estimated_vs_actual` disclosure.
- Migration **11** (`cost_store.py`, SQLite and PostgreSQL SQL): `harness_cost_reconciliations` (append-only account-period invoice records, revisions) and `harness_cost_lines` (immutable snapshot per exported section hash). Invoice facts are immutable; allocations arrive as new revisions and require method and non-empty evidence ids. A record without allocations stays an unallocated account-period record. Allocations that match no recorded meter/scope are listed as `unmatched_allocations` and produce no line, so no run is falsely marked invoice-reconciled.
- API: `GET /api/v1/runs/{id}/cost`, `GET|POST /api/v1/cost/reconciliations` (`status=unallocated|partially_allocated|allocated`).
- Evidence: the bundle gains a top-level `cost` section; the manifest gains `cost` (section hash, pricing table id/version/document hash, completeness, label, unknown counts) and `pricing` is filled when lines exist. `verify_bundle` checks the section hash, the embedded pricing document hash, and **re-derives the section from the ledger** (skipped only if payloads were tombstoned). Derived reviewer views carry the section; NDJSON has a `cost` record. Bundles from before this change still verify (no `cost` key on either side).

**Caveats / left**
- Usage recording (`cost_recorder.py`): `UsageRecorder` turns adapter `Usage` events into `cost.usage_reported` events. OpenAI `response.done` usage is incremental per response (scope = response id; cached subsets and audio/text breakdowns used only when consistent, else `*_unclassified` meters). Nova `usageEvent` totals are cumulative per provider session: each is a new revision (`revision` = provider `totalTokens`) of one session report, so later totals supersede and never add; distinct provider sessions add. Cancelled/failed responses record the usage the provider returned with `generation_state` cancelled/discarded and a `BILLED_STATUS_UNKNOWN` assumption (billing treatment is not guessed). A turn (or cumulative session, via `close()`) with no usage records an unknown line (`PROVIDER_RETURNED_NO_USAGE`), never zero. Duplicate delivery and reconnect replays append nothing (keys are seeded from the ledger).
- Wiring: `AdapterTurnDriver(..., cost_recorder=...)` feeds every event to the recorder and declares expected components (`voice`, plus `asr` when a live-capable ASR backend is set; `cost.ensure_expected_components` unions with earlier declarations because the newest declaration replaces older ones). `IndependentAssessor` records ASR usage returned by the provider (`AsrResult.usage`: duration seconds or tokens), else unknown with a reason (`OFFLINE_FIXTURE_NO_USAGE`, `ASR_REQUEST_FAILED_USAGE_UNKNOWN` for HTTP/transport failures). `CostMeteredJudge` wraps any `JudgeBackend` (OpenAI chat and Bedrock Converse backends now expose `last_usage`) and records per-call judge usage or unknown, declaring `judge`. Tests: `tests/test_cost_recorder.py` (17, replaying real shapes from `artifacts/phase0` probe logs; fixture tariffs for exact totals; the shipped null-price table exports unknown).
- Open: the driver path is wired, and live runs launched through the API now construct a `UsageRecorder` (`live_providers.py`); `Engine.create` and mock runs do not, so mock runs still export zero lines. Shipped tariffs remain null, so no real run has a known subtotal. The OpenAI/Nova modality meter names (`audio_input_tokens` ...) are the recorder's and need tariff rows when prices are established; Nova tariffs currently exist only for `*_unclassified` meters. Whether cancelled generations are billed is not established. OpenAI token usage for ASR depends on the transcription model returning `usage`; none is invented when absent.
- Judge, simulator, TTS/ASR and storage components are supported by the schema (any `component`) but no producer exists.
- Cancelled/discarded generation is included only when a report or labelled estimate covers it; there is no automatic inference of billed status.
- FX conversion is not implemented (optional in the spec); currencies are never mixed.
- Invoice import is manual via the API; no vendor invoice parser. Reconciliation records are not in any run ledger; a run's allocations are embedded in its exported section (hash-bound by the manifest) at export time.
- PostgreSQL path (migration 11, JSONB writes in the export transaction) was untested in this workstream; it has since run under the Compose suite (`make test`).
- Manifests are signed since the signing workstream landed (see Signing above).

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
| Evidence manifest lists judge/human results | Implemented: `judges/section.py` derives a `judging` section from the ledger (rubric, independence, offline, adjudication, online findings and errors, review items, review decisions, effective verdict, `judge_coverage`, calibration bindings, deterministic-failure precedence). The manifest carries `manifest["judging"]` (counts, effective assurance, `section_hash`, calibration policy hashes, precedence rule) and `manifest["human_adjudication"]` (full decision records), set before `sign_manifest`, so the signature covers them. `verify_bundle` recomputes the section from the ledger events, so a hash-consistent re-sealed forgery or a stripped section fails. Bundles lacking the section and the `versions.judging_section` marker still verify. Reviewer exports and NDJSON (`judging` record) carry it. | `section.py`, `evidence.bundle`, `exports.py`, `tests/test_judging_evidence.py` |
| Calibration policy hash/version used is recorded | Implemented: `judge.adjudicated.judge_calibration` and `judge.online_finding.calibration` bind the newest stored report (report id, policy id/version/hash, approval, blocking-enabled criteria) for the exact judge fingerprint, or `no_calibration_report`. Offline verdicts are never gated by policy; the binding discloses what governed blocking eligibility. | `calibration.binding_for` |
| Preservation holds can target the new findings | Implemented: finding ids may be `rubric.evaluated` / `judge.offline_evaluated` `finding_id`s, `judge.online_finding` ids, or review ids. Closure adds cited evidence, the evaluation's rubric/independence/adjudication events, the criterion's review items (claims/decisions join via `review_id`), online window bounds, and run envelope events. Review claims/decisions appended after activation, and the evidence they cite, stay out of metadata tombstoning (`retention._late_review_events`). | `evidence._judge_finding_seeds`, `retention.py` |

### Limits and caveats

- Judging-section limits: when metadata retention tombstones judge or review events, the section is re-derived from the surviving events only (purged findings disappear from it; the tombstone list in the manifest records the loss). A forger who also deletes `versions.judging_section` can present a bundle as pre-section legacy; for signed exports the signature (not this check) is the defence. The deterministic precedence is reported, not re-litigated: it follows `adjudicate`/`review.decide`.
- The holdout is synthetic and tiny (48 examples per criterion). With a perfect judge the Wilson 95% lower bound is about 0.86; the shipped policy thresholds (0.80) are exploratory, not derived from error costs.
  Fixture audio is silence with scripted transcripts, so nothing here measures ASR or audibility.
- CARE-01 labels and the reference CARE heuristic used in tests are keyword-based toy data; they prove the pipeline, not LLM quality.
- Online blocking is wired only as a flag on the finding (`blocking_enforced`); nothing in the engine consumes it yet, by design.
- PostgreSQL was not exercised in this worktree (no containers); `tests/test_judges.py` has since run under the Compose suite (`make test`).

## Comparison and live assessment

Scope of this section: AC-11 comparison reports, AC-14 live assessment (independent ASR source), and adapter-run metric boundaries. Judges, signing and cost accounting are tracked by their own workstreams.

### AC-11 comparison reports

- `src/voice_harness/comparison.py` is pure and deterministic: report = f(request). Pairing cluster is `(scenario_id, scenario_version, audio_variant, fixture_seed, policy_profile)`; repetitions are averaged inside a cluster first, so correlated repetitions and turns are never treated as independent.
- Method (also stated in every report under `method`): paired difference of cluster values, 95% percentile cluster bootstrap (default 2000 resamples) with `random.Random(seed)`, seed derived from the request hash unless supplied. Per-provider outcome intervals are Wilson 95% over clusters (event = any eligible repetition), so zero events still has a non-degenerate upper bound; the note says zero is not zero risk. Fewer than two paired clusters gives `interval: null`, `insufficient_pairs`; identical paired differences give `degenerate_zero_variance` with a reason (not a precision claim). Multiple comparisons are disclosed (pairwise count, measures, Bonferroni per-test level) but not corrected.
- Report contents: denominators per provider (planned, attempted, completed, failed incl. timeout, incomplete, excluded by mode, untestable results; failed runs stay in reliability denominators), paired cluster counts, exclusions with reasons and examples (`clock_ineligible_clock_unsynchronized`, `clock_ineligible_low_confidence_uncertainty_above_10ms`, `version_unresolved`, `failed_or_incomplete_run`, `adapter_lacks_dimension`, `untestable`, `metric_not_recorded`), version limitations per provider (unresolved alias, drift, `version_pinned_regression_gate_eligible`), `model_outcomes` and `platform_outcomes` as separate sections (a blocked model proposal stays a model finding), latency distributions (rank-eligible vs shown-low-confidence; p99 `insufficient_sample` below 1000), `critical_unresolved` (an untestable critical criterion yields `assurance.status = unresolved_critical_criterion`; there is no pass value), `ranking: null`. No score, rank or best-provider field exists.
- Modes: `exploratory` (unresolved aliases included but flagged) and `version_pinned` (unresolved aliases excluded). `evidence_grade` is `exploratory` unless a complete `predeclaration` is supplied; fewer than 20 repetitions per cluster is noted.
- API: `POST /api/v1/comparisons` (operator only; reviewers are read-only), `GET /api/v1/comparisons`, `GET /api/v1/comparisons/{id}`, `GET /api/v1/comparisons/{id}/export` (request + report + `report_hash`; `verify_stored_export` recomputes it). Persistence is `harness_comparisons`, created by `comparison_api.ComparisonStore` with **migration id 13** (SQLite and PostgreSQL SQL; PostgreSQL path not executed here). Schema: `contracts/comparison-request.schema.json`.
- Caveat: the input is a list of per-run observations supplied by the caller (optionally referencing `run_id`). There is no automatic extraction of observations from ledgers yet, and no live provider study has been run; the fixtures prove report properties, not provider quality.

### AC-14 live assessment (independent ASR)

- `asr.py`: backend interface; `ScriptedAsr` (offline, `live=False`, tests only) and `OpenAITranscriptionAsr` (opt-in: `VOICE_HARNESS_LIVE_ASR=openai` and `OPENAI_API_KEY` from the environment, model `gpt-4o-mini-transcribe`, synthetic audio only). `select_backend()` returns `None` unless the flag is set, so nothing is live by default. The key is only sent as a bearer header; errors carry reason codes only.
- `semantic_checks.py`: deterministic normalisation of spoken GBP amounts (digits, words, pence, `a hundred`) and dates (ISO, `fifth of March`, `March 5th`, `DD/MM/YYYY` when unambiguous). Ambiguous readings (`one fifty pounds`, `fifty pounds fifty`, `03/04`) are `untestable`; mismatches are `unsafe`; expected claims not heard are `untestable`.
- `live_assessment.IndependentAssessor`: adapter PCM unit (whole response, boundary `whole_response_no_alignment`) -> ASR -> semantic check -> `Engine.revise_audio(source="independent_asr")` (new assessment epoch, replacement unit carrying the ASR transcript) -> `Engine.approve_audio(assessment_source="independent_asr")`. Decisions record `assessment_source=independent_asr` and reason `INDEPENDENT_ASR_ASSESSMENT_SAFE`; the deterministic content layer still runs on the ASR text. New events `assessment.independent_asr` (with `live` and `assessment_scope`: `offline_fixture_not_live_evidence` for scripted backends) and `assessment.asr_unavailable`.
- Fail closed: with no usable backend the unit is blocked and `strict_speech_approval` is `unavailable` (`strict_speech_status()` also distinguishes `offline_fixture_only` from `available_live`). A scripted backend is never reported as live.
- Not done: incremental semantic segmentation (`conservative-v1`, `segment.finalized/deferred`) stays disabled; boundary is whole-response only. The post-release 4-state revision protocol is exercised by the existing AC-14 fixtures plus one independent-ASR revocation test, not by live audio.
- Live smoke: `tests/test_phase3_live_assessment.LiveAsrSmoke` (opt-in, needs the flag, key and macOS `say`). Not executed in this workstream (no `OPENAI_API_KEY` in the shell). Since then, live ASR has assessed real adapter audio in the Phase 4 live runs and bake-offs; strict speech approval on real audio is exercised there.

### Adapter-run metric boundaries

- `live_assessment.AdapterTurnDriver` consumes normalised adapter events (`audio_out`, transcripts, `turn_end`, `interrupted`), resamples to the harness rate, holds each completed response, assesses and releases, then emits `benchmark.adapter_metrics`: `first_transcript`, `time_to_first_generated_audio`, `time_to_first_released_audio`, `turn_completion`, `adapter_assessment_hold_duration`. Values need caller-supplied `speech_start_ms`/`speech_end_ms` in the adapter's monotonic domain; otherwise they are null with reasons (`NO_INPUT_BOUNDARY_SUPPLIED`, `CROSS_CLOCK_DOMAIN`, `NOT_RELEASED_*`). An interruption drops the unreleased buffer.
- `metrics.derive` now fills `time_to_first_generated_audio` / `time_to_first_released_audio` (and the extra metrics) from these events when present; the fixture-run reason is kept otherwise. These are software-boundary measurements; ASR time is observed only when a live backend is used.
- Not done in this workstream: live-provider benchmarks. These now run as bake-offs (Phase 4: 33 scenarios across four providers); `benchmark.py` remains the component benchmark.

### Conformance audit rows

| ID | Spec | Requirement | Implementation | Tests | Status | Notes |
|---|---|---|---|---|---|---|
| AC-11 | §15 | Comparison shows paired sample counts, intervals, exclusions, version limitations, separate model/platform outcomes | comparison.py, comparison_api.py | test_phase3_comparison.* | Met (fixtures) | Caller-supplied observations; PostgreSQL path of migration 13 not executed locally; no live provider data |
| AC-14 (live) | §15 | Live assessment where enabled: independent ASR feeds revision; unavailable is not a pass | asr.py, semantic_checks.py, live_assessment.py, engine.approve_audio(assessment_source) | test_phase3_live_assessment.* | Met offline; live smoke opt-in | Whole-response boundary only; scripted ASR is never live evidence |
| FN-04 | §15 | Compare two providers and export | comparison_api.export_payload | test_phase3_comparison.ComparisonApi | Met (mechanism) | Export is a comparison report, not an evidence bundle |
| PM-01 | §11 | Adapter-run metric boundaries | live_assessment.AdapterTurnDriver, metrics._adapter_metrics | test_phase3_live_assessment.AdapterMetrics | Partially met | No live benchmark study; cross-domain latency requires clock-sync input |
