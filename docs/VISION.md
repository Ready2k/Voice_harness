# Vision

## The problem

Speech-to-speech (S2S) models can hold a natural spoken conversation and call tools in the same turn. For a regulated
service such as retail banking, that raises questions a demo cannot answer:

- Does the model verify identity before it discloses or acts?
- Does it say what the system of record says (amounts, dates, identifiers), out loud, to the customer?
- Does it treat consent as an explicit, spoken, unambiguous act?
- What happens on interruption, a slow or failing tool, a noisy line, or a vulnerable caller?
- If it misbehaves, was the harm stopped, and can anyone later prove what happened?

Text-only evaluation cannot answer these. The model's reply is *audio* that reaches a person before anyone can read
a transcript. Providers also differ in the features that matter for safety: truncation, barge-in, usage reporting,
tool plumbing and transcripts. Vendor claims about them need checking.

## What Voice Harness is

A local-first, vendor-neutral **evaluation and assurance harness** for realtime voice agents in regulated
conversations. It is not a production banking service. All data is synthetic, all tools are sandboxed, and no real
money or customer record is ever touched (spec §1).

It does four things:

1. **Contains.** Every model proposal passes through an independent guardrail before a tool runs, and every model
   utterance is held as pending audio until a policy decision releases it. The model under test cannot talk or act
   around the gate.
2. **Exercises.** A catalogue of scripted and model-driven scenarios (personas, locales, noise and narrowband audio,
   injected faults, interruptions) drives live provider adapters through the same path.
3. **Judges.** Deterministic checks are authoritative for protocol safety. Independent ASR re-transcribes what the
   model actually said, and AI judges add advisory findings that are calibrated before they are trusted.
4. **Proves.** Every run is an append-only, hash-chained evidence ledger exported as a signed bundle that can be
   verified offline.

## Principles

- **Evidence over assertion.** Capabilities are *Verified*, *Observed*, *Hypothesis*, *Unsupported* or *Unknown*
  (spec §2). Unknown stays Unknown. Capability reports expire after 7 days.
- **Fail closed.** Missing evidence yields `untestable`, never a pass. An evidence-write failure blocks tool calls
  and releases. Empty independent ASR does not become "no claim made".
- **No ranking.** The harness reports verified support per provider family. It does not score or rank providers,
  and coverage exports carry `ranking: null`.
- **Deterministic first.** AI judges are advisory. No calibration policy is approved and no AI blocking is enabled.
  A judge never mutates banking state or overrides a deterministic control.
- **Independence.** The judge must not be the model under test. ASR for assessment is separate from the provider's
  own transcript.
- **Honest limits.** Fixture audio is silence with scripted metadata, so fixture tests prove enforcement mechanics,
  not recognition accuracy. Most live results are single samples. We say so.
- **No certification claim.** Passing this harness never means a model is compliant, safe or fit for deployment
  (spec §1, out of scope).

## Who it is for

| Role | Uses it to |
|---|---|
| Engineering teams choosing or integrating an S2S provider | Compare verified capability and observed behaviour on identical scenarios, with cost and latency recorded as unknown until prices are established |
| Risk, assurance and model-governance reviewers | Inspect findings, review judge disagreements and verify signed evidence offline |
| Platform and tooling teams | Run the same scenarios in CI against the deterministic core, with a mock provider and no credentials |

## Roadmap

Phases follow spec §16 and are gated by acceptance criteria (AC-01…AC-21), not elapsed time.

| Phase | Theme | State |
|---|---|---|
| 0 | Verification and design: capability report schema, probes, frozen schemas | Complete |
| 1 | Deterministic core: orchestrator, ledger, synthetic MCP bank, playback gate, mock provider | Complete |
| 2 | First adapters and coverage: OpenAI Realtime, Amazon Nova Sonic, AC-09, AC-20 | Complete |
| 3 | Judges and observability: signing, cost, calibration, review, comparison, independent ASR | Complete |
| 4 | Breadth and depth: xAI Grok Voice (merged), Gemini Live (in progress), scenario catalogue, faults, live API runs | In progress |
| 5 | Hardening: security review, load and chaos tests, reproducibility, regression gates | Ongoing |

### Next

- Finish Gemini Live and report the verified-provider count honestly.
- Repeat live runs to get variance instead of single samples.
- Build a calibration holdout from real live transcripts; the current synthetic holdout is exploratory.
- Establish real tariffs (all shipped prices are `null`) so cost exports can be known.
- Exercise reconnect/resume, direct MCP and audio alignment, which are Unknown for every provider.
- Move the synthetic caller off macOS `say` so live runs can run inside the container.

See [Phase 4 status](PHASE4_STATUS.md) for what is not yet established.
