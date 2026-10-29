# Secret file mode: 600 locally; CI runners need 644 so the non-root test container (different uid) can read it.
SECRET_MODE ?= 600

.PHONY: serve-live token-host token nova-live-container lint audit image-scan build test test-db golden recovery infra stop up init benchmark chaos-postgres load-test perf-gate demo

init:
	@mkdir -p .local
	@test -f .local/postgres-password || openssl rand -hex -out .local/postgres-password 32
	@chmod $(SECRET_MODE) .local/postgres-password

up: init
	docker compose --profile infra up --build -d api otel-collector

# Copies the operator token to the clipboard without printing it (the API must be up: make up).
token:
	@command -v pbcopy >/dev/null 2>&1 || { echo "no pbcopy: run 'docker compose exec -T api cat /data/operator-token' and copy the output yourself" >&2; exit 2; }
	@t=$$(docker compose exec -T api cat /data/operator-token 2>/dev/null) && test -n "$$t" || { echo "could not read the token: is the API running? try 'make up'" >&2; exit 1; }; \
	printf %s "$$t" | pbcopy && echo "Operator token copied to the clipboard. Paste it into the web page."

# Live runs need a macOS host (say/afconvert caller voice), so run the API on the host, not in Compose.
# Loads .env (KEY=value lines; values are never printed), uses its own data dir and port 8766, and does not touch Compose.
serve-live:
	@test -f .env || { echo "missing .env (OPENAI_API_KEY, xAI_API_KEY, ELEVENLABS_API_KEY)" >&2; exit 2; }
	@test -x .venv/bin/python || { echo "missing .venv: see CLAUDE.md local development" >&2; exit 2; }
	@mkdir -p .local/host-data
	@echo "Live API on http://127.0.0.1:8766/client/runs.html (get the token with: make token-host)"
	@set -a; . ./.env; set +a; \
	HARNESS_DATA="$(CURDIR)/.local/host-data" HARNESS_PORT=8766 VOICE_HARNESS_LIVE_ASR=openai PYTHONPATH=src \
	HARNESS_PRICING_TABLE="$${HARNESS_PRICING_TABLE:-$(CURDIR)/artifacts/pricing/vendor-pricing-2026-10-24.json}" \
	.venv/bin/python -m voice_harness serve

# Same as make token, for the host-run server (make serve-live); start it once so the token file exists.
token-host:
	@test -s .local/host-data/operator-token || { echo "no host token yet: start the server with 'make serve-live' first" >&2; exit 1; }
	@command -v pbcopy >/dev/null 2>&1 || { echo "no pbcopy: copy .local/host-data/operator-token yourself" >&2; exit 2; }
	@tr -d '\n' < .local/host-data/operator-token | pbcopy && echo "Host operator token copied to the clipboard. Paste it into the web page."

demo:
	docker compose exec -T api python -m voice_harness demo

build:
	docker compose build harness tests

lint:
	uvx ruff@0.17.1 check src tests scripts
	uvx bandit@1.9.4 -q -r src -c pyproject.toml

audit:
	uvx pip-audit -r requirements.lock --no-deps --disable-pip
	cd scripts/probe && npm audit --omit=dev

# Opt-in (not in CI): needs trivy. Scans the built api image, which covers the pinned Python base and the Node runtime.
image-scan: build
	trivy image --severity HIGH,CRITICAL --ignore-unfixed --scanners vuln voice-harness-api:latest

# Tests get their own throwaway database so fixture runs and fake-clock holds never reach the live one.
test-db: init
	docker compose up -d --wait postgres
	docker compose exec -T postgres psql -U harness -d postgres -v ON_ERROR_STOP=1 \
		-c 'DROP DATABASE IF EXISTS voice_harness_test WITH (FORCE)' -c 'CREATE DATABASE voice_harness_test'

test: init test-db
	docker compose run --build --rm tests
	docker compose run --build --rm browser-tests

golden:
	docker compose run --build --rm harness

recovery:
	docker compose run --build --rm harness recovery-run --database /data/execution.sqlite

benchmark: init
	docker compose run --build --rm benchmark

# Real PostgreSQL outage: stop the database for 6s while a driver keeps issuing tool calls, then verify the ledger (no spend).
chaos-postgres: test-db
	docker compose build tests
	@docker compose run --rm tests python -m voice_harness.chaos_postgres 25 & driver=$$!; \
		sleep 8; docker compose stop postgres; sleep 6; docker compose up -d --wait postgres; wait $$driver

# Stepped-concurrency load test of the harness (mock provider, no spend); writes /data/phase5-load.json and .local/load-run.json.
load-test: init
	docker compose run --build --rm load-test
	docker compose run --rm --no-deps --entrypoint cat load-test /data/phase5-load.json > .local/load-run.json

# Regression gate: rerun the reference benchmark and compare with artifacts/perf-baseline.json (see docs/PHASE5_STATUS.md).
perf-gate: benchmark
	docker compose run --rm --no-deps --entrypoint cat benchmark /data/phase1-baseline.json > .local/perf-run.json
	PYTHONPATH=src python3 -m voice_harness.perf_gate check .local/perf-run.json $(if $(PERF_BASELINE),--baseline $(PERF_BASELINE))

infra:
	docker compose --profile infra up -d postgres otel-collector

stop:
	docker compose --profile infra down

# Opt-in: needs unexpired .local/aws-session.env (./scripts/aws-session.sh <MFA>); runs Nova AC-09 in the image.
nova-live-container:
	@test -f .local/aws-session.env || { echo "missing .local/aws-session.env: run ./scripts/aws-session.sh <MFA_CODE>" >&2; exit 2; }
	docker compose run --build --rm nova-live
