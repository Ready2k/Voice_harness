# Voice Harness

Container-first deterministic core for the Voice AI Evaluation & Assurance Platform v0.3: synthetic banking fixtures, a mock provider, PostgreSQL, authenticated FastAPI, stdio MCP and a PCM playback gate.

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

Evidence persists in the voice-harness_evidence volume and PostgreSQL in voice-harness_postgres-data. The generated operator token is /data/operator-token (0600). The database secret is .local/postgres-password, excluded from source and container builds. No provider credentials are needed.

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

All four live provider families remain not_implemented. See [Phase 1 status](docs/PHASE1_STATUS.md) for acceptance coverage and remaining conformance work.
