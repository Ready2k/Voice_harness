# Phase 5 status (hardening, ongoing; opened 2026-10-29)

Spec §16: "Security review, dependency pinning, load tests, chaos tests, retention tests, reproducibility checks, and
regression gates in CI." Phase 5 has no pass/fail gate; it is a backlog that is worked down. Nothing below is marked done
unless a command and a result are recorded next to it. Spec §18 (runtime selection) and §11 require Phase 5 load tests to measure concurrent
sessions, CPU, memory, queue depth, JSON serialization cost and persistence backpressure before a production-scale runtime
is chosen; Phase 1's component benchmark (`make benchmark`) is the baseline this phase extends, not replaces.

## Starting position (2026-10-29)

- Done in earlier phases, not re-verified here: hash-locked `requirements.lock` and digest-pinned images; the Phase 1
  lint/security gate; RT-02 metadata tombstoning; basic multi-session load test; component benchmark baseline.
- Last full run: 1082 Python tests OK (8 skipped) and 190 browser tests, on the host with SQLite. The Compose/PostgreSQL
  suite (`make test`) was green in CI on 2026-10-29 after lint and image fixes (backlog item 1).
- CI today: `.github/workflows/phase1.yml` and `pages.yml` only. No performance, load, chaos or retention gate.

## Backlog

Order is a suggestion; the first item unblocks trusting the rest.

| # | Item | Spec | Status | Done when |
|---|------|------|--------|-----------|
| 1 | Re-run `make test` on Compose/PostgreSQL for the current commit | Phase 1 gate | done 2026-10-29 | CI run 40845575584 (commit of the voice_naturalness image fix): `make lint`, `make audit` and `make test` on Compose/PostgreSQL all passed. Test counts not recorded |
| 2 | Performance regression gate | regression gates | in progress | Implemented (`make perf-gate`, `voice_harness.perf_gate`, `artifacts/perf-baseline.json`, CI step); baseline is from a local OrbStack run, pending a CI run before it is called done. CI compares `make benchmark` output to a stored baseline with a stated tolerance and fails on a miss |
| 3 | Load test: concurrent sessions | §18 | open | Sessions, CPU, memory, queue depth/bytes, JSON cost, persistence backpressure measured at stepped concurrency; artifact and command in repo |
| 4 | Chaos tests | chaos | open | DB outage, slow/failed evidence write, collector down, provider disconnect mid-turn: evidence-write failure blocks tool calls and releases, trace-export failure loses no ledger data |
| 5 | Retention tests | retention | open | Executable test of the 30-day tombstone and export behaviour over time, not just unit-level |
| 6 | Reproducibility checks | reproducibility | open | Same scenario and seed gives the same ledger hash; contracts drift check stays green in CI |
| 7 | Security review | security review | open | Review of auth (operator token), approval-token single use, WebSocket origin, export/download links, secrets handling; findings tracked |
| 8 | Dependency audit | pinning | open | Vulnerability scan of `requirements.lock`, npm lockfiles and base-image digests; update cadence documented |
| 9 | Optional: WebRTC transport | §13 | deferred | Only after 3 and 4, and only with an equally enforceable playback gate |

## Carried over from Phase 4

Variance beyond 3 repetitions; re-probing capability reports before they expire (2026-11-04/05, re-run the probes and
`scripts/run_ac09.py`); live synthetic-commit test (no live model has proposed a commit); locale-aware caller voice; judge
calibration policy approval (blocking stays off); provider tariffs in the shipped pricing table; Nova runs re-run so they
are priced; live runs inside the container (no credentials or `say` there).

## Notes

- Load and chaos work needs no provider credentials: use the mock provider and fixtures, so it can run in CI.
- A missed performance target is an engineering decision (optimize or revise with evidence), never a fabricated pass.
