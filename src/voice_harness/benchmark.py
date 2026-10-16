"""Paced reference-framing microbenchmark; mocked speech is not live latency."""

import base64
import hashlib
import json
import os
import platform
import resource
import struct
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .contracts import RunConfig
from .engine import Engine, digest, source_hash
from .events import PROCESS_CLOCK_DOMAIN, Event
from .playback import PlaybackGate
from .policy import authorize_tool
from .storage import Database


def distribution(values):
    ordered = sorted(values)

    def percentile(p):
        return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]

    return {
        "count": len(values),
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
        "p50_ms": percentile(0.5),
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99) if len(values) >= 1000 else None,
        "p99_status": "descriptive" if len(values) >= 1000 else "insufficient_sample",
    }


def benchmark(output, observations=1000, database_url=None, pace_ms=20):
    if observations < 1000:
        raise ValueError("BASELINE_REQUIRES_1000_OBSERVATIONS")
    records = []
    with tempfile.TemporaryDirectory() as directory:
        database = Database(database_url or str(Path(directory) / "benchmark.sqlite"))
        engine = Engine(database)
        for concurrency in [1, 5, 10]:
            for scale in [1, 4]:
                for instrumented in [False, True]:
                    runs = [
                        engine.create(RunConfig().model_dump())
                        for _ in range(concurrency)
                    ]

                    def worker(
                        index,
                        scale=scale,
                        runs=tuple(runs),
                        instrumented=instrumented,
                        concurrency=concurrency,
                    ):
                        pcm = bytes(960 * scale)
                        stages = {
                            k: []
                            for k in [
                                "base64_json_encode",
                                "input_decode",
                                "binary_encode_decode",
                                "canonicalization_hash",
                                "durable_write",
                                "buffer_insert_read",
                                "envelope_construction_serialization",
                                "policy_evaluation",
                                "release_token_issue",
                                "outbound_encode",
                                "client_schedule_ack",
                                "total_path",
                            ]
                        }
                        gate = PlaybackGate(runs[index])
                        raw_observations = []
                        stamp = time.monotonic_ns if instrumented else lambda: 0
                        # Warmup excludes ten paced observations per worker.
                        count = (observations + concurrency - 1) // concurrency
                        deadline = time.perf_counter()
                        for iteration in range(count + 10):
                            start = time.monotonic_ns()
                            wire = json.dumps(
                                {
                                    "bytes": base64.b64encode(pcm).decode(),
                                    "rate": 24000,
                                    "channels": 1,
                                }
                            ).encode()
                            encode = stamp()
                            decoded = base64.b64decode(
                                json.loads(wire)["bytes"], validate=True
                            )
                            decode = stamp()
                            binary = struct.pack("<II", 24000, len(pcm)) + pcm
                            assert binary[8:] == decoded
                            binary_end = stamp()
                            payload = {
                                "index": iteration,
                                "sha256": hashlib.sha256(decoded).hexdigest(),
                                "payload_bytes": len(wire),
                            }
                            digest(payload)
                            hashed = stamp()
                            event = Event(
                                runs[index], "orchestrator", "benchmark.audio", payload
                            )
                            json.dumps(event.to_dict())
                            constructed = stamp()
                            decision = authorize_tool(
                                "get_account_summary",
                                {"account_id": "acct_1001"},
                                allowed_tools={"get_account_summary"},
                                verified=True,
                                account_number="acct_1001",
                            )
                            assert decision.allowed
                            policy_end = stamp()
                            with database.transaction(runs[index]) as tx:
                                event_id = tx.event(
                                    "orchestrator", "benchmark.observation", payload
                                )
                            persisted = stamp()
                            unit = gate.hold(
                                "response", decoded, sample_range=(0, len(decoded) // 2)
                            )
                            buffered = stamp()
                            token = gate.approve(unit)
                            token_end = stamp()
                            outbound = base64.b64encode(gate.release(token))
                            outbound_end = stamp()
                            assert base64.b64decode(outbound) == pcm
                            ack_end = time.monotonic_ns()
                            if iteration >= 10:
                                observation = {
                                    "event_id": event_id,
                                    "run_id": runs[index],
                                    "operation_id": f"{index}:{iteration}",
                                    "start_ns": start,
                                    "end_ns": ack_end,
                                    "clock_domain_id": PROCESS_CLOCK_DOMAIN,
                                    "stage_spans_ns": [],
                                }
                                stages["total_path"].append((ack_end - start) / 1e6)
                                if instrumented:
                                    for name, left, right in [
                                        ("base64_json_encode", start, encode),
                                        ("input_decode", encode, decode),
                                        ("binary_encode_decode", decode, binary_end),
                                        ("canonicalization_hash", binary_end, hashed),
                                        (
                                            "envelope_construction_serialization",
                                            hashed,
                                            constructed,
                                        ),
                                        ("policy_evaluation", constructed, policy_end),
                                        ("durable_write", policy_end, persisted),
                                        ("buffer_insert_read", persisted, buffered),
                                        ("release_token_issue", buffered, token_end),
                                        ("outbound_encode", token_end, outbound_end),
                                        ("client_schedule_ack", outbound_end, ack_end),
                                    ]:
                                        stages[name].append((right - left) / 1e6)
                                        observation["stage_spans_ns"].append(
                                            {
                                                "name": name,
                                                "start_ns": left,
                                                "end_ns": right,
                                            }
                                        )
                                raw_observations.append(observation)
                            deadline += pace_ms / 1000
                            time.sleep(max(0, deadline - time.perf_counter()))
                        return stages, len(wire), len(binary), raw_observations

                    wall, cpu = time.perf_counter(), time.process_time()
                    with ThreadPoolExecutor(max_workers=concurrency) as pool:
                        results = list(pool.map(worker, range(concurrency)))
                    elapsed, cpu = time.perf_counter() - wall, time.process_time() - cpu
                    from .relational import metrics

                    metrics(database, [o for result in results for o in result[3]])
                    for run_id in runs:
                        with database.transaction(run_id) as tx:
                            tx.state["state"] = "completed"
                            tx.event(
                                "orchestrator",
                                "benchmark.completed",
                                {
                                    "assessment": "performance_only",
                                    "assurance": "untestable",
                                },
                            )
                    combined = {
                        name: [v for result, _, _, _ in results for v in result[name]]
                        for name in results[0][0]
                    }
                    records.append(
                        {
                            "sessions": concurrency,
                            "payload_scale": scale,
                            "instrumented": instrumented,
                            "reference_bytes": results[0][1],
                            "binary_bytes": results[0][2],
                            "pcm_bytes": 960 * scale,
                            "stages": {
                                k: distribution(v) for k, v in combined.items() if v
                            },
                            "elapsed_seconds": elapsed,
                            "cpu_seconds": cpu,
                            "run_ids": runs,
                            "observations": [
                                o for result in results for o in result[3]
                            ],
                            "throughput_observations_s": len(combined["total_path"])
                            / elapsed,
                            "timeouts": 0,
                            "failures": 0,
                            "exclusions": [],
                            "queue_depth_max": concurrency,
                            "dropped_samples": 0,
                            "discontinuous_samples": 0,
                        }
                    )
        # Explicit slow-storage and stalled-consumer faults are separate from SLOs.
        fault_run = engine.create(RunConfig().model_dump())
        fault_start = time.perf_counter_ns()
        with database.transaction(fault_run) as tx:
            time.sleep(0.025)
            tx.event(
                "orchestrator", "benchmark.slow_storage", {"injected_delay_ms": 25}
            )
        slow_ms = (time.perf_counter_ns() - fault_start) / 1e6
        buffer = bytearray()
        rejected = False
        for _ in range(101):
            if len(buffer) + 960 > engine.MAX_AUDIO_BYTES:
                rejected = True
                break
            buffer.extend(bytes(960))

        def system_file(path):
            target = Path(path)
            return target.read_text().strip() if target.exists() else None

        output_data = {
            "schema_version": "0.3",
            "benchmark_version": "phase1-reference-v1",
            "status": "measured_microbenchmark",
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "cpu_count": os.cpu_count(),
                "max_rss_native_units": resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
                "rss_unit": "KiB" if platform.system() == "Linux" else "bytes",
                "cpu_scope": "harness_process_only",
                "cgroup_cpu_max": system_file("/sys/fs/cgroup/cpu.max"),
                "cgroup_memory_max": system_file("/sys/fs/cgroup/memory.max"),
                "storage": "postgresql" if database.postgres else "sqlite_development",
            },
            "config": {
                "observations_per_profile": observations,
                "pace_ms": pace_ms,
                "warmup_per_worker": 10,
            },
            "profiles": records,
            "faults": {
                "slow_storage_observed_ms": slow_ms,
                "stalled_consumer_rejected": rejected,
                "buffer_max_bytes": len(buffer),
            },
            "limitations": [
                "Component microbenchmark; no live ASR or provider latency",
                "Synthetic PCM silence; each frame is one controlled turn",
                "Client schedule/ack is a software fixture, not a browser or DAC measurement",
                "No resampling: matching 24kHz passes through",
                "Full orchestration SLO remains provisional",
                "Synchronous ledger: queue wait is not applicable; lock contention is included in durable_write",
            ],
        }
        output_data["config_hash"] = digest(output_data["config"])
        output_data["source_hash"] = source_hash()
        output_data["environment_hash"] = digest(output_data["environment"])
        from .websocket_benchmark import replay

        output_data["websocket_profiles"] = replay(database, observations, pace_ms)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(output_data, indent=2) + "\n")
        return output_data
