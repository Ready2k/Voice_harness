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
| 2 | Performance regression gate | regression gates | done 2026-10-29 | Implemented (`make perf-gate`, `voice_harness.perf_gate`, `artifacts/perf-baseline.json`, CI step); the first CI run (40852009154) failed against a baseline taken on a Mac: the 4-vCPU runner is faster at 1 session (p95 about 2.4 ms against 9 ms) but about 2.5x slower at 10 sessions (component p95 25-27 ms, WebSocket p50 37 ms / p95 45 ms against 4.5 ms), so the baseline was rebuilt from that CI run. CI passed twice against it (run 40853813868, original and rerun), then failed once (40857717192) on `websocket/sessions=5` alone (p95 24 ms against a 4.9 ms baseline taken from a fast run). The baseline is now the worst p95 per profile over four CI runs (`perf_gate baseline` accepts several results), and all four runs pass against it. `websocket/sessions=5` is bimodal on shared runners, so its limit is wide (about 48 ms); runner spread is wide (10-session component p95 17-26 ms), so the tolerance is coarse and only catches large regressions. The 10-session saturation on 4 vCPUs is an input to item 3. CI compares `make benchmark` output to a stored baseline with a stated tolerance and fails on a miss |
| 3 | Harness load test (scoped down 2026-10-29) | §11, §18 | done 2026-10-29 | `make load-test` (`voice_harness.load_test`, mock provider, no spend, not in CI) steps 1/6/12/24 concurrent sessions and reports turn latency, pacing lag, CPU and throughput. Result: `artifacts/phase5-load-2026-10-29.json`. It measures the harness as the instrument, not any provider: bake-offs run up to about 6 providers at once, so the question is how much concurrency is safe before the harness skews provider timings. Runtime selection (§18) is deferred until a real scale requirement exists |
| 4 | Chaos tests | chaos | mostly done 2026-10-29 | `tests/test_chaos.py` (in `make test`, SQLite and PostgreSQL): a failure after the first staged event leaves no partial evidence and the retry is exactly-once; an outage during release does not consume the token; random concurrent outages (120 calls, 6 threads) never lose, duplicate or gap the ledger; an unreachable OTLP collector neither blocks nor loses evidence. `make chaos-postgres` (opt-in, not in CI) stops the real PostgreSQL container for 6 s mid-run. Not covered: provider disconnect mid-turn (adapter tests in `test_provider_failures.py` cover the adapter side only), slow-but-working storage under load, and a partial network partition |
| 5 | Retention tests | retention | done 2026-10-29 | `tests/test_retention_timeline.py` (in `make test`, SQLite and PostgreSQL), driven by a simulated clock: a run-wide hold blocks the 30-day sweep, the expiry notice appears 7 days before expiry, the 7-day grace after expiry holds, then the sweep proceeds; renewal extends protection; audio goes at 7 days while metadata waits 30 (held audio survives to the end of the grace); 150 days of daily sweeps add at most one purge record a month and the chain verifies throughout; NDJSON and reviewer exports of a tombstoned run verify and carry none of the purged text; an export taken before the purge stays valid; the real API scheduler loop performs the sweep. The two gaps found on the way (below) are fixed and covered |
| 6 | Reproducibility checks | reproducibility | open | Same scenario and seed gives the same ledger hash; contracts drift check stays green in CI |
| 7 | Security review | security review | open | Review of auth (operator token), approval-token single use, WebSocket origin, export/download links, secrets handling; findings tracked |
| 8 | Dependency audit | pinning | open | Vulnerability scan of `requirements.lock`, npm lockfiles and base-image digests; update cadence documented |
| 9 | Optional: WebRTC transport | §13 | deferred | Only after 3 and 4, and only with an equally enforceable playback gate |

## Retention findings (2026-10-29)

- **Fixed (was: projection tables kept purged content):** RT-02 used to tombstone the ledger payloads only. Transcript text, tool-result messages and judge rationales stayed readable in `harness_transcript_segments`, `harness_tool_calls`, `harness_judge_findings` and the run state after 30 days, against the spec's "metadata retained 30 days". `retention.scrub_projections` now clears them in the same transaction as the tombstoning: transcript text and judge rationales become empty, tool results keep status, error code and citations but lose their body, unit transcripts in the run state are emptied. Hashes, verdicts and ids stay. Rows tied to events a hold still protects are left alone, and the scrub runs on every sweep, so runs tombstoned by the old code are backfilled on the next sweep (the API sweeps every 30 s).
- **Fixed (was: audio outlived its seven days):** the held PCM inside each approval unit in the run state (`units[*].pcm`, base64) was never purged, so synthetic audio stayed in the database indefinitely even after the seven-day artifact purge deleted the files. `Evidence.purge` now drops it after seven days for terminal runs, honouring run-wide and artifact-scoped holds and their grace. Live runs keep it, because they may still release it.
- Not scrubbed, on purpose: `harness_human_reviews` is never written by the code today, `harness_fixture_snapshots` and `harness_scenario_definitions` are synthetic fixtures, and `harness_provider_capability_reports` is global. Revisit if reviews start being stored there.
- The scrub itself is not recorded as a ledger event; the tombstone records and the `retention.metadata_purged` event are the audit trail.
- Mutation checks: removing the 7-day grace, shortening metadata retention to 7 days, lengthening audio retention to 30 days, and (for the two fixes) skipping the tool-result, transcript or backfill scrub, ignoring holds, or purging audio on live runs each fail a specific new test.
- Test-authoring note: `Event.occurred_at` reads `time.gmtime()`, not the injected engine clock, so simulated-time tests must patch it too (done in this file's setUp). Without it a fresh purge record looks 30 days old immediately.
- Not covered: filesystem garbage collection after a crash between tombstone and unlink, and retention across a real process restart.

## Load test result (2026-10-29, PostgreSQL, 8-CPU container limit, mock provider)

| Sessions | Turn p95 (ms) | Pacing lag p95 (ms) | Throughput / offered (turns/s) | CPU cores used |
|---|---|---|---|---|
| 1 | 8.5 | 3.3 | 49 / 50 | 0.12 |
| 6 | 11.6 | 3.0 | 283 / 300 | 0.37 |
| 12 | 8.9 | 2.8 | 533 / 600 | 0.34 |
| 24 | 20.5 | 17.0 | 982 / 1200 | 0.67 |

Up to 12 concurrent sessions (twice the largest bake-off) the harness adds roughly 3 ms of scheduling lag. At 24 it falls behind its own schedule (82% of offered turns, lag p95 17 ms) while using under one core, so the limit is single-process serialization, not CPU. One run; single-run figures carry the runner variance seen in the perf gate. Not measured here: memory limits, multi-process or multi-host scale, evidence-write failure (item 4). Findings that follow: keep bake-off concurrency at or below 12 sessions per process, and do not read first-audio latency taken above that as harness-free.

## Chaos results (2026-10-29)

- Mutation check: removing the commit-time outage check in `storage.py` makes the mid-transaction test fail, so the new tests do detect that regression (source restored).
- Real PostgreSQL stop/start (`make chaos-postgres`, 25 s run, database down for about 6 s): 302 calls acknowledged, ledger verified, each call recorded exactly once, no sequence gaps. One call blocked for 7.8 s inside the connection pool's 10 s wait before storage returned, so a database outage stalls callers for up to the pool timeout rather than failing fast; whether that is acceptable for live sessions is an open decision.

## Carried over from Phase 4

Variance beyond 3 repetitions; re-probing capability reports before they expire (2026-11-04/05, re-run the probes and
`scripts/run_ac09.py`); live synthetic-commit test (no live model has proposed a commit); locale-aware caller voice; judge
calibration policy approval (blocking stays off); provider tariffs in the shipped pricing table; Nova runs re-run so they
are priced; live runs inside the container (no credentials or `say` there).

## Notes

- Load and chaos work needs no provider credentials: use the mock provider and fixtures, so it can run in CI.
- A missed performance target is an engineering decision (optimize or revise with evidence), never a fabricated pass.
