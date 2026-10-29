# Who assesses a bake-off run

Updated 22 October 2026.

Each bake-off observation carries `assessors` (`judges/assessors.py::assessor_identity`), read from the run's
ledger. It records which models produced the evidence, so a reader can judge it. It changes no verdict.

- `judges[]`: judge id, provider, model id, family, `host` (who serves it: any Bedrock model is `aws`) and `vendor`
  (who made it: a Bedrock Claude is `anthropic`, Nova is `amazon`), prompt template, evaluation version, status,
  independence disclosures (`SAME_VENDOR_AS_TESTED_MODEL`, `SAME_HOST_AS_TESTED_MODEL`, `SAME_FAMILY_AS_TESTED_MODEL`,
  `DUPLICATE_MODEL_NOT_INDEPENDENT`; `SAME_PROVIDER_AS_TESTED_MODEL` is kept and now means same host, so a Bedrock
  judge on a Nova run raises it)
  and a `calibration` summary (newest stored holdout report for that exact judge, template and rubric: precision,
  recall, abstention, error rate and reviewer agreement per criterion; `no_calibration_report` if none). Holdout
  agreement is measured on synthetic labelled examples; it is not accuracy on live runs.
- `judges` is empty when no judge was configured. Judge-authority criteria such as CARE-01 are then untestable.
- `asr[]`: backend, vendor, model and reply count for the independent ASR that transcribed each reply. Safe-speech
  and FACT-01 verdicts depend on it. `asr_disclosures` contains `ASR_VENDOR_SAME_AS_TESTED_PROVIDER` when the
  transcriber's vendor is the tested provider (OpenAI ASR scoring OpenAI Realtime).
- `summarise_assessors(observations)` rolls these up per provider; `spot_check_sample(observations, per_group, seed)`
  picks a reproducible set of passes and non-passes per provider for human review.

## Judge blinding and independence

Updated 27 October 2026 (`PROVIDER_BIAS_REVIEW_2026-10-27.md` findings 4 and 6).

- The judge oracle (`judges/oracle.py`) leaves out provider-session events (`adapter.native_event`,
  `adapter.session_ready`, `cost.*`, `benchmark.adapter_metrics`), strips identity keys everywhere (the
  `run.live_binding` `adapter` and `provider` session metadata, model/voice/region/endpoint/SDK fields, ASR and caller
  backend names), replaces provider-issued `call_id`/`response_id`/`item_id` values with stable hashes (linkage kept),
  and masks every remaining string case-insensitively. Mask tokens are derived from `live_providers` (config ids,
  families, model ids, provider names and their distinctive parts), adapter class names, endpoint domains and default
  voices read from the adapter sources, plus a brand list (`openai`, `gpt`, `amazon`, `aws`, `bedrock`, `nova`,
  `google`, `gemini`, `xai`, `grok`, `elevenlabs`, ...) and known voice names. Spoken self-identification ("I'm
  Gemini, made by Google") is masked the same way.
- `blinding.provider_identity_hidden` is measured: the oracle actually sent is scanned for identity tokens, and any
  survivor sets it false and is listed in `limitations`. It cannot detect reply style or tool-use habits, so a judge
  may still guess a provider.
- Same-vendor judges (an OpenAI judge on an OpenAI provider, a Bedrock Nova judge on Nova) are disclosed. In a
  `material_benchmark` they are ineligible (`SAME_VENDOR_IN_MATERIAL_BENCHMARK`), because self-preference would
  count toward coverage. Outside material benchmarks they still run, disclosed, and `cross_family_judge_present`
  needs a judge whose vendor and family both differ from the tested model.

## ASR configuration

- Default ASR model: `gpt-transcribe` (OpenAI's non-deprecated replacement). `gpt-4o-transcribe` and
  `gpt-4o-mini-transcribe` shut down on **2027-02-26**; `gpt-live-transcribe` is the live-stream alternative.
  `VOICE_HARNESS_ASR_MODEL` overrides the model, and every assessment records the model used.
- Request shape: `POST /v1/audio/transcriptions` with `file`, `model`, `response_format=json`, `temperature=0`. OpenAI's
  speech-to-text guide confirms the endpoint and `file`/`model` for `gpt-transcribe` but does not mention `temperature` or
  `response_format` for it, so a 400 that names either field is retried once without it (`AsrResult.dropped_params`);
  any other 400 is `ASR_HTTP_400` (unavailable, fail closed). Not verified live.
- `VOICE_HARNESS_ASR_SECOND_MODEL` (off unless set, e.g. `gpt-4o-transcribe` until it shuts down) transcribes each reply
  with both models in parallel. The first model's text is the transcript of record. If the two disagree on any amount,
  date or ambiguity, the reply is `untestable` with `ASR_DISAGREEMENT` and is held; FACT-01 is untestable for that unit.
  It doubles ASR cost and latency per reply (each model's usage is recorded separately). Both models are OpenAI, so errors
  can correlate: this catches model-specific mishearings, not systematic ones. Second-model failure means ASR unavailable.
- No other vendor is usable: Amazon Transcribe is a separate AWS service with no code, dependency or tariff here, and
  Bedrock Converse transcription was not verified.
- Prices: `artifacts/pricing/vendor-pricing-2026-10-24.json` (as of 2026-10-24) adds per-minute transcription estimates:
  `gpt-transcribe` $0.0045, `gpt-4o-transcribe` $0.006, `gpt-4o-mini-transcribe` $0.003, `gpt-live-transcribe` $0.017.
  OpenAI's page does not split audio and text tokens for these models and lists no token price for `gpt-transcribe`, so the
  rows price the `asr_seconds` meter (a provider-reported duration). A run whose ASR reports only tokens stays unpriced for
  `gpt-transcribe` (unknown, never zero). When the provider returns no usage at all, the recorder now prices the audio
  length the harness measured (`AsrResult.audio_seconds`) at the per-minute rate. That cost line has provenance
  `estimated`, `estimation_method` `harness_measured_audio_seconds` and a `HARNESS_MEASURED_NOT_PROVIDER_REPORTED`
  assumption; provider-reported usage is always preferred, and offline fixtures and failed requests stay unknown.
  `make serve-live` loads the 2026-10-29 table by default (prices unchanged from 2026-10-24).

## Judge configuration

- OpenAI judge default: `gpt-6-luna` ($0.10 input, $0.01 cached, $0.50 output per 1M tokens, short context; tariff added).
  `gpt-4.1-mini` shuts down on **2027-01-14**; set `VOICE_HARNESS_OPENAI_JUDGE_MODEL` to keep it.
- Bedrock judge default: the calibrated Claude Haiku 4.5, `us.anthropic.claude-haiku-4-5-20251001-v1:0`
  (`PHASE4_NOTES_bedrock_calibration.md`). The Claude 5.5 models were refused on the project's AWS account
  (`AccessDeniedException`, not available for this account) on 2026-10-24: both Haiku 5.5 and Sonnet 5.5 are
  Marketplace-billed and need model access enabled. Once enabled, select them with `VOICE_HARNESS_BEDROCK_JUDGE_MODEL`:
  `us.anthropic.claude-haiku-5-5` (the bare `anthropic.claude-haiku-5-5` has no in-Region endpoint; `global.` also exists;
  adaptive thinking is on by default, so temperature 0 and reasoning blocks need a live check first) or
  `us.anthropic.claude-sonnet-5-5` (priced from Anthropic's official page, global $2.00 in / $10.00 out per 1M tokens, plus the
  10 percent premium that page states for regional and multi-region endpoints, so $2.20 / $11.00 for the `us.` profile; the
  `us.` figure is derived, not read from an AWS page).
- A new judge model has no stored calibration report: the run records `no_calibration_report` and `judges[].calibration` says
  so, with no criteria. Calibration is matched on the exact provider, model, template and rubric version, so the Haiku
  report never applies to Sonnet. Run `scripts/calibrate_bedrock_judge.py` to produce one.
- Judges send temperature 0. If an OpenAI model rejects the parameter (HTTP 400 naming it) the call is retried once
  without it and recorded (`temperature` null, `temperature_fallback` true). The API's resolved model string is recorded as
  `model_version` (OpenAI returns one; Bedrock Converse does not, so the pinned id is the version).
  `judge.offline_evaluated` gained `temperature`, `temperature_fallback` and `model_version` (optional; older events
  read as unrecorded).
- Other uses of `gpt-4.1-mini` (`model_simulator.py`, `delegate_dialogue.py`) are not judges and are unchanged.

## How many judges

Only CARE-01 (non-mandatory, outside the assurance verdict and the composite) is decided by a judge. IDENTITY-01, FACT-01,
CONFIRM-01, AUDIO-01 and SCENARIO-PROPOSAL-01 are deterministic: a judge can only add advisory dissent and a review item, or
turn an untestable result into `review_required`. The bake-off's safe-speech and factual rates come from the ASR plus rule
checks, not an LLM. So a second judge changes no scored number today. `rubric.required_judge_count` requires two judges
only for a mandatory criterion that has judge authority (none yet); otherwise one cross-family judge. Setting
`material_benchmark` for a bake-off would be harmful: with one judge, every non-failing run becomes `review_required`.
`judge_disclosure` on each observation says why judge evidence is thin: `NO_JUDGE_RAN`, `NO_CROSS_FAMILY_JUDGE` or
`JUDGE_COVERAGE_INCOMPLETE` (null when met).

Not recorded: a provider-side version behind an alias beyond what the API returns.
