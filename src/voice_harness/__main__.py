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
    verify.add_argument("--public-key", action="append", default=[], help="trusted PEM/base64 key or key-ring JSON (repeatable)")
    verify.add_argument("--revoked", action="append", default=[], help="key id to treat as revoked (repeatable)")
    verify.add_argument("--allow-unsigned", action="store_true", help="legacy local use: accept a bundle that declares itself unsigned")
    keys = sub.add_parser("signing-keys")
    keys.add_argument("action", choices=["public", "rotate", "revoke", "history"])
    keys.add_argument("--data-dir", default="/data")
    keys.add_argument("--key-id")
    keys.add_argument("--reason", default="operator action")
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
        from .signing import Keyring, SigningError, check_bundle_signature, load_keyring

        bundle = json.loads(Path(args.path).read_text())
        if not args.public_key and not args.allow_unsigned:
            print(json.dumps({"verified": False, "reason": "NO_TRUSTED_KEY: pass --public-key or --allow-unsigned"}))
            raise SystemExit(2)
        integrity = verify_bundle(bundle)
        try:
            ring = Keyring()
            for source in args.public_key:
                loaded = load_keyring(Path(source))
                ring.keys.update(loaded.keys)
                ring.revoked |= loaded.revoked
            signature = check_bundle_signature(
                bundle, ring if args.public_key else None,
                allow_unsigned=args.allow_unsigned, revoked=args.revoked,
            )
        except (SigningError, OSError, ValueError) as error:
            signature = str(error) or "KEYRING_INVALID"
        valid = integrity and signature == "ok"
        print(json.dumps({"verified": valid, "integrity": integrity, "signature": signature}))
        if not valid:
            raise SystemExit(1)
    elif args.command == "signing-keys":
        from .signing import KeyStore

        store = KeyStore(args.data_dir)
        if args.action == "rotate":
            store.rotate(args.reason)
        elif args.action == "revoke":
            store.revoke(args.key_id, args.reason)
        print(json.dumps(store.history() if args.action == "history" else store.public_keys(), indent=2))
    elif args.command == "demo":
        from .demo import demo

        print(json.dumps(demo(), indent=2))


if __name__ == "__main__":
    main()
