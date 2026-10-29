FROM mirror.gcr.io/library/node:24.22.0-bookworm-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5 AS nova-deps
WORKDIR /probe
COPY scripts/probe/package.json scripts/probe/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM mirror.gcr.io/library/python:3.13.16-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS base

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
COPY --chown=harness:harness artifacts/pricing/ ./artifacts/pricing/
COPY --chown=harness:harness artifacts/phase0/*-capability-report.json ./artifacts/phase0/
# Node runtime for the Nova bridge (same pinned image as nova-deps) + lockfile-installed deps. No credentials.
COPY --from=nova-deps /usr/local/bin/node /usr/local/bin/node
COPY --chown=harness:harness scripts/probe/nova_bridge.mjs scripts/probe/package.json scripts/probe/package-lock.json ./scripts/probe/
COPY --from=nova-deps --chown=harness:harness /probe/node_modules ./scripts/probe/node_modules
USER harness

FROM base AS test
COPY --chown=harness:harness tests/ ./tests/
COPY --chown=harness:harness scripts/voice_conversation.py ./scripts/voice_conversation.py
COPY --chown=harness:harness scripts/rescore_bakeoff_report.py ./scripts/rescore_bakeoff_report.py
COPY --chown=harness:harness scripts/voice_naturalness.py ./scripts/voice_naturalness.py
COPY --chown=harness:harness scenarios/ ./scenarios/
ENV HARNESS_REQUIRE_NOVA_RUNTIME=1
COPY --chown=harness:harness artifacts/phase0/ ./artifacts/phase0/
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]

FROM mirror.gcr.io/library/node:24.22.0-bookworm-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5 AS browser-test
WORKDIR /app
COPY apps/web/ ./apps/web/
COPY tests/browser/ ./tests/browser/
COPY contracts/approval-fixture.json ./contracts/approval-fixture.json
USER node
CMD ["node", "--test", "tests/browser/scheduler.test.mjs", "tests/browser/coverage.test.mjs", "tests/browser/clock-sync.test.mjs", "tests/browser/runs.test.mjs", "tests/browser/onboarding.test.mjs", "tests/browser/interactive-caller.test.mjs"]

FROM base AS runtime
ENTRYPOINT ["python", "-m", "voice_harness"]
CMD ["golden-run", "--output", "/data/golden-run.json"]
