# Voice Harness

A local-first, vendor-neutral **evaluation and assurance harness for realtime voice agents in regulated
conversations** (spec: [`docs/voice_evals_spec_v0.3.md`](docs/voice_evals_spec_v0.3.md)). It tests whether
speech-to-speech models behave safely in synthetic banking calls, and produces signed evidence of what happened.
It is not a production banking service, and passing it is not a certification.

## What it does

- **Contains** the model under test: every tool call passes an independent guardrail, and every spoken reply is
  held as pending audio until policy releases it.
- **Exercises** six live provider configurations (OpenAI GPT-Live 1 `openai-live`, OpenAI Realtime `openai-realtime-live`,
  Amazon Nova Sonic `nova-sonic-live`, xAI Grok Voice `xai-voice-live`, Google Gemini Live 3.8 `gemini-live` /
  `gemini-3.8-live`, ElevenLabs Agents `elevenlabs-live`) with a catalogue of 40 live scenarios in five groups
  (happy path, identity protection, vulnerable customers, fault tolerance, payment actions) plus 3 built-in mock fixtures: personas, locales, noise and narrowband audio, injected faults, interruptions.
- **Judges** with deterministic checks first (identity, factual accuracy against independent ASR, consent,
  audio), then advisory AI judges that are calibrated before they are trusted. Missing evidence is `untestable`,
  never a pass.
- **Proves** with an append-only, hash-chained ledger exported as an Ed25519-signed bundle that verifies offline.
- **Reports** verified support per provider family. It never ranks providers.

Read next: [Vision](docs/VISION.md) · [Architecture](docs/ARCHITECTURE.md) · [Engineering guide](docs/ENGINEERING.md)

The **Runs** page supports **Fixed caller** scripts and a **Live AI caller** pilot for the clean balance enquiry. The live caller listens and replies with a selected persona and natural voice; its OpenAI session is independent of the agent under test. See [setup, limits and API example](docs/INTERACTIVE_CALLER_SCOPE.md#using-the-pilot). Enable audio retention to replay both speakers from run details.

Delegate agent extension: the [specification](docs/DELEGATE_AGENT_SPEC.md) is still a proposal; only a bounded CLI telephone pilot (and the **Telephone** page that wraps it) is implemented. See the [runbook](docs/DELEGATE_PHONE_RUNBOOK.md) and [adaptive scope](docs/DELEGATE_ADAPTIVE_SCOPE.md). It is diagnostic and not independent platform verification.

## Status

Phases 0-3 are complete. Phase 4 (provider breadth, scenarios, live runs) is in progress: provider
families are `implemented_and_verified` only while their capability reports are current (7-day expiry); check the Coverage page for the live state. The 28 October fair bake-off (6 providers × 40 scenarios × 3 repetitions) is the first repeated-sample set. No AI judge policy is approved
(blocking is off), and no real prices are established. See [Phase 4 status](docs/PHASE4_STATUS.md).

## Start and verify

```bash
make up
make test
make demo
```

The API is available at [127.0.0.1:8765](http://127.0.0.1:8765/docs). PostgreSQL and OTLP ports remain internal to Compose. Port 8000 was already occupied on the development host. Web pages are served under `/client/` (for example `runs.html`, `scenarios.html`, `coverage.html`, `manual.html`, `bakeoff.html`, `balances.html`, `telephone.html`) and need the operator token.

Tests run in a non-root container against PostgreSQL, including stdio MCP, authenticated HTTP/WebSocket controls, twenty concurrent fixture namespaces, approval attacks, confirmation replay, retention races, evidence tampering and abrupt termination after commit.

The demo launches identity-bypass, verified-balance and unsafe-disclosure scenarios through the API. Expected model/platform outcomes are fail/pass, pass/pass and fail/pass. It reads the operator token inside the container, verifies exports and saves bundles under /data/demo.

Fixture audio is PCM silence with scripted transcript/assessment metadata. These tests establish enforcement mechanics; they do not measure live ASR, spoken factual correctness or physical audibility.

The **Balances** page shows provider credit readings, setup gaps and dated manual balances. It adds credit warnings to connected pages. See [supported billing APIs and setup](docs/PROVIDER_BALANCES.md).

## Evidence and measurements

Evidence persists in the voice-harness_evidence volume and PostgreSQL in voice-harness_postgres-data. The generated operator token is /data/operator-token (0600). `make token` copies it to the clipboard without printing it; the web pages explain this on first load.

Live provider runs need a macOS host, so run `make serve-live` (loads `.env`, own data dir under `.local/host-data`, port 8766, separate from Compose) and fetch its token with `make token-host`. The Runs page shows what each provider still needs. The database secret is .local/postgres-password, excluded from source and container builds. The deterministic core needs no provider credentials; live runs need provider keys and a macOS host (see the engineering guide). `.env` supplies `OPENAI_API_KEY`, `xAI_API_KEY` and `ELEVENLABS_API_KEY`; Nova uses AWS credentials (`make nova-live-container`, `scripts/aws-session.sh`).

```bash
make benchmark
docker compose cp api:/data/phase1-baseline.json artifacts/phase1-baseline.json
docker compose cp api:/data/demo artifacts/demo
docker compose exec -T api python -m voice_harness verify-bundle /data/demo/RUN_ID.json
```

The benchmark covers 1/5/10 sessions, normal/4× payloads, enabled/disabled instrumentation and 1,000 observations per profile after warmup. It records stage distributions, base64/JSON versus binary framing, CPU/memory and slow-storage/stalled-consumer faults. These are component measurements; whole conversational latency is separate.

Manifests bind event count, final ledger hash and artifact hashes; verification detects changed events/artifacts and suffix deletion relative to the manifest. Ed25519 signing (Phase 3, see [signed export notes](docs/PHASE4_NOTES_signed_export.md)) authenticates the signer; an unsigned manifest does not authenticate its author. Missing or purged audio is explicit.

## Blind voice comparisons

Open the [two-comparison menu](artifacts/voice-comparisons/index.html) locally to choose the original fixed-line listening test or the advanced conversation comparison. The advanced live set contains 115 complete conversations from a 120-cell plan, comparing common and provider-specific prompts across six multi-turn situations. Five attempts exceeded the response-length limit. Response timings are recorded; blind human ratings are pending. See the [recording and review runbook](docs/VOICE_CONVERSATION_COMPARISON.md).

For the complete assessed banking catalogue, the [advanced full bake-off](docs/ADVANCED_FULL_BAKEOFF.md) runs provider-specific conversation styles with all verification rules, tools and speech assessments retained. Select **Advanced provider-specific prompts** in the bake-off page or use `prompt_profile: "advanced-v1"` through the API.

The [completed 600-cell advanced report](artifacts/full-advanced-bakeoff-2026-10-26/index.html) includes results, exact prompt bindings and verified reuse of completed runs after pauses. Human likeness still requires the separate blind listening review.

## Fair bake-off (28 October 2026)

The latest comparison is the [fair-harness bake-off](artifacts/fair-bakeoff-2026-10-28/index.html): 720 scored cells, with the bias remediation from the [provider bias review](docs/PROVIDER_BIAS_REVIEW_2026-10-27.md) applied. Read the [run notes](artifacts/fair-bakeoff-2026-10-28/run-notes.md) and the [re-scored report](artifacts/fair-bakeoff-2026-10-28/rescored-2026-10-28/README.md) for conclusions and limits. Reports describe verified support, interval scorecards and paired comparisons; they are not certification.

## Development

```bash
make lint    # ruff and bandit
make audit   # pip-audit on requirements.lock
uv venv .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m voice_harness.generate_contracts
```

Local tests default to SQLite; Compose tests use PostgreSQL. Schemas and OpenAPI are generated in contracts/. Argument and result schemas reject unexpected properties. Python dependencies are pinned with hashes; Python and collector images are pinned by digest.

make golden and make recovery retain the earlier standalone development fixtures. The API/demo path is the integrated PostgreSQL/MCP implementation. make stop preserves volumes.

Acceptance coverage is mapped in [Phase 1 status](docs/PHASE1_STATUS.md) and the later phase status documents.

GPT-Live 1 is available as **OpenAI GPT-Live 1** (`openai-live`), separately from Realtime.
See [configuration, diagnostic limitations and the live smoke test](docs/OPENAI_LIVE.md).

## Published research archive

The [GitHub Pages hub](https://ready2k.github.io/Voice_harness/) brings together the latest assessments, earlier experiments, listening pages and supporting artifacts. See [site/README.md](site/README.md) for publishing and catalogue maintenance.
