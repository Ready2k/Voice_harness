"""Stepped-concurrency load test of the harness's own turn path; mocked speech, no provider or network spend.

It measures the instrument, not a model: how much latency and scheduling lag the harness adds as concurrent sessions
grow. Sessions share one process (like the API), so the GIL, SQLite/PostgreSQL write contention and CPU limits all show up.
"""

import base64
import hashlib
import json
import os
import platform
import resource
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .benchmark import distribution
from .contracts import RunConfig
from .engine import Engine, digest, source_hash
from .events import Event
from .playback import PlaybackGate
from .policy import authorize_tool
from .storage import Database

STEPS = (1, 6, 12, 24)
KNEE_FACTOR = 2.0
WARMUP = 10


def _session(database, run_id, turns, pace_ms):
    """Run one paced session; return (turn latencies, pacing lags) in ms."""
    pcm = bytes(960)
    gate = PlaybackGate(run_id)
    latencies, lags = [], []
    deadline = time.perf_counter()
    for iteration in range(turns + WARMUP):
        scheduled = deadline
        started = time.perf_counter()
        start_ns = time.monotonic_ns()
        wire = json.dumps({"bytes": base64.b64encode(pcm).decode(), "rate": 24000, "channels": 1}).encode()
        decoded = base64.b64decode(json.loads(wire)["bytes"], validate=True)
        payload = {"index": iteration, "sha256": hashlib.sha256(decoded).hexdigest(), "payload_bytes": len(wire)}
        digest(payload)
        json.dumps(Event(run_id, "orchestrator", "load.audio", payload).to_dict())
        decision = authorize_tool("get_account_summary", {"account_id": "acct_1001"},
                                  allowed_tools={"get_account_summary"}, verified=True, account_number="acct_1001")
        if not decision.allowed:
            raise RuntimeError("LOAD_POLICY_DENIED")
        with database.transaction(run_id) as tx:
            tx.event("orchestrator", "load.observation", payload)
        unit = gate.hold("response", decoded, sample_range=(0, len(decoded) // 2))
        if base64.b64decode(base64.b64encode(gate.release(gate.approve(unit)))) != pcm:
            raise RuntimeError("LOAD_OUTBOUND_MISMATCH")
        if iteration >= WARMUP:
            latencies.append((time.monotonic_ns() - start_ns) / 1e6)
            lags.append(max(0.0, (started - scheduled) * 1000))
        deadline += pace_ms / 1000
        time.sleep(max(0, deadline - time.perf_counter()))
    return latencies, lags


def _step(engine, database, sessions, observations, pace_ms):
    runs = [engine.create(RunConfig().model_dump()) for _ in range(sessions)]
    turns = max(50, -(-observations // sessions))
    wall, cpu = time.perf_counter(), time.process_time()
    with ThreadPoolExecutor(max_workers=sessions) as pool:
        results = list(pool.map(lambda run: _session(database, run, turns, pace_ms), runs))
    elapsed, cpu = time.perf_counter() - wall, time.process_time() - cpu
    for run_id in runs:
        with database.transaction(run_id) as tx:
            tx.state["state"] = "completed"
            tx.event("orchestrator", "load.completed", {"assessment": "performance_only", "assurance": "untestable"})
    latencies = [v for result in results for v in result[0]]
    lags = [v for result in results for v in result[1]]
    return {
        "sessions": sessions,
        "observations": len(latencies),
        "turn_latency": distribution(latencies),
        "pacing_lag": distribution(lags),
        "elapsed_seconds": elapsed,
        "cpu_seconds": cpu,
        "cpu_cores_used": cpu / elapsed,
        "throughput_turns_s": len(latencies) / elapsed,
        "offered_turns_s": sessions * 1000 / pace_ms,
        "max_rss_after_step": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def knee(steps):
    """First step whose turn p95 exceeds KNEE_FACTOR x the single-session p95 (descriptive, not a pass/fail)."""
    base = steps[0]["turn_latency"]["p95_ms"]
    for step in steps[1:]:
        if step["turn_latency"]["p95_ms"] > base * KNEE_FACTOR:
            return step["sessions"]
    return None


def load_test(output, steps=STEPS, observations=1000, database_url=None, pace_ms=20):
    with tempfile.TemporaryDirectory() as directory:
        database = Database(database_url or str(Path(directory) / "load.sqlite"))
        engine = Engine(database)
        records = [_step(engine, database, sessions, observations, pace_ms) for sessions in steps]

        def system_file(path):
            target = Path(path)
            return target.read_text().strip() if target.exists() else None

        result = {
            "schema_version": "0.3",
            "load_test_version": "phase5-harness-v1",
            "status": "measured_load_test",
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "cpu_count": os.cpu_count(),
                "rss_unit": "KiB" if platform.system() == "Linux" else "bytes",
                "cgroup_cpu_max": system_file("/sys/fs/cgroup/cpu.max"),
                "cgroup_memory_max": system_file("/sys/fs/cgroup/memory.max"),
                "storage": "postgresql" if database.postgres else "sqlite_development",
            },
            "config": {"steps": list(steps), "observations_per_step": observations, "pace_ms": pace_ms, "warmup_per_session": WARMUP},
            "steps": records,
            "knee_sessions": knee(records),
            "knee_rule": f"first step with turn p95 > {KNEE_FACTOR}x the single-session p95",
            "source_hash": source_hash(),
            "limitations": [
                "Measures the harness turn path (encode, policy, durable write, playback gate) with mocked speech; no provider, ASR or network",
                "Sessions are threads in one process, like the API; this is not a multi-process or multi-host scale test",
                "Results depend on the container CPU/memory limits and are not comparable across machines",
                "Synchronous ledger: there is no queue to measure, so backpressure appears as durable-write time inside turn latency",
                "Evidence-write failure behaviour is covered by the chaos tests (PHASE5_STATUS item 4), not here",
            ],
        }
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(result, indent=2) + "\n")
        return result
