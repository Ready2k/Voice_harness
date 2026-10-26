# Full assessed bake-off with advanced prompts

The `advanced-v1` profile applies provider-specific conversation style to the complete banking scenario catalogue. It retains all `standardised-v2` banking instructions verbatim: identity verification, successful-tool-result grounding, exact amounts and currencies, confirmation IDs, and instruction authority. All banking tools and strict speech assessment remain enabled.

It deliberately does not copy the listening test's fixture facts, “already verified” assumption, or “no tools” instruction. That test remains a separate blind voice comparison. Advanced prompts are documentation-informed starting configurations, not proven optimal prompts.

For identity-policy questions, the profile explicitly requires `get_policy_text` (`identity`, version `1`) before answering. This is needed to demonstrate real tool use in the voice-and-tool preflight; answering from the system instructions alone does not pass that gate.

## Run and resume

```sh
.venv/bin/python scripts/full_advanced_bakeoff.py \
  --out artifacts/full-advanced-bakeoff-2026-10-26 \
  --data .local/full-advanced-bakeoff-2026-10-26 \
  --repetitions 3
```

The plan covers all 40 scenarios × five providers × three repetitions = 600 scored cells, plus separate preflight checks. This is the current 600-cell scheduler limit. Group-balanced scoring is enabled. Provider traffic and independent ASR incur charges; the existing voice pricing table is used for the report. Costs can remain partial or unavailable where provider usage or tariffs are missing.

The runner uses a separate SQLite database and the real API lifecycle, MCP tool subprocesses, strict speech gate, independent ASR, deterministic assessment and any already configured optional judges. Optional judge coverage is disclosed in the report; human-like interaction is still assessed through the separate blind review.

The scheduler allows six simultaneous jobs, at most two per provider. It requires all five providers to pass voice-and-tool preflight, retains attempts, allows only the existing bounded retries and pauses on access rejection or repeated infrastructure failures. It does not bypass those checks to finish a plan.

To resume after investigating and fixing the reported cause:

```sh
.venv/bin/python scripts/full_advanced_bakeoff.py \
  --out artifacts/full-advanced-bakeoff-2026-10-26 \
  --data .local/full-advanced-bakeoff-2026-10-26 \
  --repetitions 3 --resume
```

Using the same output folder reuses the saved bake-off rather than creating another paid plan. Prompt changes are rejected after any scored cell has launched. Preflight-only refinements archive the previous prompt manifest in `prompt-history/`; actual instructions and tool schemas are also bound by hashes in every live run's ledger.

## Review

The output folder contains:

- `status.json`: progress and any pause reason.
- `report.html`: self-contained interactive assessed report, exported when the run completes or pauses.
- `report.json` and `payload.json`: observations, attempts, statistics and preflight history.
- `prompts.json`: exact requested instructions and tool schemas per provider.
- `verified-prompt-bindings.json`: provider prompt bindings checked against the run ledgers.

The local data folder contains the underlying run database, signed manifests and local secrets. It must not be committed or uploaded. The runner does not enable raw audio retention or export credentials. No artifacts are automatically pushed.

The API accepts `prompt_profile: "advanced-v1"` on both run and bake-off requests. The bake-off page offers **Advanced provider-specific prompts**, and cloning preserves the profile. Existing defaults remain `standardised-v2`. Restart an existing API process after updating the code so that its request schema recognises the new profile.

## First-run setup history

Before scoring began, the first preflight exposed a missing child-process `PYTHONPATH`. The runner was corrected and the unsuccessful preflight was retained. A subsequent check found that OpenAI answered the policy question without calling the policy tool, while the other four providers completed the required exchange. Explicit policy-tool routing was then added for every provider, the previous prompt manifest was archived, and a fresh five-provider preflight passed. Scoring began only after that final preflight; scored prompts were not changed afterward.

## Completed comparison

The [26 October report](../artifacts/full-advanced-bakeoff-2026-10-26/index.html) contains all 600 completed cells: 588 passes, 11 failures and one untestable outcome. Nova has the fastest median first audio; the comparison does not establish a clear overall winner. Run notes disclose pauses, retries, missing judges and incomplete pricing. The reuse manifest confirms that completed calls were retained across both scored-run resumptions.
