# Voice Harness decision briefing

Prepared 31 October 2026. The presentation treats the harness as a prototype throughout. The recommendation is an analytical judgement: retain the Connect baseline while running matched native, hybrid and third-party pilots. The existing benchmark cannot establish that a migration will improve a deployed contact-centre journey.

## Evidence used

The authoritative numerical snapshot is `artifacts/bakeoff-2026-10-29-standard-5/report.json`, generated 30 October, with its accompanying `run-notes.md`. It contains 600 newly run standard-prompt observations across five providers and 120 GPT-Live observations carried over from 28 October. The latter have different prompts and caller/voice settings. The deck displays them separately with an asterisk. No new evaluations were launched to produce this briefing.

The scorecard uses `report.scores` for quality scores and raw assurance, task-completion and factual counts. The quality score is `composite_excluding_latency`. It is group-balanced and reweights the non-latency dimensions, so it is not a raw success percentage. The counts use each measure's recorded `k` and `n`. For example, Realtime and Gemini both have 35/36 tool-completion observations but different weighted task-completion values because the failures occur in differently weighted scenario groups.

The quality lead interval in slide 9 is `report.verdict.quality_lead`, rounded to three decimals. It spans zero. The report applies scenario-cluster bootstrap uncertainty (1,000 resamples, 40 scenario clusters) and adjusted paired comparisons. With three repetitions and a narrow synthetic catalogue, the evidence does not establish equivalence or absence of rare risk.

Latency is taken from `report.latency`, rounded from milliseconds to seconds. It measures first provider audio from caller speech end, not useful answer completion or caller-perceived latency. Explicit-commit providers use a 500 ms harness delay. Provider endpointing differs. GPT-Live timing is estimated. These are descriptive values, not a speed ranking.

Slide 10's targeted Nova prompt experiment comes from the run notes. It is a separate diagnostic outside the ranked cohort, with three repetitions per case. The notes report tool calls; scored verdicts were not reviewed. The handoff moved from 1/3 to 3/3, and transaction identifiers from 0/3 to 2/3. Those hints were tailored to the observed scenarios, so holdout validation remains necessary.

Slide 11 describes seven ASR-blocked reruns and three ElevenLabs adapter-fault reruns. They replaced earlier samples. The previous attempts and costs remain recorded. Only three ElevenLabs cells used the repaired adapter, while the other 117 retained the earlier version. The corrections improve evidence validity but do not make the cohort a pristine controlled experiment.

The latest report records no matching calibration report for its two judge bindings. Broader repository calibration work does not override that run-level disclosure. Mandatory assurance checks are deterministic; CARE-01 has judge authority but is outside the assurance composite. No judge policy is approved for blocking. OpenAI ASR assesses OpenAI agents too, which is a disclosed same-vendor dependency.

Historical scorecard costs exclude judge spend and some overhead. The README reports subsequent metering improvements. Neither supports a full production cost-per-resolution comparison. The presentation therefore avoids a cost winner or ROI claim.

Additional internal sources:

- `README.md`: capability inventory, prototype boundaries and recorded conversation library.
- `docs/BAKEOFF_SAFEGUARDS.md`: preflight, pause/retry handling, scoring and uncertainty.
- `docs/BAKEOFF_ASSESSORS.md`: deterministic checks, judges, blinding and ASR dependencies.
- `docs/INTERACTIVE_CALLER_SCOPE.md`: bounded live AI caller pilot.
- `docs/DELEGATE_PHONE_RUNBOOK.md` and `docs/DELEGATE_ADAPTIVE_SCOPE.md`: telephone diagnostics and outstanding adaptive acceptance criteria.

## External research

Official sources accessed 31 October 2026:

1. [AWS: Agentic CX designer](https://docs.aws.amazon.com/connect/latest/adminguide/acxd.html). Establishes ACXD as the conversational application layer integrated with Connect flows. Supports visual design, tools, knowledge, guardrails and lifecycle management.
2. [AWS: Testing your application](https://docs.aws.amazon.com/connect/latest/adminguide/acxd-testing.html). Describes application, flow, routing and repeatable flow-logic tests and debugging.
3. [AWS: Connect Customer Testing and Simulation](https://docs.aws.amazon.com/connect/latest/adminguide/testing-simulation.html). Establishes native automated test capabilities.
4. [AWS: Flow-action outcome validation](https://aws.amazon.com/about-aws/whats-new/2026/10/connect-customer-validate-flow-actions/). October 2026 update to native testing. The harness is not positioned as filling an absence of native AWS testing.
5. [AWS: External AI agent integration](https://docs.aws.amazon.com/connect/latest/adminguide/a2a-setup-external.html). Supports a hybrid strategic option. External agents must implement Connect's A2A extensions. The documented audio path requires immediate handoff to one external collaborator and restricts the handing-off agent's prompt, guardrail and tools. This is not evidence of current harness compatibility or implementation by every provider in the scorecard.
6. [ElevenLabs: Agent Testing](https://elevenlabs.io/docs/eleven-agents/customization/agent-testing). Documents conversation simulations and repeated probabilistic testing, illustrating that specialist providers also supply native evaluation tools.

## Proposed decision work

The six-week plan and acceptance framework are illustrative consulting recommendations, not completed work or commitments. A proper pilot needs the actual Connect/ACXD build, actual third-party configuration, matched business data and tools, retained contact evidence, randomised ordering, independent human listening, and total operating costs. Integration access and account/region availability must be confirmed.

Retaining Connect with an external conversational agent is distinct from replacing the contact-centre platform. The deck recommends testing both where useful, rather than treating model selection as sufficient grounds for migration.
