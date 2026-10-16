from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
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
