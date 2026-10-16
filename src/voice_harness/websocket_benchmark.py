"""Actual TCP WebSocket reference replay, with client-clock round trips."""

import base64
import json
import socket
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import uvicorn
from websockets.sync.client import connect

from .api import create_app
from .contracts import RunConfig
from .engine import Engine


def replay(database, observations, pace_ms):
    from .benchmark import distribution

    results = []
    engine = Engine(database)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(database, directory, "benchmark-only", recover=False)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="error", ws_max_size=16384)
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise TimeoutError("BENCHMARK_SERVER_START_TIMEOUT")
                time.sleep(0.01)
            for concurrency in [1, 5, 10]:
                runs = [
                    engine.create(RunConfig(retain_audio=False).model_dump())
                    for _ in range(concurrency)
                ]

                def worker(run, concurrency=concurrency):
                    durations, spans = [], []
                    with connect(
                        f"ws://127.0.0.1:{port}/api/v1/runs/{run}/ws",
                        additional_headers={
                            "Authorization": "Bearer benchmark-only",
                            "Origin": "http://localhost:8765",
                        },
                        max_size=16384,
                    ) as stream:
                        start = json.loads(stream.recv())
                        deadline = time.perf_counter()
                        sequence = 0
                        for index in range(
                            (observations + concurrency - 1) // concurrency + 10
                        ):
                            sequence += 1
                            message = {
                                "schema_version": "0.3",
                                "message_id": str(sequence),
                                "session_id": start["session_id"],
                                "connection_epoch": start["connection_epoch"],
                                "client_sequence": sequence,
                                "type": "audio.input",
                                "payload": {
                                    "stream_id": "reference-input",
                                    "chunk_sequence": index + 1,
                                    "bytes": base64.b64encode(bytes(960)).decode(),
                                    "codec": "pcm_s16le",
                                    "rate": 24000,
                                    "channels": 1,
                                    "start_sample": index * 480,
                                    "end_sample": (index + 1) * 480,
                                },
                            }
                            sent = time.monotonic_ns()
                            stream.send(json.dumps(message))
                            while True:
                                reply = json.loads(stream.recv())
                                if reply["type"] != "ping":
                                    break
                                sequence += 1
                                stream.send(
                                    json.dumps(
                                        {
                                            **message,
                                            "message_id": str(sequence),
                                            "client_sequence": sequence,
                                            "type": "pong",
                                            "payload": {},
                                        }
                                    )
                                )
                            received = time.monotonic_ns()
                            if (
                                reply["type"] != "audio.accepted"
                                or reply["end_sample"] != (index + 1) * 480
                            ):
                                raise ValueError("REFERENCE_REPLAY_DISCONTINUITY")
                            if index >= 10:
                                durations.append((received - sent) / 1e6)
                                spans.append(
                                    {
                                        "run_id": run,
                                        "client_sequence": sequence,
                                        "send_ns": sent,
                                        "receive_ns": received,
                                    }
                                )
                            deadline += pace_ms / 1000
                            time.sleep(max(0, deadline - time.perf_counter()))
                    return durations, spans

                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    samples = list(pool.map(worker, runs))
                results.append(
                    {
                        "sessions": concurrency,
                        "metric": "client_send_to_server_accepted_round_trip",
                        "distribution": distribution(
                            [v for durations, _ in samples for v in durations]
                        ),
                        "spans": [span for _, spans in samples for span in spans],
                        "clock_domain": "benchmark_client_process",
                        "failures": 0,
                        "discontinuities": 0,
                    }
                )
                for run in runs:
                    engine.cancel(run, "benchmark_complete")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()
    return results
