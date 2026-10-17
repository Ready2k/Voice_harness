FROM public.ecr.aws/docker/library/node:24.9.0-bookworm-slim@sha256:3e69116c924bfcba6c6979aff60d966c37aef56d488ce091c69d442ebec9f103 AS nova-deps
WORKDIR /probe
COPY scripts/probe/package.json scripts/probe/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM public.ecr.aws/docker/library/python:3.13.9-slim-bookworm@sha256:b685a4fa58bb19d1814d78a1ec0f0208f351452724f78b20212c984d6e124a34 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
RUN groupadd --gid 10001 harness && useradd --uid 10001 --gid harness --create-home harness \
    && mkdir -p /data && chown harness:harness /data
COPY --chown=harness:harness src/ ./src/
COPY --chown=harness:harness contracts/ ./contracts/
COPY --chown=harness:harness apps/web/ ./apps/web/
COPY --chown=harness:harness artifacts/phase2/ ./artifacts/phase2/
COPY --chown=harness:harness artifacts/phase0/*-capability-report.json ./artifacts/phase0/
# Node runtime for the Nova bridge (same pinned image as nova-deps) + lockfile-installed deps. No credentials.
COPY --from=nova-deps /usr/local/bin/node /usr/local/bin/node
COPY --chown=harness:harness scripts/probe/nova_bridge.mjs scripts/probe/package.json scripts/probe/package-lock.json ./scripts/probe/
COPY --from=nova-deps --chown=harness:harness /probe/node_modules ./scripts/probe/node_modules
USER harness

FROM base AS test
COPY --chown=harness:harness tests/ ./tests/
ENV HARNESS_REQUIRE_NOVA_RUNTIME=1
COPY --chown=harness:harness artifacts/phase0/ ./artifacts/phase0/
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]

FROM public.ecr.aws/docker/library/node:24.9.0-bookworm-slim@sha256:3e69116c924bfcba6c6979aff60d966c37aef56d488ce091c69d442ebec9f103 AS browser-test
WORKDIR /app
COPY apps/web/ ./apps/web/
COPY tests/browser/ ./tests/browser/
COPY contracts/approval-fixture.json ./contracts/approval-fixture.json
USER node
CMD ["node", "--test", "tests/browser/scheduler.test.mjs", "tests/browser/coverage.test.mjs", "tests/browser/clock-sync.test.mjs"]

FROM base AS runtime
ENTRYPOINT ["python", "-m", "voice_harness"]
CMD ["golden-run", "--output", "/data/golden-run.json"]
