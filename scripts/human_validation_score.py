"""Score completed human labels against the automated scorer and the stored judge verdicts (offline, zero spend).

    PYTHONPATH=src .venv/bin/python scripts/human_validation_score.py --labels human-labels-X.json|filled-pack.csv \
        --key KEY-DO-NOT-SHARE-WITH-LABELLER.json --out /tmp/hv-score

Writes human-validation-score.json and human-validation-summary.md: per criterion raw agreement, Cohen's kappa, Wilson 95%
interval and automated->human confusion counts; the same for each judge that has stored verdicts.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from voice_harness import human_validation as hv  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--labels", type=Path, required=True, help="JSON exported by labelling.html, or the filled-in CSV pack")
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args(argv)
    key = json.loads(a.key.read_text())
    result = hv.score(hv.read_labels(a.labels), key)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "human-validation-score.json").write_text(json.dumps(result, indent=2) + "\n")
    (a.out / "human-validation-summary.md").write_text(hv.summary_markdown(result))
    print(hv.summary_markdown(result))


if __name__ == "__main__":
    main()
