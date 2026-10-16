# Phase 0 decision records

Status: **APPROVED by user 2026-10-16.** Each restates a spec default; none is new policy.

| ID | Decision | Source |
|---|---|---|
| D-01 | Harness media convention: PCM s16le, mono, 24 kHz, 20 ms chunks (480 samples / 960 bytes). Adapters resample only if the verified native rate differs; record the transform and its cost. | spec §14 (l.781) |
| D-02 | Browser-to-harness control: WebSocket. Provider-facing transport stays native. | spec l.781 |
| D-03 | Storage: PostgreSQL evidence ledger, append-only; local object storage for audio artifacts, content-hash checked. | spec §13 |
| D-04 | Retention: metadata 30 days; synthetic audio opt-in, 7 days; secrets never retained in general telemetry; purge appends a tombstone. | spec l.582 |
| D-05 | Privacy: minimal retention by default; replay suites explicitly enable audio retention; holds override expiry but not secret exclusion. | spec l.582-590, l.1010 |
| D-06 | Capability reports expire after 7 days and invalidate on config/SDK/endpoint/model change; fail closed on missing primitives. | spec §3 |
| D-07 | Schema freeze: `contracts/` JSON Schemas and event models are the Phase 1 baseline. Frozen at git tag `phase1-baseline` (commit b548b98); later changes to `contracts/` or event models require a new schema version and a recorded decision. | spec §16 |

Not decided (need user/provider input): supported input codecs beyond PCM, any jurisdiction-specific retention duty.
