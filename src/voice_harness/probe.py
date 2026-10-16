"""Phase 0 probe CLI: `python -m voice_harness.probe {seed,validate,schema}`.

No provider calls are made. Live probes are Phase 2; until then every row is
`unknown` and the report fails closed.
"""

import argparse
import json
import sys

from .capability_report import CapabilityReport, unknown_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="voice_harness.probe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="emit an all-unknown report")
    s.add_argument("provider_family")
    s.add_argument("requested_model_id")
    s.add_argument("--report-id", default=None)
    v = sub.add_parser("validate", help="validate a report JSON file; exit 1 if invalid or fails closed")
    v.add_argument("path")
    sub.add_parser("schema", help="print the JSON Schema")
    a = ap.parse_args(argv)

    if a.cmd == "schema":
        print(json.dumps(CapabilityReport.model_json_schema(), indent=2, sort_keys=True))
        return 0
    if a.cmd == "seed":
        rid = a.report_id or f"cap_{a.provider_family}_seed"
        print(unknown_report(rid, a.provider_family, a.requested_model_id).model_dump_json(indent=2))
        return 0
    with open(a.path) as f:
        report = CapabilityReport.model_validate_json(f.read())
    closed = report.failed_closed()
    out = {"valid": True, "expired": report.expired(), "failed_closed": closed}
    print(json.dumps(out))
    return 1 if closed or out["expired"] else 0


if __name__ == "__main__":
    sys.exit(main())
