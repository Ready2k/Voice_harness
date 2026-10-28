# Research archive

The repository-root `index.html` is the GitHub Pages landing page. It highlights curated experiments and searches all tracked files in `reports/`, `artifacts/` and `docs/`. Notes open in GitHub's Markdown viewer; web reports, audio and data use relative Pages URLs.

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
