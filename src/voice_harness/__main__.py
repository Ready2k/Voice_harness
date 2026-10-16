from __future__ import annotations

import argparse
import json

from .golden import run_identity_bypass
from .recovery import run_recovery


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    golden = sub.add_parser("golden-run")
    golden.add_argument("--output")
    recovery = sub.add_parser("recovery-run")
    recovery.add_argument("--database", default="/data/execution.sqlite")
    sub.add_parser("serve")
    bench = sub.add_parser("benchmark")
    bench.add_argument("--output", default="/data/phase1-baseline.json")
    bench.add_argument("--observations", type=int, default=1000)
    verify = sub.add_parser("verify-bundle")
    verify.add_argument("path")
    sub.add_parser("demo")
    args = parser.parse_args()
    if args.command == "golden-run":
        result = run_identity_bypass(args.output)
        print(json.dumps({k: v for k, v in result.items() if k != "ledger"}, indent=2))
    elif args.command == "recovery-run":
        print(json.dumps(run_recovery(args.database), indent=2))
    elif args.command == "serve":
        from .api import serve

        serve()
    elif args.command == "benchmark":
        import os

        from .benchmark import benchmark

        result = benchmark(
            args.output, args.observations, os.environ.get("HARNESS_DATABASE_URL")
        )
        print(json.dumps({"output": args.output, "profiles": len(result["profiles"])}))
    elif args.command == "verify-bundle":
        from pathlib import Path

        from .evidence import verify_bundle

        valid = verify_bundle(json.loads(Path(args.path).read_text()))
        print(json.dumps({"verified": valid}))
        if not valid:
            raise SystemExit(1)
    elif args.command == "demo":
        from .demo import demo

        print(json.dumps(demo(), indent=2))


if __name__ == "__main__":
    main()
