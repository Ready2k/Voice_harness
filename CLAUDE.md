# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Voice Harness is a container-first, deterministic core for a Voice AI Evaluation & Assurance Platform (spec: `docs/voice_evals_spec_v0.3.md`). It provides synthetic banking fixtures, a mock provider, PostgreSQL-backed evidence, an authenticated FastAPI service, a stdio MCP server and a PCM playback gate. The deterministic core and CI need no provider credentials; live adapters (OpenAI, Nova, xAI verified; Gemini in progress) are opt-in and need keys. See `docs/ARCHITECTURE.md`, `docs/ENGINEERING.md`, `docs/VISION.md`. Acceptance-gate coverage (AC-01…AC-21) is mapped in `docs/PHASE1_STATUS.md`. Fixture audio is PCM silence with scripted metadata, so tests prove enforcement mechanics, not ASR/audibility.

## Commands

Primary workflow is Docker Compose via `make` (tests run in a non-root container against PostgreSQL):

```bash
make up          # init secrets, build and start api + otel-collector (API on 127.0.0.1:8765, not 8000)
make test        # container suite (tests + browser-tests); this is what CI runs
make demo        # run 3 scenarios through the API, save verified bundles under /data/demo
make benchmark   # component benchmark -> /data/phase1-baseline.json
make golden      # standalone identity-bypass fixture
make recovery    # standalone SQLite recovery fixture
make stop        # compose down (keeps volumes)
```

Local development (SQLite by default; Compose uses PostgreSQL):

```bash
uv venv .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m unittest tests.test_core.SomeClass.test_name -v   # single test
PYTHONPATH=src .venv/bin/python -m voice_harness.generate_contracts                   # regenerate contracts/
docker compose exec -T api python -m voice_harness verify-bundle /data/demo/RUN_ID.json
```

Tests use `unittest` (not pytest). `tests/test_release.py` requires PostgreSQL, so it only fully runs under Compose. Browser tests are `tests/browser/*.test.mjs` (Node). The provider coverage panel (AC-20) is served at `/client/coverage.html` (needs the operator token).

Dependencies are pinned in `pyproject.toml` and hash-locked in `requirements.lock`; Python and collector images are pinned by digest — update the lock, not just `pyproject.toml`.

## Architecture

Python package `src/voice_harness/` (CLI entry: `__main__.py` with `serve`, `golden-run`, `recovery-run`, `benchmark`, `verify-bundle`, `demo`):

- **`engine.py`** (largest module): trusted orchestration, synthetic banking tools, and immutable PCM approval units. Uses `approvals.py` (token signing), `policy.py`, `tool_lifecycle.py`, `verdicts.py`.
- **`contracts.py`**: pydantic models for tools/results/approval tokens; schemas reject unexpected properties. `generate_contracts.py` emits JSON Schemas and OpenAPI into `contracts/` — rerun it after changing models or API routes.
- **`storage.py` / `relational.py`**: transactional per-run namespaces and append-only canonical evidence ledger; psycopg pool for PostgreSQL, SQLite fallback. Event models in `events.py`.
- **`evidence.py`**: hash-chained ledger, export manifests (event count, final ledger hash, artifact hashes) and `verify_bundle`. Manifests are unsigned; signing is a Phase 3 gate.
- **`api.py`**: authenticated FastAPI (HTTP + WebSocket); operator token generated at `/data/operator-token`.
- **`mcp_server.py` / `mcp_gateway.py`**: stdio MCP surface and correlated tool-call gateway.
- **`playback.py`**: PCM playback gate bound to session/hash/range/epoch. Browser side lives in `apps/web/` (`scheduler.mjs`, `playback-worklet.js`, `client.mjs`).
- **`mock_provider.py`, `simulator.py`, `scenarios.py`, `golden.py`, `recovery.py`**: deterministic fixtures and scenario replay.
- **`benchmark.py`, `websocket_benchmark.py`, `clock_sync.py`, `telemetry.py`**: component measurements (not end-to-end latency) and OTLP export (`infra/otel/collector.yaml`).

Key invariants: evidence-write failure must block tool calls/releases; trace-export failure must not lose ledger data; protected tool proposals are denied before execution; confirmations/approval tokens are single-use and consumed transactionally.

## Environment notes

- Database secret is `.local/postgres-password` (created by `make init`, git-ignored and excluded from builds). Evidence lives in the `voice-harness_evidence` volume, DB in `voice-harness_postgres-data`.
- Port 8765 is used because 8000 was occupied on the dev host; PostgreSQL and OTLP ports stay internal to Compose.
- `docs/VOICE_S2S_REUSE_AUDIT.md` records what was reused from the sibling `AntiGravity_projects/Voice_S2S` project.
