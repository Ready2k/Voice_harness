"""Run live AC-09 conformance for one family and store the result bound to its current capability report.

PYTHONPATH=src:tests .venv/bin/python scripts/run_ac09.py FAMILY   (credentials from .env; never printed)
FAMILY is one of: openai, openai_live, aws_nova_sonic, xai_grok_voice, google_gemini_live, elevenlabs_agents.
"""
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from adapter_conformance import LiveConformance, run_conformance, write_result  # noqa: E402

from voice_harness.coverage import _load_reports  # noqa: E402

MODULES = {**LiveConformance.MODULES, "openai_live": "voice_harness.adapters.openai_live",
           "elevenlabs_agents": "voice_harness.adapters.elevenlabs_voice"}


def main(family):
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.removeprefix("export ").split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
    report = _load_reports(ROOT / "artifacts" / "phase0")[family]["report"]
    adapter, prime = importlib.import_module(MODULES[family]).conformance_factory()
    result = asyncio.run(run_conformance(adapter, prime=prime))
    path = write_result(result, family, report.report_id, ROOT / "artifacts" / "phase2")
    print(json.dumps({k: result[k] for k in ("excluded", "failures", "passed")}, indent=2), path)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
