"""One-pass calibration of a Bedrock judge on the existing holdout (no policy approval, no blocking).

Usage: PYTHONPATH=src python scripts/calibrate_bedrock_judge.py MODEL_ID OUT.json
AWS credentials are read from the git-ignored session file into this process only; never printed.
"""
import json
import os
import sys

from voice_harness.adapters.nova_sonic import SESSION_ENV
from voice_harness.judges import calibration
from voice_harness.judges.backends import BedrockConverseBackend

TESTED = [("openai", "gpt-realtime-2.1", "openai"), ("aws_nova_sonic", "amazon.nova-2-5-sonic", "nova")]


def independence(cfg):
    return [{"tested_provider": p, "tested_model": m, "tested_family": f,
             "independent": cfg.provider != p and cfg.model_id != m and cfg.family != f,
             "same_provider": cfg.provider == p, "same_family": cfg.family == f} for p, m, f in TESTED]


def main(model, out):
    for line in SESSION_ENV.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k] = v.strip("'\"")
    os.environ["VOICE_HARNESS_LIVE_JUDGES"] = "bedrock"
    backend = BedrockConverseBackend(model)
    report = calibration.evaluate_holdout(backend, calibration.default_policy(calibration.fingerprint(backend.config)))
    report["independence_from_tested_models"] = independence(backend.config)
    with open(out, "w") as fh:
        json.dump(report, fh, indent=1, sort_keys=True)


if __name__ == "__main__":
    main(*sys.argv[1:3])
