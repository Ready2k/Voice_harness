# Phase 1 acceptance status

The canonical measurement artifact is `artifacts/phase1-final-baseline.json`; similarly named earlier files are historical and are superseded for current measurement reporting.

Phase 0 (verification and design) is tracked separately in `docs/phase0/`: capability report schema and probe CLI are implemented; the provider matrix is approved but every cell is `unknown` pending provider accounts.

The deterministic core runs in Docker with PostgreSQL, FastAPI, stdio MCP, a mock adapter, PCM tokens and local object storage. Earlier SQLite/JSONL prototypes remain regression fixtures. Passing named tests alone does not establish full specification conformance. The Phase 1 gate (AC-01 through AC-08, AC-13 through AC-18 and AC-21, with the spec's Phase 1 qualifications) is not yet declared met.

| Gate | Executable fixture coverage |
|---|---|
| AC-01 | Stable logical call/result identity, repeated delivery deduplication and correlated stdio MCP results |
| AC-02 | Protected proposal denial before execution; separate model-fail/platform-pass outcomes |
| AC-03 | PCM bytes, exact session/hash/range/epoch binding, expiry/replay/forgery, partial evidence and bounded buffering |
| AC-04 | Durable intent/outbox, transactional cancellation/confirmation consumption, restart and SIGKILL-after-commit |
| AC-05 | Twenty interleaved namespaces with isolated proposal and cancellation mutations |
| AC-06 | Closed schemas, malformed-result rollback, context/ownership/identity checks |
| AC-07 | Evidence failure blocks calls/releases; trace-export failure preserves ledger and exports |
| AC-08 | Unsigned manifest roots, offline verification, event/artifact/suffix tampering and declared missing audio |
| AC-13 | Twelve PostgreSQL component benchmark profiles, 1,000 observations each, stage timings and fault dispositions |
| AC-14 | Pending/approved/scheduled/observed revisions, token invalidation and unsafe/unassessable post-release dispositions |
| AC-15 | Simulator mechanics: exact presented proposal accepted once; ambiguity, replay, expiry, proposal-hash mismatch and changed-amount rejection; one-time transactional consumption. Explicit unsolicited and cross-session confirmation cases, and direct changed-payee coverage, remain to be demonstrated. |
| AC-16 | Scripted branch replay and closed simulator context rejecting hidden oracle fields |
| AC-17 | Run-wide hold/purge serialization, expiry/release grace and tombstones |
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

## Remaining v0.3 conformance work

The whole Phase 1 release is not yet formally complete:

- Real-browser and physical-device playback verification remains: Node playback tests and Python WebSocket clients are not evidence of physical audibility or browser/DAC latency.
- The reference client is not a complete operator review UI.
- A final requirement-by-requirement conformance audit is still needed for exhaustive record metadata/examples, retention lifecycle, queue accounting and finding-scoped preservation dependency closure.
- AC-15 needs explicit evidence for unsolicited and cross-session confirmation rejection and direct changed-payee invalidation, rather than inferring coverage from related binding tests.
- The expanded 59-test suite needs a fresh PostgreSQL-backed Compose validation.
- Confirmation and semantic safety checks use labelled scripted fixtures, not live voice recognition or independently calibrated judges.

## Phase 2 adapter prerequisite

Native mismatched-rate resampling is not implemented; the reference playback path requires 24 kHz. This is a Phase 2 adapter gap rather than a Phase 1 mock-rate blocker. Any adapter with a verified native rate other than 24 kHz must implement and validate the spec's resampling path before claiming support.

Live provider verification, live ASR/judge calibration, signing and provider costs are later-phase work. Scripted PCM fixtures must not be reported as live semantic-assessment success.
