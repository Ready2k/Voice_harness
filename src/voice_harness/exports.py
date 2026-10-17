"""Evidence export forms (spec section 10, OB-04): NDJSON, embedded schemas, reviewer view."""

import functools
import json
from pathlib import Path

from . import __version__
from .canonical import canonical_json
from .engine import digest
from .scenario_defs import SCENARIOS, definition_hash

_CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
REVIEWER_EXPORT_TYPE = "restricted_reviewer_derived_view"
NDJSON_MEDIA_TYPE = "application/x-ndjson"


@functools.lru_cache(maxsize=1)
def _schema_files():
    if not _CONTRACTS.is_dir():
        return {}
    return {
        path.name[: -len(".schema.json")]: json.loads(path.read_text())
        for path in sorted(_CONTRACTS.glob("*.schema.json"))
    }


def embedded_schemas():
    """Copy of the checked-in JSON Schemas shipped inside every export."""
    return json.loads(json.dumps(_schema_files()))


def schema_digests():
    return {name: digest(schema) for name, schema in _schema_files().items()}


def versions(config=None):
    config = config or {}
    return {
        "harness": __version__,
        "event_schema": "0.3",
        "manifest_schema": "0.3",
        "canonicalization": "RFC8785",
        "hash": "sha256",
        "judging_section": "1",
        "scenario_id": config.get("scenario_id"),
        "scenario_version": config.get("scenario_version"),
        "policy_profile": config.get("policy_profile"),
    }


def fixture_oracle(scenario_id):
    """Restricted expected-behaviour oracle for a built-in scenario (no secrets)."""
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        return {"scenario_id": scenario_id, "available": False, "reason": "SCENARIO_NOT_BUILTIN"}
    return {
        "scenario_id": scenario_id,
        "available": True,
        "scenario_version": scenario["version"],
        "content_hash": definition_hash(scenario),
        "goal": scenario["goal"],
        "expected_tool_sequence": scenario["expected_tool_sequence"],
        "expected_tool_order": scenario["expected_tool_order"],
        "applicable_criteria": scenario["applicable_criteria"],
        "turns": [
            {
                "expected_intent": t["expected_intent"],
                "allowed_actions": t["allowed_actions"],
                "prohibited_actions": t["prohibited_actions"],
            }
            for t in scenario["turns"]
        ],
        "reference_transcript": scenario["reference_transcript"],
    }


def reviewer_export(evidence, run_id):
    """Newly sealed derived view: ledger verbatim, plus restricted oracle, new manifest.

    No hashed payload is rewritten.  The derived manifest has its own hash and
    links to the source manifest by hash; the source manifest is embedded so a
    reviewer can check that link without provider or database access.
    """
    source = evidence.bundle(run_id)
    src = source["manifest"]
    oracle = fixture_oracle(src["scenario"]["scenario_id"])
    manifest = {
        **src,
        "export_type": REVIEWER_EXPORT_TYPE,
        "access_classification": evidence.ACCESS_CLASSIFICATION,
        "fixture_oracle": oracle,
        "omitted_from_standard_export": [
            k for k in src["omitted_from_standard_export"] if k != "fixture_oracle"
        ],
        "derived_from": {
            "source_manifest_hash": source["manifest_hash"],
            "source_export_type": src["export_type"],
            "source_final_ledger_hash": src["final_ledger_hash"],
            "source_event_count": src["event_count"],
        },
        "transformation": {
            "transformation_id": "reviewer_view_v1",
            "created_at": evidence.clock(),
            "events_modified": False,
            "artifacts_modified": False,
            "steps": [
                {"op": "copy_events_verbatim", "count": src["event_count"]},
                {"op": "copy_artifacts_verbatim", "count": len(source["artifacts"])},
                {"op": "add_section", "section": "fixture_oracle"},
                {"op": "reseal_manifest", "algorithm": "sha256+rfc8785"},
            ],
            "omitted": ["challenge_answers", "isolation_keys", "provider_credentials"],
            "integrity_claim": "derived view proves its own integrity and its link to the "
            "source manifest hash, not the contents of omitted source artifacts",
        },
    }
    # AC-08: the derived view is sealed as its own manifest (the copied source
    # signature fields are replaced); the source signature travels alongside.
    envelope = evidence.sign_manifest(manifest)
    return {
        "manifest": manifest,
        "manifest_hash": digest(manifest),
        "events": source["events"],
        "artifacts": source["artifacts"],
        "schemas": source["schemas"],
        "versions": source["versions"],
        "source_manifest": src,
        **({"signature": envelope, "source_signature": source["signature"]} if envelope else {}),
        "cost": source.get("cost"),
        "judging": source.get("judging"),
    }


def to_ndjson(bundle):
    """One JSON record per line: manifest, versions, schemas, events, artifacts."""

    def line(record_type, **fields):
        return json.dumps(
            {"record_type": record_type, **fields}, sort_keys=True, separators=(",", ":")
        )

    lines = [
        line("manifest", manifest=bundle["manifest"], manifest_hash=bundle["manifest_hash"]),
        line("versions", versions=bundle.get("versions")),
    ]
    if "signature" in bundle:
        lines.append(line("signature", signature=bundle["signature"]))
    if "source_manifest" in bundle:
        lines.append(line("source_manifest", manifest=bundle["source_manifest"]))
    if "source_signature" in bundle:
        lines.append(line("source_signature", signature=bundle["source_signature"]))
    if bundle.get("cost") is not None:
        lines.append(line("cost", section=bundle["cost"]))
    if bundle.get("judging") is not None:
        lines.append(line("judging", section=bundle["judging"]))
    lines += [line("schema", name=n, schema=s) for n, s in (bundle.get("schemas") or {}).items()]
    lines += [line("event", event=e) for e in bundle["events"]]
    lines += [line("artifact", artifact=a) for a in bundle["artifacts"]]
    return "\n".join(lines) + "\n"


def from_ndjson(text):
    """Inverse of to_ndjson, yielding a bundle accepted by verify_bundle."""
    bundle = {"events": [], "artifacts": [], "schemas": {}}
    for raw in text.splitlines():
        if not raw.strip():
            continue
        record = json.loads(raw)
        kind = record["record_type"]
        if kind == "manifest":
            bundle["manifest"], bundle["manifest_hash"] = record["manifest"], record["manifest_hash"]
        elif kind == "versions":
            bundle["versions"] = record["versions"]
        elif kind == "source_manifest":
            bundle["source_manifest"] = record["manifest"]
        elif kind in ("signature", "source_signature"):
            bundle[kind] = record["signature"]
        elif kind == "cost":
            bundle["cost"] = record["section"]
        elif kind == "judging":
            bundle["judging"] = record["section"]
        elif kind == "schema":
            bundle["schemas"][record["name"]] = record["schema"]
        elif kind == "event":
            bundle["events"].append(record["event"])
        elif kind == "artifact":
            bundle["artifacts"].append(record["artifact"])
        else:
            raise ValueError("UNKNOWN_NDJSON_RECORD")
    return bundle


def download_links(run_id):
    base = f"/api/v1/runs/{run_id}/evidence/download"
    return [
        {
            "view": view,
            "format": fmt,
            "method": "GET",
            "href": f"{base}?view={view}&format={fmt}",
            "authentication": "bearer_operator_token",
            "media_type": NDJSON_MEDIA_TYPE if fmt == "ndjson" else "application/json",
        }
        for view in ("standard", "reviewer")
        for fmt in ("json", "ndjson")
    ]


def render(bundle, fmt):
    """Serialize a bundle for download; returns (bytes, media_type)."""
    if fmt == "ndjson":
        return to_ndjson(bundle).encode(), NDJSON_MEDIA_TYPE
    return canonical_json(bundle), "application/json"
