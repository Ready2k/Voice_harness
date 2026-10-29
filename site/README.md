# Research archive

## Customer experience review (draft)

Open `cx-review.html` through a local HTTP server for the executive-facing HTML5 page. It separates eligible task completion, scoped protection checks, spoken facts and scripted listening evidence. Amazon Connect AI Agents, ACXD and proposed Connect configurations are explicitly unbenchmarked. No Connect performance is inferred from direct provider tests.

Refresh the saved page data after adding or updating assessments:

```sh
python3 scripts/build_cx_review.py
```

To select different report exports, supply repository-relative paths with repeated `--report` arguments; the first opens by default. The source must contain the existing bake-off `report`, `observations`, `names` and `providers` schema. The generator retains source paths and SHA-256 hashes and takes rates and intervals directly from the assessment. It does not call providers, re-score results, merge experiments or infer a trend. Defaults include the 28 October final re-score and the earlier corrected advanced assessment; these use different setups.

The “What we’ve built” section uses an interactive, explicitly illustrative call replay. Select caller and service behaviour, then play, pause or step through action/speech checks and retained evidence. It is not a retrieved incident or a provider finding. The executive statistics, clickable scorecard and customer-situation bars use the selected assessment, preserve metric definitions and unresolved outcomes, and do not introduce reliability grades or new provider rankings.

Listening uses `site/voice-library.json`, refreshed by the existing catalogue builder. Those recordings remain a separate study. The Pages staging list includes the review and its generated data; deployment still uses the repository's normal workflow.

The repository-root `index.html` is the GitHub Pages landing page. It highlights curated experiments and searches all tracked files in `reports/`, `artifacts/` and `docs/`. Notes open in GitHub's Markdown viewer; web reports, audio and data use relative Pages URLs.

## Executive briefing (draft)

`exec-briefing.html` is the business-facing view: a trust-led story (airlock introduction, call replay, caller moments, scorecard, priorities, listening). It is separate from `cx-review.html`, which remains the evidence explorer.

**The numbers on this page change.** Service, situation, repetition and run counts, every score and the "clear winner" statement are read at load time from the first assessment in `site/cx-review-data.json`. Nothing is fixed in the HTML. We have run many tests and will run more, so figures such as "6 services, 40 situations, 720 calls" describe the current assessment only. The footer prints the assessment title and generation date so a stale page is visible.

After adding or updating an assessment:

1. Run `python3 scripts/build_cx_review.py` to refresh the data.
2. Re-read the **authored wording**, which the data does not update: the three hero answers, the priorities caveat, the caller persona quotes in `PERSONAS`, the call replay in `renderHow`, and the pillar copy. Check each still matches the new results.
3. Keep the scorecard thresholds (98% reliable, 93% mostly) and the choice to leave response pause ungraded and unranked unless timing methods become comparable.

`executive-trust.html` is the combined briefing: it joins the technology (architecture diagram, airlock pillars, call replay, fairness) to the results (customer moments, scorecard, priorities, listening) under one trust-led story, and ends with the decision on what to test next in Connect. It is data-driven in the same way, so the same refresh and re-read steps apply, plus the fairness and decision wording (taken from `executive-special.html`) and the architecture diagram.

The replay uses scripted service replies and real caller lines from `scenario_catalogue.py`; it is illustrative, not a recorded run.

## Publishing

In repository **Settings → Pages → Build and deployment → Source**, choose **GitHub Actions**. Push these changes to `main` (or run **Publish research archive** manually). The landing page will be at `https://ready2k.github.io/Voice_harness/`.

The Pages workflow generates the catalogue and stages only the landing page assets and tracked report, artifact and documentation files. Application source, `.env`, local ledgers and ignored artifacts are excluded. Existing report paths are preserved. The published bundle is currently approximately 750 MiB, largely retained audio; monitor GitHub Pages' 1 GB site limit as recordings grow.

## Updating

Add and commit new evidence under `reports/` or `artifacts/`. Every deployment discovers it automatically. To refresh the checked-in catalogue for local preview or branch-based Pages hosting:

```sh
python3 scripts/build_report_catalogue.py
python3 -m http.server 8765
```

Open `http://localhost:8765/`. Edit `FEATURES` in the generator to change the featured assessment and recent work; list the preferred assessment first. A newer file does not automatically become the recommended assessment: the archive dates and featured recommendation are intentionally separate.

## Organization

- **Current**: preferred assessment of the most recent featured experiment.
- **Original**: preserved initial scoring, retained for comparison and provenance.
- **Re-scored**: revised assessment of existing calls, not a new experimental run.
- **Evaluation / Listening**: complementary analysis and human-audition pages.

Do not move or delete earlier artifacts merely to tidy the hub: reports may link to nearby audio, JSON and notes. Grouping and version labels provide navigation while preserving those links. The full archive includes historical diagnostics and incomplete studies; inclusion is not endorsement of their conclusions. Read each report's limitations.

## Voice library

`voice-library.html` compares the 25 October matched-script recordings: six prompts × six voice variants in clean audio, plus telephone versions for the first two prompts (48 clips). Its bottom player provides native play/pause and seeking, speed selection, previous/next provider and optional continuous comparison across a prompt. Switching audio conditions stops and clears playback. Missing recordings are explicit.

The catalogue builder derives `site/voice-library.json` from the original `key.json`, verifies each WAV is tracked, and records its SHA-256. Audio is reused without copies or regeneration. Exported model and unnamed voice identities are unavailable and are labelled accordingly; do not substitute current adapter defaults as historical recording metadata. The library is representative scripted speech, not per-run bake-off evidence. Add separately labelled datasets for future model/voice versions rather than silently replacing these clips.

### GPT-Live extension (28 October)

The library also merges `artifacts/gpt-live-voice-library-2026-10-28/key.json`: six GPT-Live 1 / Marin scripts plus two telephone derivatives, bringing the total to 56 clips across seven variants. This export records model, voice, backend, date, delivered prompt and provider transcripts; its independent ASR results are retained alongside it. The builder checks exact script equality before merging sources. Older samples retain unknown model/voice labels. Per-column recording dates disclose the different batches.

## Test setup transparency

`test-setup.html` is generated from the dated `prompts.json`, `verified-prompt-bindings.json` and re-scored `report.json` files listed in `scripts/build_test_setup.py`. Run `python3 scripts/build_test_setup.py` after changing the template (`site/test-setup.template.html`) or adding an experiment to that explicit list. The Pages workflow regenerates it before staging. `site/test-setup.js` renders the selectors, configurations, exact texts and evidence links using text content.

Never substitute current model defaults for historical gaps. Shared and differing prompt lines are computed from the selected experiment's saved texts, not current prompt constants. Configuration variants retain observation counts; assessor records come from the selected re-scored revision. Hash checks compare files and manifests only; this page does not reverify ledger signatures or vendor execution. Report model labels remain report labels, not independently resolved versions. Keep raw evidence artifacts unchanged.
