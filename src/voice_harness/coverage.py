from __future__ import annotations

import importlib
import importlib.util
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Coverage:
    provider_family: str
    model_id: str | None
    api_surface: str | None
    region: str | None
    coverage_state: str
    checked_at: str | None = None
    capability_report_id: str | None = None
    reason_codes: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_registry() -> list[Coverage]:
    """Keep all requested families visible without implying support."""
    return [
        Coverage(
            "openai", None, None, None, "not_implemented", reason_codes=("NO_ADAPTER",)
        ),
        Coverage(
            "aws_nova_sonic",
            None,
            None,
            None,
            "not_implemented",
            reason_codes=("NO_ADAPTER",),
        ),
        Coverage(
            "xai_grok_voice",
            None,
            None,
            None,
            "not_implemented",
            reason_codes=("NO_ADAPTER",),
        ),
        Coverage(
            "google_gemini_live",
            None,
            None,
            None,
            "not_implemented",
            reason_codes=("NO_ADAPTER",),
        ),
    ]


def verified_report(
    provider_family: str,
    model_id: str,
    api_surface: str,
    region: str,
    capability_report_id: str,
    evidence_ids: tuple[str, ...],
) -> Coverage:
    return Coverage(
        provider_family,
        model_id,
        api_surface,
        region,
        "implemented_and_verified",
        datetime.now(UTC).isoformat(),
        capability_report_id,
        (),
        evidence_ids,
    )


FAMILIES = ("openai", "aws_nova_sonic", "xai_grok_voice", "google_gemini_live")
# Adapter module per family; absence of the module means NO_ADAPTER.
ADAPTER_MODULES = {
    "openai": "voice_harness.adapters.openai_realtime",
    "aws_nova_sonic": "voice_harness.adapters.nova_sonic",
}
DEFAULT_REPORT_DIR = Path(
    os.environ.get("HARNESS_PHASE0_DIR")
    or Path(__file__).resolve().parents[2] / "artifacts" / "phase0"
)
DEFAULT_AC09_DIR = Path(
    os.environ.get("HARNESS_PHASE2_DIR")
    or Path(__file__).resolve().parents[2] / "artifacts" / "phase2"
)


def _adapter_present(family: str) -> bool:
    module = ADAPTER_MODULES.get(family)
    try:
        return bool(module and importlib.util.find_spec(module))
    except (ImportError, ValueError):
        return False


def _runtime_reasons(family: str) -> list[str]:
    """Adapter runtime preflight (e.g. Nova needs node + bridge + deps). Empty = can run here."""
    module = ADAPTER_MODULES.get(family)
    try:
        fn = getattr(importlib.import_module(module), "runtime_status", None) if module else None
    except ImportError:
        return ["ADAPTER_RUNTIME_UNAVAILABLE"]
    return ["ADAPTER_RUNTIME_UNAVAILABLE"] if fn and fn() else []


def _load_reports(report_dir: Path) -> dict[str, dict[str, Any]]:
    """Latest *-capability-report.json per family; malformed files are skipped."""
    from .capability_report import CapabilityReport

    out: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(report_dir).glob("*-capability-report.json")):
        try:
            report = CapabilityReport.model_validate_json(path.read_text())
        except (OSError, ValueError):
            continue
        prev = out.get(report.provider_family)
        if prev is None or report.checked_at > prev["report"].checked_at:
            out[report.provider_family] = {"report": report, "path": path.name}
    return out


def _ac09_passed(ac09_dir: Path, family: str, report_id: str) -> bool:
    """AC-09 result file must pass and be bound to the same capability report."""
    try:
        data = json.loads((Path(ac09_dir) / f"{family}-ac09.json").read_text())
    except (OSError, ValueError):
        return False
    return (
        data.get("passed") is True
        and data.get("capability_report_id") == report_id
        and not data.get("failures")
    )


def build_registry(
    report_dir: Path | str | None = None,
    ac09_dir: Path | str | None = None,
    now: datetime | None = None,
    adapter_present=_adapter_present,
    runtime_reasons=_runtime_reasons,
) -> list[Coverage]:
    """Derive coverage per family. Only implemented_and_verified counts as support."""
    now = now or datetime.now(UTC)
    reports = _load_reports(Path(report_dir or DEFAULT_REPORT_DIR))
    ac09_dir = Path(ac09_dir or DEFAULT_AC09_DIR)
    items: list[Coverage] = []
    for family in FAMILIES:
        entry = reports.get(family)
        if not adapter_present(family):
            reasons = ("NO_ADAPTER",) + (("REPORT_WITHOUT_ADAPTER",) if entry else ())
            items.append(Coverage(family, None, None, None, "not_implemented", reason_codes=reasons))
            continue
        if entry is None:
            items.append(
                Coverage(family, None, None, None, "implemented_but_unverified",
                         reason_codes=("NO_CAPABILITY_REPORT",))
            )
            continue
        r = entry["report"]
        reasons: list[str] = list(runtime_reasons(family))
        if r.expired(now):
            reasons.append("REPORT_EXPIRED")
        if r.failed_closed():
            reasons.append("REPORT_FAILED_CLOSED")
        if not _ac09_passed(ac09_dir, family, r.report_id):
            reasons.append("AC09_NOT_PASSED")
        evidence = tuple(
            sorted({e for row in r.rows for e in row.evidence.probe_event_ids})
        )
        items.append(
            Coverage(
                family,
                r.returned_model_id or r.requested_model_id,
                r.api_surface,
                r.region,
                "implemented_and_verified" if not reasons else "implemented_but_unverified",
                r.checked_at,
                r.report_id,
                tuple(reasons),
                evidence if not reasons else (),
            )
        )
    return items


def summarize(items: list[Coverage]) -> dict[str, Any]:
    """API/export payload. Unordered by design: no ranking, no score."""
    verified = sum(1 for c in items if c.coverage_state == "implemented_and_verified")
    return {
        "items": [c.to_dict() for c in items],
        "verified_count": verified,
        "requested_count": len(items),
        "statement": f"verified for {verified} of {len(items)} requested provider families",
        "ranking": None,
    }
