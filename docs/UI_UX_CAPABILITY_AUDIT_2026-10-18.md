# UI / UX capability audit — 18 October 2026

Scope: specification v0.3, the current FastAPI routes and contracts, browser modules, and real local API/browser checks. This is a UI capability audit, not certification of provider safety or full backend conformance.

## Findings addressed

| Operator requirement | Current UI |
| --- | --- |
| Understand and select a task | Searchable scenario library with execution mode, expected outcome, criteria, tools, versioned definition, download, and launch handoff |
| Launch scenario / provider configuration | Grouped scenario picker, server readiness, seed and audio retention; live configurations stay unavailable when dependencies are absent |
| Follow what happened | Execution-stage graph, event timeline, immutable event IDs and inspector; raw payloads behind disclosure |
| Inspect history without moving underneath the user | Pausing freezes the current sequence. Stage buttons remain mounted across refreshes. Comparison retains each selected run's inspection state |
| Distinguish generation, approval and playback | Separate transcript version and recorded playback status; revision does not erase a previous release or acknowledgement |
| Separate model and platform outcomes | Latest assessed model/platform verdicts take priority over initial run summaries; lifecycle completion is not a task-success claim |
| Inspect costs | Run cost view and comparison statements preserve currencies, provenance, unknown components and completeness; no browser billing calculations |
| Compare providers / repeated runs | Same scenario/version validation, configuration differences, X/Y traces, all-selected summary, measured differences only when compatible, retryable matched batches |
| Inspect statistical comparison reports | Stored report viewer exposes paired counts, intervals, exclusions, version limitations and full provenance; submitted observations are explicitly distinguished from extracted ledger facts |
| Export evidence | Authenticated standard/reviewer JSON and NDJSON downloads; coverage and comparison export remain separately labelled |
| Reassess finished runs | Deterministic-only or explicit opt-in live judge selections; tested model identity comes from run evidence, never guessed for a live run |
| Human review | Paginated state-filtered queue, claim, evidence-backed decision, optional deterministic-check correction, persisted effective result and direct finding URLs |
| Preserve evidence | Create a run-wide or finding-scoped hold; explicit owner, reason and expiry; renew/release with reason; backend handles retention and access rules |
| Run again | Copy stored execution configuration and seed into one new run, with retry-safe launch identity; explicitly labelled rerun |
| Access and usability | Consistent navigation and connection disclosure, tab-only token, disconnect, visible focus, skip link, responsive layouts, restrained status colours and reduced-motion support |

## Remaining limitations / backend dependencies

1. **Recorded-audio replay:** the run API does not expose an exact recorded-input replay job. A seeded rerun is available, but is not represented as replay of retained audio. No arbitrary audio player was added to bypass signed playback approval.
2. **Custom scenario execution:** the API can validate/store immutable definitions, but marks definitions without a registered runner as not runnable. The library exposes this distinction rather than offering a launch that the backend must reject.
3. **Statistical study creation:** selecting 2–8 stored runs produces a descriptive comparison. It is not an automatic predeclared study, a twenty-repetition experiment, or evidence of statistical significance. Existing backend statistical reports can be inspected/exported with their provenance and limitations.
4. **Provider and judge availability:** credentials, capability evidence, ASR, caller synthesis and opt-in live judge configuration remain server prerequisites. No provider keys are accepted by the UI. Browser testing did not call paid providers/judges.
5. **Physical playback:** a client playback acknowledgement is software evidence. Physical audibility is not inferred.
6. **Administration:** signing-key lifecycle, pricing/invoice reconciliation, calibration-policy approval and infrastructure management remain API/CLI workflows. The spec's explicit operator UI acceptance criteria do not require all administrative routes to be browser controls.
7. **Framework:** these pages remain native JavaScript modules. This is a documented deviation from the spec's React/TypeScript default, not a claim that a framework migration occurred.

## Verification

- 52 browser regression tests pass. They cover history truncation, blocked speech, unknown measurements, comparison matching, paired batch seeds, cost completeness, hold scope/expiry, review evidence inputs and release history surviving a revision.
- All five UI pages return HTTP 200. Authenticated standard/reviewer exports both succeed in JSON and NDJSON and supply a manifest hash.
- Local browser: reran a fixture as a distinct run, saved deterministic reassessment, created/released a preservation hold, claimed and decided a new synthetic finding, and verified scenario search and run handoff.
- Synthetic decision check: `run_11b2514bad77401f83a167ca524eadc4`, `rev_70b4becde38b5920`, result `applied`, effective verdict `fail`.
- Synthetic hold check: `run_a82609594cc0433189b2e2d7e48b3358`, `hold_74ea8d440f6d4456916d2e43ef9c1c84`, subsequently released.
- Changes target browser modules, styles, UI regression tests and this audit. Cost calculation and provider execution logic were not edited.
