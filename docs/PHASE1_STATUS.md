# Phase 1 acceptance status

**Current local validation (19 October 2026, `dbbe37a`, SQLite):** 642 Python tests OK (8 skipped) and 71 browser tests passed. The Compose/PostgreSQL suite and lint were not re-run on this commit, and no component benchmark has been run since `89fa78f`. The figures below are historical, labelled by commit. Phase 2–4 work (live adapters, signing, cost, judges, the operator UI and provider bake-offs) is tracked in `PHASE2_STATUS.md`, `PHASE3_STATUS.md` and `PHASE4_STATUS.md`.

**Previous local validation (18 October 2026, `f477615`, SQLite):** 603 Python tests OK (6 skipped) and the browser tests passed.

**Latest validation (18 October 2026, `89fa78f`):** CI passed; local Compose/PostgreSQL validation passed 455 Python tests (5 skipped) and 28 browser tests, plus lint. A matched rerun against `31ccddd` measured component-path p95 **9.29–13.17 ms before / 9.50–13.70 ms after** under the existing 8-vCPU / 1 GiB benchmark profile. The study and exact source/image hashes are in `artifacts/matched-latency-31ccddd-vs-89fa78f-2026-10-18.json`. Ten-session WebSocket p95 was higher on the current revision in both the main and reversed-order check; causal attribution remains unresolved. See [the current review](CODE_SPEC_RECHECK_89fa78f.md) for remaining approval gaps, deferred scope and full measurement limits. This supersedes the older component artifact below for current-source reporting; it is not a live-assurance pass.

**Later commit (not re-benchmarked):** `1a96590` (recheck 89fa78f fixes: clause-level amount attribution, expiry-aware verified account, unkeyed quarantine) passed 465 local tests (6 skipped, SQLite) and lint; the Compose/PostgreSQL suite, browser tests and the matched latency study above were not re-run on it.

The latest component measurement artifact is `artifacts/phase1-recheck-baseline-2026-10-18.json`, from the frozen runtime image at commit `7d173f5`: twelve PostgreSQL profiles, 1,000 observations each, `total_path` p95 **8.29–10.67 ms**, plus three 1,000-observation WebSocket profiles. The companion `.provenance.json` records source/build hashes and the prior-profile comparison. The existing 8-vCPU quota / 1 GiB container profile is retained; full orchestration targets remain provisional. A scenario-text edit made during the run is preserved but excluded from the measured image. See [the current recheck](CODE_SPEC_RECHECK_2026-10-18.md) for exact scope, 440-test PostgreSQL validation, remaining live safety defects and the higher single-session measurements. This component baseline is not a live-path conformance declaration.

Previous measurement artifacts are historical: `artifacts/phase1-post-gap-baseline.json` (2026-10-17, path p95 **5.84–11.25 ms**), `artifacts/phase1-post-audit-baseline.json` (**9.54–14.08 ms**) and the pre-audit `artifacts/phase1-final-baseline.json` (**6.22–12.08 ms**). The sections below retain earlier phase-specific acceptance history; use the linked recheck for the current review position.

Phase 0 (verification and design) is tracked separately in `docs/phase0/`: capability report schema and probe CLI are implemented. The original all-`unknown` provider matrix was superseded by live probes; all four families now have capability reports in `artifacts/phase0/` (OpenAI and Nova from 2026-10-17, xAI from 2026-10-18, Gemini 3.8 from 2026-10-19). Reports expire after seven days.

The deterministic core runs in Docker with PostgreSQL, FastAPI, stdio MCP, a mock adapter, PCM tokens and local object storage. Earlier SQLite/JSONL prototypes remain regression fixtures. Passing named tests alone does not establish full specification conformance.

**Gate position (17 October 2026, after the requirement-by-requirement audit in `docs/PHASE1_CONFORMANCE_AUDIT.md`).** Every acceptance check in the Phase 1 gate (AC-01 through AC-08, AC-13 through AC-18 and AC-21, with the spec's Phase 1 qualifications) passes its executable oracle on SQLite and on PostgreSQL. The gate can be declared met for those checks, scoped to scripted fixtures and the software playback boundary: the audit work is committed and the post-audit benchmark was re-run (`artifacts/phase1-recheck-baseline-2026-10-18.json`, then the matched study at `89fa78f`). It is **not** a declaration that the whole of spec v0.3 is satisfied for Phase 1: the audit leaves 31 rows partially met and 3 not met (see "Remaining v0.3 conformance work"). Timing targets remain provisional. Physical browser/DAC playback is an explicit documented exclusion, not a failure.

| Gate | Executable fixture coverage |
|---|---|
| AC-01 | Stable logical call/result identity, repeated delivery deduplication and correlated stdio MCP results |
| AC-02 | Protected proposal denial before execution; separate model-fail/platform-pass outcomes, asserted on the Engine path for all three scenarios as well as the standalone golden ledger |
| AC-03 | PCM bytes, exact session/hash/range/epoch binding, expiry/replay/forgery, partial evidence, bounded buffering; overflow and a late (over 5 s) decision persist typed failures, discard unreleased units and cancel generation |
| AC-04 | Durable intent/outbox, transactional cancellation/confirmation consumption, restart and SIGKILL-after-commit |
| AC-05 | Twenty interleaved namespaces with isolated proposal and cancellation mutations |
| AC-06 | Closed schemas, malformed-result rollback, context/ownership/identity checks |
| AC-07 | Evidence failure blocks calls/releases; trace-export failure preserves ledger and exports |
| AC-08 | Unsigned manifest roots, offline verification, event/artifact/suffix tampering and declared missing audio |
| AC-13 | Twelve PostgreSQL component benchmark profiles, 1,000 observations each, stage timings and fault dispositions |
| AC-14 | Pending/approved/scheduled/observed revisions, token invalidation and unsafe/unassessable post-release dispositions |
| AC-15 | Exact presented proposal accepted once; ambiguity, replay, expiry, proposal-hash mismatch, changed amount rejected; explicit tests for unsolicited (unknown and superseded request), cross-session (foreign context, foreign run, foreign confirmation id) and changed-payee invalidation (payee now part of the proposal hash). One-time transactional consumption. |
| AC-16 | Full branch record (closed schema, precedence, fallback, utterance event) that reproduces scripted/seeded selection from the record plus policy; closed simulator context rejecting hidden oracle fields |
| AC-17 | Run-wide and finding-scoped holds (dependency closure), hold/purge serialization with both legal orderings asserted, per-artifact expiry/release grace and tombstones |
| AC-18 | Fail precedence, disagreement/review, missing/partial/error evidence, no scope and all-pass truth table |
| AC-21 | Eight-sample four-timestamp bounds, asymmetry, drift, stale/reconnect invalidation and ranking exclusions |

`tests/test_release.py` uses PostgreSQL when run in Compose. CI is configured to run the container suite; configuration alone is not evidence of a successful hosted CI run. The table maps available fixture evidence, not complete acceptance certification.

## Measurements

`artifacts/phase1-final-baseline.json` has status `measured_microbenchmark`. It contains twelve component profiles and three TCP WebSocket profiles, each with 1,000 observations, covering 1, 5 and 10 sessions. Component `total_path` p95 ranges from **6.22–12.08 ms** across the twelve profiles; this is not a sum of stage percentiles or a browser/DAC latency measurement.

Historical artifacts: `artifacts/phase1-baseline.json` records connection-per-event overhead (approximately 85–90 ms p95 at ten sessions); `artifacts/phase1-baseline-pooled.json` records the pooled rerun; `artifacts/phase1-baseline-final.json` adds envelope, policy, token, outbound and software-client stages. The previously reported 8.0–10.9 ms range belongs to the earlier component run, not the canonical artifact. CPU limits differed between the initial and pooled runs; all improvement cannot be attributed solely to pooling.

These measurements include no live ASR, provider latency or physical browser/DAC path. The canonical artifact's source hash identifies the benchmarked revision; subsequent trace-propagation changes were tested but not re-benchmarked.

The spec's §16 baseline commit is recorded: git was initialised on 2026-10-16 and commit `b548b98` (tag `phase1-baseline`) includes the measurement artifacts. Timing targets nonetheless remain **provisional**, not established, until the Compose-backed re-run and the remaining conformance work below are complete.

## Latest validation (16 October 2026)

- Previous container validation: 53 Python tests and three playback-client tests passed, with PostgreSQL exercised through Compose.
- Subsequent reviewer-reported local validation: 59 Python tests passed on SQLite, including six new Phase 0 tests. This does not revalidate the expanded suite against PostgreSQL; an updated Compose run remains outstanding.

Implemented since the initial acceptance notes: signed browser approval verification and bounded AudioWorklet scheduling; acknowledgement/flush/revocation handling; strict and exploratory release profiles; consent revision revocation and rejected-observation replay prevention; MCP fault and unknown-outcome recovery; generated TypeScript contracts and relational projections; raw benchmark spans and provenance hashes; preservation renewal and expiry-notice events.

## Audit validation (17 October 2026)

Local SQLite: 150 Python tests OK (3 live-provider skips) plus ruff clean. PostgreSQL through Compose (`make test`): 150 Python tests OK (4 skips: 3 live-provider, 1 golden-artifact check because the image does not ship `artifacts/`) and 3 browser-scheduler tests OK, run repeatedly against a persistent database; `make golden`, `make recovery`, `make lint` (ruff + bandit) and `make audit` exit 0. See the conformance audit for the exact command list.

## Compose validation (16 October 2026, post-Phase 2 code)

`make test` against PostgreSQL: 101 Python tests OK (3 live-provider tests skipped by design) and 3 browser-scheduler tests passed; `make golden` and `make recovery` exit 0. `make lint` (ruff 0.17.1, bandit 1.9.4; configuration in `pyproject.toml`) and `make audit` (pip-audit on the hash-locked requirements: no known vulnerabilities) pass and run in CI. Reviewed suppressions: bandit B311/B608 (seeded fixtures; internal table names, no user input). This supersedes the earlier "fresh PostgreSQL validation outstanding" item. The Nova bridge (Node) is not exercised inside the container.

## Conformance audit (17 October 2026)

`docs/PHASE1_CONFORMANCE_AUDIT.md` is the traceability table (142 rows: 94 Met, 31 Partially met, 3 Not met, 13 Out of Phase 1 scope, 1 Excluded). The audit fixed 13 groups of defects found by reading the code (RFC 8785 hashing, closed event sources, finding-scoped holds, queue accounting and typed failures, cancellation versus unknown outcomes, tool assembling/abandonment, policy decision records, simulator branch records, manifest completeness, real-digest examples, declarative scenarios, spec criterion IDs, smaller items), each with a regression test in `tests/test_conformance.py`. The four items previously listed as unaudited are resolved as: record metadata/examples partially met; retention lifecycle partially met (30-day metadata purge not implemented); queue accounting partially met (defaults stricter than proposed, 100 ms lookahead and 2 s inbound queue recorded but not enforced); finding-scoped dependency closure met for purgeable artifacts.

## Remaining v0.3 conformance work

The whole Phase 1 release is not yet formally complete:

- **Excluded (documented):** real-browser and physical-device playback verification. Node playback tests and Python WebSocket clients are not evidence of physical audibility or browser/DAC latency.
- **Not met at the audit, since resolved:** 30-day metadata retention purge (now implemented: `evidence.py` RT-02 tombstoning with `retention.metadata_purged`); operator UI (now `apps/web/`: runs, live view, review, compare, coverage, manual and bake-off pages); per-sample Python resampler (`resample.py` now uses native libsoxr). The audit table in `PHASE1_CONFORMANCE_AUDIT.md` has not been re-scored for these.
- **Partially met at the audit, highest risk first (not re-audited since):** per-call `cancel_requested` during execution; runtime browser clock-sync exchange and `clock_sync_samples`; operator/reviewer roles and TLS enforcement; relational shape of `runs`/`events`; OTel metrics/logs and span links; NDJSON/restricted export; guardrail content layer; MC error shape (`retryable`, `message`). Full list and proposed fixes are in the audit.
- Hashes in ledger bundles created before the audit no longer verify (RFC 8785 canonicalization). `artifacts/golden-run.json` was regenerated and is checked by a test; any external exports must be regenerated. Bundles exported before the AC-20 coverage section (Phase 2) also fail `verify_bundle` and must be re-exported.
- The post-audit baseline has been re-run (see the top of this file); timing targets remain provisional, and the latest benchmark (`89fa78f`) predates the Phase 4 and Gemini 3.8 changes.
- Confirmation and semantic safety checks use labelled scripted fixtures, not live voice recognition or independently calibrated judges.

## Phase 2 adapter prerequisite

Native (libsoxr) mismatched-rate resampling is implemented (2026-10-17) and used by the live adapters (for example Nova and Gemini input at 16 kHz); the reference playback path still requires 24 kHz.

Live provider verification, live ASR, judge calibration, signing and provider costs were later-phase work and are now tracked in the Phase 2–4 status files. Scripted PCM fixtures must not be reported as live semantic-assessment success.
