# Secret file mode: 600 locally; CI runners need 644 so the non-root test container (different uid) can read it.
SECRET_MODE ?= 600

.PHONY: token nova-live-container lint audit build test golden recovery infra stop up init benchmark demo

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

demo:
	docker compose exec -T api python -m voice_harness demo

build:
	docker compose build harness tests

lint:
	uvx ruff@0.17.1 check src tests scripts
	uvx bandit@1.9.4 -q -r src -c pyproject.toml

audit:
	uvx pip-audit -r requirements.lock --no-deps --disable-pip

test: init
	docker compose run --build --rm tests
	docker compose run --build --rm browser-tests

golden:
	docker compose run --build --rm harness

recovery:
	docker compose run --build --rm harness recovery-run --database /data/execution.sqlite

benchmark: init
	docker compose run --build --rm benchmark

infra:
	docker compose --profile infra up -d postgres otel-collector

stop:
	docker compose --profile infra down

# Opt-in: needs unexpired .local/aws-session.env (./scripts/aws-session.sh <MFA>); runs Nova AC-09 in the image.
nova-live-container:
	@test -f .local/aws-session.env || { echo "missing .local/aws-session.env: run ./scripts/aws-session.sh <MFA_CODE>" >&2; exit 2; }
	docker compose run --build --rm nova-live
