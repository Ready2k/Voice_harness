.PHONY: build test golden recovery infra stop up init benchmark demo

init:
	@mkdir -p .local
	@test -f .local/postgres-password || openssl rand -hex -out .local/postgres-password 32
	@chmod 600 .local/postgres-password

up: init
	docker compose --profile infra up --build -d api otel-collector

demo:
	docker compose exec -T api python -m voice_harness demo

build:
	docker compose build harness tests

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
