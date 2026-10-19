# Engineering guide

How the harness is built, tested and operated. For design see [ARCHITECTURE.md](ARCHITECTURE.md); for intent see
[VISION.md](VISION.md); for the normative requirements see `voice_evals_spec_v0.3.md`.

## Repository layout

```text
src/voice_harness/   Python package (service, engine, adapters, judges, evidence)
apps/web/            operator UI: coverage, runs, review; PCM scheduler and AudioWorklet
tests/               unittest suites; tests/browser/*.test.mjs (Node)
contracts/           generated JSON Schemas and OpenAPI (regenerate; do not hand-edit)
artifacts/           committed evidence: phase0 capability reports and probe logs, phase2 AC-09, phase4 runs
docs/                spec, per-phase status, per-topic notes, reviews
scripts/             probe CLI, Bedrock calibration, AWS session helper, comparison renderer
infra/otel/          OTLP collector config
compose.yaml         api, postgres, otel-collector; profiles for tests, browser-tests, benchmark, live
```

## Day-to-day commands

```bash
make up            # api + collector on 127.0.0.1:8765
make test          # container suite (PostgreSQL) + browser tests: this is what CI runs
make lint          # ruff + bandit
make audit         # pip-audit on requirements.lock
make demo          # three mock scenarios through the API, verified bundles under /data/demo
make benchmark     # component benchmark -> /data/phase1-baseline.json
make stop
```

Local development uses SQLite; Compose uses PostgreSQL. Run both before pushing: some behaviour (migrations,
JSONB writes, pool) exists only on PostgreSQL.

```bash
uv venv .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m unittest tests.test_core.SomeClass.test_name -v
PYTHONPATH=src .venv/bin/python -m voice_harness.generate_contracts   # after model or route changes
```

Tests use `unittest`, not pytest. After regenerating contracts, check `git diff contracts/` is intended.

## Live provider benchmark

New Gemini sessions use the pinned model `gemini-3.8-live` on Vertex AI in `us-central1`,
with Application Default Credentials. The adapter resamples harness audio to 16 kHz mono PCM;
output remains 24 kHz. `DEFAULT_MODEL` in `adapters/google_gemini_live.py` and the
`gemini-live` entry in `live_providers.TESTED_MODELS` must agree. Earlier evidence naming
`gemini-live-2.5-flash-native-audio` describes historical 2.5 runs.

Run a fresh API process after a model change (`make serve-live`). Use a new matrix filename:
the batch runner resumes completed cells when its output already exists.

```bash
.venv/bin/python scripts/provider_matrix.py --base http://127.0.0.1:8766 \
  --token-file .local/host-data/operator-token \
  --out artifacts/provider-matrix/matrix-gemini38-2026-10-19.json
PYTHONPATH=src .venv/bin/python scripts/benchmark_report.py \
  artifacts/provider-matrix/matrix-gemini38-2026-10-19.json \
  reports/voice-provider-benchmark-gemini38-2026-10-19.html \
  --database .local/host-data/development.sqlite
```

The database must be the one used by that API process. The 2026-10-19 Gemini 3.8 rerun uses
an isolated server on port 8767 and `.local/gemini38-data/development.sqlite`; substitute
its token path and database when reproducing that report. All 33 live scenarios run against
all four providers, with one sample per cell and at most two simultaneous runs per provider.
Keep failed attempts visible; any retry must preserve its first attempt. Component benchmarks
(`make benchmark`) measure harness stages separately from conversational provider latency.

The latest rerun on revision `5350e4a` uses port 8768 and
`.local/benchmark-5350e4a/development.sqlite`. See
[BENCHMARK_5350E4A_2026-10-19.md](BENCHMARK_5350E4A_2026-10-19.md) for results, fault-isolation
verification and the ASR credit limitation.

## Test strategy

| Layer | What it establishes | Where |
|---|---|---|
| Deterministic core | Enforcement mechanics: approvals, replay, races, tampering, retention, isolation, abrupt termination | `test_core`, `test_phase1`, `test_gaps*`, `test_release` (PostgreSQL) |
| Adapter conformance | Event mapping per provider against recorded provider shapes; AC-09 harness | `test_openai_adapter`, `test_nova_*`, `test_xai_adapter`, `test_google_adapter`, `adapter_conformance.py` |
| Live pipeline, scripted | Real API, runner, gateway and assessment with scripted adapter events and ASR | `test_live_api`, `test_phase4_*`, `test_live_review_fixes` |
| Judges and evidence | Rubric, FACT/CONFIRM/fault evaluation, calibration, signing, cost, export | `test_judges`, `test_fact_faults_confirm`, `test_signing`, `test_cost*`, `test_signed_live_export` |
| Browser | View-models and playback scheduling | `tests/browser/*.test.mjs` |
| Live | Opt-in provider calls | `VOICE_HARNESS_LIVE_ADAPTERS=...`; skipped by default |

PostgreSQL schema initialisation takes a transaction-scoped advisory lock so API and MCP
workers starting together cannot race while replacing the append-only evidence trigger function.

Scripted tests prove behaviour, not live recognition accuracy. A UI change is not done until it has been checked in
a running app, because unit tests have missed layout bugs before.

## CI

`.github/workflows/phase1.yml`: `make lint`, `make audit`, image load with retry, then `make test` (Compose with
PostgreSQL). Lessons recorded in the status docs: validate on PostgreSQL, not only SQLite; CI has no macOS
speech tools, so live-run tests must not depend on them.

## Adding a provider adapter

1. **Probe first.** Run the probe to record only observed behaviour (auth, codecs, tool plumbing, transcripts,
   turn signalling, cancel and truncate, usage). Anything not exercised stays `Unknown`. Write the capability
   report; it expires after 7 days.
2. **Adapter.** Implement `LiveAdapter` over the closed canonical events, preserving the original provider
   payload. Declare unsupported features rather than emulating them. Pin the versioned model name; never run
   against an alias.
3. **AC-09.** Run live conformance (audio in, audio out, tool-result correlation, interruption, error) and store
   `artifacts/phase2/<family>-ac09.json` bound to the capability report id. Excluding a mandatory dimension
   prevents `passed`.
4. **Register.** Add to `LIVE_PROVIDERS` and `TESTED_MODELS` in `live_providers.py`, the contract enums and
   `coverage.py`; update `docs/phase0/PROVIDER_MATRIX.md` with observed values only.
5. **Tests and notes.** Offline adapter tests against recorded shapes; one live smoke scenario; a
   `PHASE4_NOTES_<provider>.md` listing quirks and Unknowns.

Do not record provider prices unless verified, and do not attest provider terms on anyone's behalf.

## Adding a scenario or criterion

- Scenarios are closed `ScenarioDefinition` records with a content hash. Faults are a separate closed
  `FaultInjection` list so existing hashes stay stable. Changing a definition changes its hash.
- Declare applicability explicitly. A criterion that cannot be assessed is `untestable` or `not_applicable` with
  a reason, never an implicit pass.
- Add an API-level regression for any assurance bug, asserting the overall verdict as well as the criterion.

## Security and secrets

- Provider and judge keys come from the environment (`OPENAI_API_KEY`, `xAI_API_KEY`) or cloud credentials (AWS
  session, Google ADC). They must never be logged, echoed, committed or written into artifacts. Exports record
  endpoint host, region and SDK versions, not credentials.
- Operator and reviewer tokens are separate roles, compared in constant time; the operator token is generated at
  `/data/operator-token` (mode 0600). The database secret is `.local/postgres-password`, git-ignored and excluded
  from builds.
- The API binds to loopback; non-loopback binding has an explicit policy. The web UI uses a strict CSP and renders
  with `textContent`.
- Dependencies are hash-locked (`requirements.lock`) and images pinned by digest. Update the lock, not just
  `pyproject.toml`. `make lint` (ruff, bandit) and `make audit` run in CI.
- Synthetic data only. Do not point the harness at real customer data or production systems.

## Evidence handling

- Committed artifacts under `artifacts/` are labelled with the commit and date they describe and are not evidence
  for later revisions.
- Verify any bundle offline: `python -m voice_harness verify-bundle BUNDLE --public-key KEYRING`. Without a key
  ring it exits 2 unless `--allow-unsigned` is given for a bundle that declares itself unsigned.
- Retention purges leave tombstones; re-exporting after a purge produces a newly signed bundle.

## Working agreements

- Lint and security-check as you go; run `make test` (Compose and PostgreSQL) before pushing.
- Keep claims at the strength of the evidence: single samples are called single samples.
- Reviews and judge re-runs by the reviewers are theirs to do; respond with fixes and regressions, and record the
  revision, CI run and test counts as the next baseline.

## Where to read next

| Topic | Document |
|---|---|
| Spec and acceptance criteria | `voice_evals_spec_v0.3.md` |
| Phase state | `PHASE1_STATUS.md`, `PHASE2_STATUS.md`, `PHASE3_STATUS.md`, `PHASE4_STATUS.md` |
| Provider facts and Unknowns | `phase0/PROVIDER_MATRIX.md`, `phase0/DECISIONS.md`, `phase0/DOC_EVIDENCE.md` |
| Provider quirks | `PHASE4_NOTES_*.md` |
| Reviews and rechecks | `CODE_SPEC_*.md`, `PHASE_REVIEW_2026-10-18.md` |
| Reuse from the sibling project | `VOICE_S2S_REUSE_AUDIT.md` |
