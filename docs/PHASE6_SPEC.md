# Phase 6 spec: transport latency study (proposed 2026-10-29)

Status: proposal, nothing built. Phase 5 (hardening) is complete; this phase replaces its deferred item 9 (WebRTC) with a
narrower question that needs no change to the playback gate.

## Question

How much does the transport between the harness and a provider change first-audio latency for a real caller? Today every
provider is called over its own WebSocket or stream, and first-audio latency is taken at the harness
(`bakeoff_metrics.py`). A real caller on WebRTC may see lower latency from providers that offer it. We do not know by how much.

## Why a study and not a transport rewrite

- The harness-to-browser leg is loopback and adds a few milliseconds (Phase 5 item 3: pacing lag about 3 ms up to 12 sessions). It is not
  where the delay is.
- The delay worth measuring is on the provider leg. The harness terminates that leg itself (in the adapter), so audio still
  enters the approval buffer and the playback gate unchanged. This meets the platform spec ("Component responsibilities": no WebRTC track may bypass the gate)
  without browser-side WebRTC. Adding WebRTC from harness to browser is out of scope.
- Fairness: not every provider offers WebRTC. Results are a separate, labelled study. They never enter the bake-off
  scorecard, head-to-head tables or coverage ranking.

## Constraints

- No spend by default. Every stage below up to S3 is free (docs, mock, loopback). S4 spends money only after the operator
  approves a written budget; the study stops at the cap.
- Synthetic data only. Same scenarios and caller audio as the fair bake-off, so differences are transport, not content.
- Claims stay at the strength of the evidence: single samples are called single samples; intervals, not point estimates.

## Stages and gates

| Stage | Work | Cost | Done when |
|---|---|---|---|
| S1 | Capability survey: which of the six providers document a WebRTC (or other lower-latency) path, with the doc cited, terms and auth model, and what the server side must terminate | none | `docs/PHASE6_NOTES_transport_survey.md`; providers without a documented path are listed as out of scope, not guessed |
| S2 | Design note: where the harness terminates the media leg, how audio reaches the approval buffer, how first-audio is timestamped identically on both transports (the same `speech_end` to first-audio basis), clock and jitter-buffer treatment | none | Reviewed note; the measurement basis cannot differ between transports |
| S3 | Free rig: a loopback mock provider that serves both transports with an injected, configurable network model (delay, jitter, loss). Shows the measurement method detects a known latency difference and reports none when there is none | none | Tests in `make test`; a planted 40 ms difference is recovered within tolerance and a null run shows no difference; mutation check on the timestamp basis |
| S4 | Live study: for each in-scope provider, the same scenarios on the existing transport and on WebRTC, paired and interleaved, N repetitions | operator approves a budget first | Per-provider paired difference in first-audio latency with a confidence interval, run costs from the existing cost export, and a stop at the budget cap |
| S5 | Report: clearly labelled, separate from the bake-off report, with the limits (one network, one host, synthetic callers) | none | Report and run notes committed; README and status updated |

## Out of scope

- WebRTC between harness and browser; any change to `playback.py` or the browser scheduler.
- Ranking providers by this study; adding WebRTC as a default transport.
- Telephony (PSTN/SIP) paths, which have their own latency and belong to the telephone pilot.

## Acceptance

- AC-P6-1: the survey cites documentation for every provider claimed in scope.
- AC-P6-2: the measurement basis is identical across transports and is covered by tests that fail if it diverges.
- AC-P6-3: the free rig recovers a known injected difference and reports none for a null run.
- AC-P6-4: the live study stays within its approved budget and every run verifies offline like any other run.
- AC-P6-5: no result from this study appears in a bake-off scorecard, coverage page or head-to-head table.

## Open questions

- Which providers actually offer a documented WebRTC path (S1 answers this; the answer may be fewer than three).
- Whether a single host and network is enough to say anything about real callers (probably only a lower bound).
- Budget for S4, to be set by the operator after S1 lists the providers and the existing per-run costs.
