"""Versioned pricing table (spec section 11).

A tariff row is data: provider/service/model/region, billing model, effective
dates, rate, rounding and minimum-billing rules, evidence ids. An unknown price
is ``None`` with ``unknown_reason``; it is never zero and never invented. The
shipped table (``data/pricing_table.json``) contains no established vendor
tariff. Tests use ``tests/fixtures/pricing_fixture.json``, which is labelled as
not being a vendor price.
"""

from __future__ import annotations

import hashlib
import json
import os
from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
    InvalidOperation,
)
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .canonical import canonical_json
from .contracts import Closed, Identifier

DEFAULT_TABLE = Path(__file__).parent / "data" / "pricing_table.json"
DecimalText = Annotated[str, Field(pattern=r"^\d+(\.\d+)?$")]
DateText = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
ROUNDING = {
    "ROUND_HALF_UP": ROUND_HALF_UP,
    "ROUND_UP": ROUND_UP,
    "ROUND_DOWN": ROUND_DOWN,
    "ROUND_CEILING": ROUND_CEILING,
    "ROUND_FLOOR": ROUND_FLOOR,
}


def dec(value) -> Decimal:
    """Strict decimal parse: floats and non-numeric text are rejected."""
    if isinstance(value, float | bool):
        raise TypeError("FLOAT_NOT_ALLOWED")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("INVALID_DECIMAL") from error
    if not result.is_finite():
        raise ValueError("INVALID_DECIMAL")
    return result


class Tariff(Closed):
    tariff_id: Identifier
    provider: Identifier
    service: Identifier
    model: Identifier | None
    region: Identifier | None
    component: Identifier
    meter_id: Identifier
    unit: Literal["token", "second", "request", "character"]
    billing_model: Literal["per_token", "per_minute", "per_second", "per_request", "per_character"]
    exclusivity_group: Identifier | None
    exclusivity_priority: Annotated[int, Field(ge=1)]
    effective_from: DateText
    effective_to: DateText | None
    currency: Annotated[str, Field(pattern="^[A-Z]{3}$")] | None
    rate_unit_quantity: DecimalText | None
    unit_price: DecimalText | None
    cached_unit_price: DecimalText | None
    cached_billing: Literal["discounted", "same_rate", "not_documented"]
    quantity_increment: DecimalText | None
    quantity_rounding: Literal["none", "up", "down", "half_up"]
    minimum_billed_quantity: DecimalText | None
    amount_scale: Annotated[int, Field(ge=0, le=12)]
    amount_rounding: Literal["ROUND_HALF_UP", "ROUND_UP", "ROUND_DOWN", "ROUND_CEILING", "ROUND_FLOOR"]
    source_status: Literal["unestablished", "operator_attested", "primary_source", "fixture_not_a_vendor_price"]
    evidence_ids: list[Identifier]
    unknown_reason: Identifier | None

    @model_validator(mode="after")
    def _consistent(self):
        if self.unit_price is None:
            if not self.unknown_reason:
                raise ValueError("unknown price requires unknown_reason")
        else:
            if self.currency is None or self.rate_unit_quantity is None:
                raise ValueError("known price requires currency and rate_unit_quantity")
            if self.source_status == "unestablished":
                raise ValueError("an unestablished tariff cannot carry a price")
            if not self.evidence_ids:
                raise ValueError("known price requires evidence ids")
            if dec(self.rate_unit_quantity) <= 0:
                raise ValueError("rate_unit_quantity must be positive")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must follow effective_from")
        if self.quantity_rounding != "none" and self.quantity_increment is None:
            raise ValueError("quantity rounding requires quantity_increment")
        if self.quantity_increment is not None and dec(self.quantity_increment) <= 0:
            raise ValueError("quantity_increment must be positive")
        return self


class PricingTable:
    def __init__(self, document: dict):
        self.document = document
        self.table_id = document["table_id"]
        self.version = document["version"]
        self.status = document.get("status")
        self.tariffs = [Tariff.model_validate(t) for t in document["tariffs"]]
        ids = [t.tariff_id for t in self.tariffs]
        if len(ids) != len(set(ids)):
            raise ValueError("DUPLICATE_TARIFF_ID")
        self.document_hash = hashlib.sha256(canonical_json(document)).hexdigest()
        self.hashes = {
            t.tariff_id: hashlib.sha256(canonical_json(t.model_dump())).hexdigest() for t in self.tariffs
        }
        # Overlapping validity for one provider/service/model/region/meter is ambiguous.
        for index, a in enumerate(self.tariffs):
            for b in self.tariffs[index + 1 :]:
                same = (a.provider, a.service, a.model, a.region, a.meter_id) == (
                    b.provider, b.service, b.model, b.region, b.meter_id)
                if (
                    same
                    and a.effective_from < (b.effective_to or "9999-12-31")
                    and b.effective_from < (a.effective_to or "9999-12-31")
                ):
                    raise ValueError("OVERLAPPING_TARIFFS: " + a.tariff_id + "," + b.tariff_id)

    def find(self, *, provider, service, model, region, meter_id, usage_date):
        """Most specific tariff effective on ``usage_date`` (model/region None match any)."""
        best = None
        for t in self.tariffs:
            if (t.provider, t.service, t.meter_id) != (provider, service, meter_id):
                continue
            if t.model not in (None, model) or t.region not in (None, region):
                continue
            if not (t.effective_from <= usage_date and (t.effective_to is None or usage_date < t.effective_to)):
                continue
            specificity = (t.model is not None) + (t.region is not None)
            if best is None or specificity > best[0]:
                best = (specificity, t)
        return best[1] if best else None

    def summary(self):
        return {
            "table_id": self.table_id,
            "version": self.version,
            "document_hash": self.document_hash,
            "status": self.status,
            "tariff_count": len(self.tariffs),
        }


@lru_cache(maxsize=8)
def _load(path: str) -> PricingTable:
    return PricingTable(json.loads(Path(path).read_text()))


def load_table(path=None) -> PricingTable:
    """Explicit path, else ``HARNESS_PRICING_TABLE`` (operator-attested table), else the shipped table."""
    return _load(str(path or os.environ.get("HARNESS_PRICING_TABLE") or DEFAULT_TABLE))
