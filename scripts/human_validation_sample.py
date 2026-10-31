"""Draw a reproducible, provider-blind human-labelling pack from a finished bake-off run folder (read-only, zero spend).

    PYTHONPATH=src .venv/bin/python scripts/human_validation_sample.py artifacts/bakeoff-2026-10-29-standard-5 \
        --out /tmp/hv-pack [--ledger .local/<run>/bakeoff.sqlite] [--fraction 0.10] [--min-per-provider 8] [--seed S]

Writes labelling-pack.json, labelling-pack.csv, labelling.html (self-contained; exports labels as JSON) and
KEY-DO-NOT-SHARE-WITH-LABELLER.json (blind code -> run, provider, automated and judge verdicts). Give the labeller
the first three only. Cells without ledger evidence (e.g. carried-over results) are not sampled.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from voice_harness import human_validation as hv  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("folder", type=Path, help="bake-off run folder holding report.json")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--ledger", type=Path, help="evidence sqlite (default: .local/<folder name>/bakeoff.sqlite)")
    p.add_argument("--fraction", type=float, default=0.10)
    p.add_argument("--min-per-provider", type=int, default=8)
    p.add_argument("--seed", default="human-validation-1")
    a = p.parse_args(argv)
    ledger = hv.find_ledger(a.folder, a.ledger)
    if not ledger:
        sys.exit("No evidence ledger found; pass --ledger.")
    observations = hv.load_observations(a.folder)
    db = hv.open_readonly(ledger)
    try:
        available = hv.run_ids_in_ledger(db)
        sample = hv.draw_sample(observations, available, fraction=a.fraction, min_per_provider=a.min_per_provider, seed=a.seed)
        pack, key = hv.build_pack(a.folder, db, observations, sample, seed=a.seed, fraction=a.fraction, min_per_provider=a.min_per_provider)
    finally:
        db.close()
    files = hv.write_pack(a.out, pack, key)
    completed = [o for o in observations if o.get("completed")]
    skipped = sum(1 for o in completed if o.get("run_id") not in available)
    per = {}
    for c in key["cells"].values():
        per[c["provider"]] = per.get(c["provider"], 0) + 1
    print(f"Sampled {len(sample)} cells of {len(completed)} completed ({skipped} without ledger evidence skipped).")
    print("Per provider:", dict(sorted(per.items())))
    for name, path in files.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
