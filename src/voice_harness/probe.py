"""Phase 0 probe CLI: `python -m voice_harness.probe {seed,validate,schema,from-*-observations}`.

No provider calls are made here. Live probes live in scripts/probe/ and write observation files; the
`from-*-observations` commands turn those into reports, and a row without probe evidence stays `unknown`,
so the report fails closed.
"""

import argparse
import json
import sys

from .capability_report import OPERATOR_ATTESTATION, CapabilityReport, unknown_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="voice_harness.probe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="emit an all-unknown report")
    s.add_argument("provider_family")
    s.add_argument("requested_model_id")
    s.add_argument("--report-id", default=None)
    v = sub.add_parser("validate", help="validate a report JSON file; exit 1 if invalid or fails closed")
    v.add_argument("path")
    o = sub.add_parser("from-nova-observations", help="build a report from nova_probe.mjs output")
    o.add_argument("path")
    o.add_argument("--report-id", required=True)
    o.add_argument("--attest", action="store_true", help="record the operator terms attestation")
    oa = sub.add_parser("from-openai-observations", help="build a report from openai_probe.mjs output")
    oa.add_argument("path")
    oa.add_argument("--report-id", required=True)
    oa.add_argument("--attest", action="store_true", help="record the operator terms attestation")
    ox = sub.add_parser("from-xai-observations", help="build a report from xai_probe.py output")
    ox.add_argument("path")
    ox.add_argument("--report-id", required=True)
    ox.add_argument("--attest", action="store_true", help="record the operator terms attestation")
    sub.add_parser("schema", help="print the JSON Schema")
    a = ap.parse_args(argv)

    if a.cmd == "schema":
        print(json.dumps(CapabilityReport.model_json_schema(), indent=2, sort_keys=True))
        return 0
    if a.cmd == "seed":
        rid = a.report_id or f"cap_{a.provider_family}_seed"
        print(unknown_report(rid, a.provider_family, a.requested_model_id).model_dump_json(indent=2))
        return 0
    if a.cmd == "from-nova-observations":
        from .nova_report import load

        print(load(a.path, a.report_id, OPERATOR_ATTESTATION if a.attest else None).model_dump_json(indent=2))
        return 0
    if a.cmd == "from-openai-observations":
        from .openai_report import load as load_openai

        print(load_openai(a.path, a.report_id, OPERATOR_ATTESTATION if a.attest else None).model_dump_json(indent=2))
        return 0
    if a.cmd == "from-xai-observations":
        from .xai_report import load as load_xai

        print(load_xai(a.path, a.report_id, OPERATOR_ATTESTATION if a.attest else None).model_dump_json(indent=2))
        return 0
    with open(a.path) as f:
        report = CapabilityReport.model_validate_json(f.read())
    closed = report.failed_closed()
    out = {"valid": True, "expired": report.expired(), "failed_closed": closed}
    print(json.dumps(out))
    return 1 if closed or out["expired"] else 0


if __name__ == "__main__":
    sys.exit(main())
