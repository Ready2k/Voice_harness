"""Run the three core scenarios against the authenticated API without printing keys."""

import json
import time
import uuid
from pathlib import Path

import httpx

from .evidence import verify_bundle


def demo(root="/data", base_url="http://127.0.0.1:8000"):
    root = Path(root)
    token = (root / "operator-token").read_text().strip()
    output = root / "demo"
    output.mkdir(exist_ok=True)
    rows = []
    with httpx.Client(
        base_url=base_url, headers={"Authorization": "Bearer " + token}, timeout=20
    ) as client:
        keys = client.get("/api/v1/signing/public-keys")
        keys.raise_for_status()
        (output / "public-keys.json").write_text(json.dumps(keys.json(), indent=2) + "\n")
        for scenario in [
            "identity-bypass-001",
            "balance-verified-001",
            "unsafe-disclosure-001",
        ]:
            response = client.post(
                "/api/v1/runs",
                json={"scenario_id": scenario, "retain_audio": True},
                headers={"Idempotency-Key": uuid.uuid4().hex},
            )
            response.raise_for_status()
            run_id = response.json()["run_ids"][0]
            deadline = time.monotonic() + 20
            while True:
                response = client.get("/api/v1/runs/" + run_id)
                response.raise_for_status()
                state = response.json()
                if state["state"] in {"completed", "failed", "cancelled"}:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("DEMO_RUN_TIMEOUT")
                time.sleep(0.05)
            response = client.get("/api/v1/runs/" + run_id + "/evidence")
            response.raise_for_status()
            bundle = response.json()
            if not verify_bundle(bundle, keys.json()):
                raise ValueError("DEMO_EVIDENCE_INVALID")
            path = output / (run_id + ".json")
            path.write_text(json.dumps(bundle, indent=2) + "\n")
            rows.append(
                {
                    "scenario": scenario,
                    "run_id": run_id,
                    "state": state["state"],
                    "assurance": state["assurance"],
                    "evidence_verified": True,
                    "bundle": str(path),
                }
            )
    return rows
