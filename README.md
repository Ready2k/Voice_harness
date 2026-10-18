# Voice Harness

A local-first, vendor-neutral **evaluation and assurance harness for realtime voice agents in regulated
conversations** (spec: [`docs/voice_evals_spec_v0.3.md`](docs/voice_evals_spec_v0.3.md)). It tests whether
speech-to-speech models behave safely in synthetic banking calls, and produces signed evidence of what happened.
It is not a production banking service, and passing it is not a certification.

## What it does

- **Contains** the model under test: every tool call passes an independent guardrail, and every spoken reply is
  held as pending audio until policy releases it.
- **Exercises** live providers (OpenAI Realtime, Amazon Nova Sonic, xAI Grok Voice; Gemini Live in progress)
  with a catalogue of 24 scenarios: personas, locales, noise and narrowband audio, injected faults, interruptions.
- **Judges** with deterministic checks first (identity, factual accuracy against independent ASR, consent,
  audio), then advisory AI judges that are calibrated before they are trusted. Missing evidence is `untestable`,
  never a pass.
- **Proves** with an append-only, hash-chained ledger exported as an Ed25519-signed bundle that verifies offline.
- **Reports** verified support per provider family. It never ranks providers.

Read next: [Vision](docs/VISION.md) · [Architecture](docs/ARCHITECTURE.md) · [Engineering guide](docs/ENGINEERING.md)

## Status

Phases 0-3 are complete. Phase 4 (provider breadth, scenarios, live runs) is in progress: three of four provider
families are `implemented_and_verified`. Live results are mostly single samples, no AI judge policy is approved
(blocking is off), and no real prices are established. See [Phase 4 status](docs/PHASE4_STATUS.md).

## Start and verify

```bash
make up
make test
make demo
```

The API is available at [127.0.0.1:8765](http://127.0.0.1:8765/docs). PostgreSQL and OTLP ports remain internal to Compose. Port 8000 was already occupied on the development host.

Tests run in a non-root container against PostgreSQL, including stdio MCP, authenticated HTTP/WebSocket controls, twenty concurrent fixture namespaces, approval attacks, confirmation replay, retention races, evidence tampering and abrupt termination after commit.

The demo launches identity-bypass, verified-balance and unsafe-disclosure scenarios through the API. Expected model/platform outcomes are fail/pass, pass/pass and fail/pass. It reads the operator token inside the container, verifies exports and saves bundles under /data/demo.

Fixture audio is PCM silence with scripted transcript/assessment metadata. These tests establish enforcement mechanics; they do not measure live ASR, spoken factual correctness or physical audibility.

## Evidence and measurements

Evidence persists in the voice-harness_evidence volume and PostgreSQL in voice-harness_postgres-data. The generated operator token is /data/operator-token (0600). The database secret is .local/postgres-password, excluded from source and container builds. The deterministic core needs no provider credentials; live runs need provider keys and a macOS host (see the engineering guide).

```bash
make benchmark
docker compose cp api:/data/phase1-baseline.json artifacts/phase1-baseline.json
docker compose cp api:/data/demo artifacts/demo
docker compose exec -T api python -m voice_harness verify-bundle /data/demo/RUN_ID.json
```

The benchmark covers 1/5/10 sessions, normal/4× payloads, enabled/disabled instrumentation and 1,000 observations per profile after warmup. It records stage distributions, base64/JSON versus binary framing, CPU/memory and slow-storage/stalled-consumer faults. These are component measurements; whole conversational latency is separate.

Unsigned manifests bind event count, final ledger hash and artifact hashes. Verification detects changed events/artifacts and suffix deletion relative to the manifest. A checksum does not authenticate its author; signing is a Phase 3 gate. Missing or purged audio is explicit.

## Development

```bash
uv venv .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m voice_harness.generate_contracts
```

Local tests default to SQLite; Compose tests use PostgreSQL. Schemas and OpenAPI are generated in contracts/. Argument and result schemas reject unexpected properties. Python dependencies are pinned with hashes; Python and collector images are pinned by digest.

make golden and make recovery retain the earlier standalone development fixtures. The API/demo path is the integrated PostgreSQL/MCP implementation. make stop preserves volumes.

Acceptance coverage is mapped in [Phase 1 status](docs/PHASE1_STATUS.md) and the later phase status documents.
