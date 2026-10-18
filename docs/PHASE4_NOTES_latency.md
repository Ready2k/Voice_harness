# Matched component latency, 37732bb vs 89fa78f (18 October 2026)

Method identical to the 89fa78f study (see `docs/CODE_SPEC_RECHECK_89fa78f.md`): the `make benchmark` container command, 12 component profiles x 1,000 observations plus 3 TCP WebSocket profiles x 1,000, 20 ms pacing, 8 vCPU / 1 GiB, immutable git-archive build contexts, fresh PostgreSQL database per arm, sequential runs, seed 20261018 (order: 89fa78f, then 37732bb). Component latency only: synthetic PCM silence, no live providers, no paid calls, no end-to-end claim.

Artifacts: `artifacts/matched-latency-37732bb-2026-10-18.json`, `artifacts/matched-latency-89fa78f-control-rerun-2026-10-18.json` (control re-run this session), `artifacts/matched-latency-37732bb-vs-89fa78f-2026-10-18.json` (comparison, hashes, commands, host). The earlier 89fa78f artifact is untracked in the main checkout and is referenced by SHA-256.

Results (total_path p95, 12 profiles): all 12 profiles recorded 1,000 samples, no reported failures/discontinuities.

- vs earlier 89fa78f artifact: 11 of 12 lower, 1 higher (+0.44 ms); max 13.13 ms vs 13.70 ms. Instrumented durable_write max p95 12.06 vs 12.63 ms; policy_evaluation 0.016 vs 0.030 ms.
- vs same-session 89fa78f control: 9 lower, 3 higher (max +2.36 ms). The control run is unreliable: one profile (10 sessions, 4x, instrumented) had p95 112 ms and WebSocket 5/10-session p95 was 40/29 ms, versus about 6-14 ms earlier, so same-session "improvements" are mostly control noise.
- WebSocket p95 (1/5/10 sessions): 8.70 / 5.45 / 4.28 ms for 37732bb vs 10.94 / 6.14 / 14.21 ms in the earlier 89fa78f artifact. The ten-session increase seen between 31ccddd and 89fa78f did not reproduce on 37732bb in this run.
- Faults: slow storage 29.68 ms observed (25 ms injected); stalled consumer rejected at 96,000 bytes.

Caveats: single pair, one order, no significance testing; host load not instrumented; control re-run variance shows identical source can differ by large margins in p95 on this host. No regression is evident for 37732bb, but this does not prove absence of one. The ten-session WebSocket follow-up remains open for the earlier 89fa78f finding.
