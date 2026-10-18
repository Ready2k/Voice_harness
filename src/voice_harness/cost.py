"""Cost accounting (spec section 11, AC-12, AC-19).

The ledger holds ``cost.usage_reported`` events (provider-reported or labelled
estimated usage). Cost lines are *derived*: pricing table + reports (+ invoice
allocations) -> lines -> per-currency summaries. Derivation is deterministic, so
``verify_cost_section`` can recompute a bundle's section and detect forgery.

Rules enforced here:
* decimal arithmetic only (floats are rejected); amounts are strings;
* unknown quantity/rate/tariff gives amount ``None`` with a reason, never zero;
* a line supersedes (never adds to) another when it is a later revision, a
  declared correction, a cumulative report covering incremental ones, a higher
  provenance of the same meter/scope, a declared covering scope, or the
  preferred alternative of an exclusivity group;
* invoice-reconciled lines exist only for an allocation with method and
  evidence that matches a recorded meter/scope; everything else stays in an
  account-period reconciliation record.
"""

from __future__ import annotations

import hashlib
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext

from .canonical import canonical_json
from .event_contracts import CostExpectationDeclared, CostUsageReported
from .pricing import ROUNDING, PricingTable, dec, load_table

SCHEMA_VERSION = "0.3"
RANK = {"unknown": 0, "estimated": 1, "provider_reported_usage": 2, "invoice_reconciled": 3}
KNOWN_SCOPES = {"request", "session", "account_period"}
PROVENANCES = ("estimated", "provider_reported_usage", "invoice_reconciled")


def _digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _text(value: Decimal) -> str:
    return format(value, "f")


# ---------------------------------------------------------------- recording

def record_usage(database, run_id, report: dict):
    """Validate and append one ``cost.usage_reported`` event; returns its event id."""
    content = CostUsageReported.model_validate(report).model_dump()
    with database.transaction(run_id) as tx:
        return tx.event("orchestrator", "cost.usage_reported", content)


def ensure_expected_components(database, run_id, components, declared_by="orchestrator"):
    """Declare components, unioned with the latest declaration (the newest declaration replaces older ones).

    Appends nothing when every component is already expected; returns the event id or None.
    """
    current = []
    for event in database.events(run_id):
        if event["type"] == "cost.expectation_declared":
            current = list(event["content"]["components"])
    merged = current + [c for c in components if c not in current]
    if merged == current:
        return None
    return declare_expected_components(database, run_id, merged, declared_by)


def declare_expected_components(database, run_id, components, declared_by="orchestrator"):
    content = CostExpectationDeclared.model_validate(
        {"components": list(components), "declared_by": declared_by}
    ).model_dump()
    with database.transaction(run_id) as tx:
        return tx.event("orchestrator", "cost.expectation_declared", content)


def reports_from_adapter_usage(provider, usage, *, scope_id, usage_date, revision=1, service=None, model=None,
                               region=None, component="voice", evidence_event_ids=(), generation_state=None,
                               assumptions=(), meter_map=None):
    """Translate an adapter ``Usage`` (dict or model) into report dicts.

    Provider-reported only; nothing is estimated. Nova usage is cumulative per
    session (adapter ``raw['cumulative']``); OpenAI ``response.done`` usage is
    per response. Modality meters are used only when the provider supplied a
    breakdown; otherwise the ``*_unclassified`` meters are used, which have no
    established tariff and therefore price as unknown rather than guessed.
    """
    data = usage if isinstance(usage, dict) else usage.model_dump()
    raw = data.get("raw") or {}
    cumulative = bool(raw.get("cumulative"))
    base = {
        "component": component, "provider": provider, "service": service or provider, "model": model,
        "region": region, "billing_scope_id": scope_id, "scope_type": "session" if cumulative else "request",
        "report_kind": "cumulative" if cumulative else "incremental", "revision": revision,
        "corrects_report_id": None, "covers_scope_ids": [], "provenance": "provider_reported_usage",
        "generation_state": generation_state or ("cancelled" if raw.get("status") == "cancelled" else "completed"),
        "usage_date": usage_date, "interval_start_ms": None, "interval_end_ms": None,
        "estimation_method": None, "assumptions": list(assumptions), "unknown_reason": None,
        "evidence_event_ids": list(evidence_event_ids), "cached_quantity": None, "unit": "token",
    }
    out = []
    meter_map = meter_map or {}

    def add(meter, quantity, cached=None):
        unknown = None if quantity is not None else "USAGE_NOT_REPORTED"
        out.append({**base, "report_id": f"{scope_id}:{meter}" + ("" if cumulative else f":{revision}"),
                    "meter_id": meter_map.get(meter, meter), "quantity": None if quantity is None else str(int(quantity)),
                    "cached_quantity": None if cached is None else str(int(cached)), "unknown_reason": unknown})

    for side, total_key, details_key in (("input", "input_tokens", "input_token_details"),
                                         ("output", "output_tokens", "output_token_details")):
        total = data.get(total_key)
        details = raw.get(details_key) if isinstance(raw.get(details_key), dict) else None
        if details is None:  # Nova usageEvent: details.total.{input|output}.{speechTokens,textTokens}
            nova = ((raw.get("details") or {}).get("total") or {}).get(side)
            if isinstance(nova, dict) and "speechTokens" in nova and "textTokens" in nova:
                details = {"audio_tokens": nova["speechTokens"], "text_tokens": nova["textTokens"]}
        audio, text = (details or {}).get("audio_tokens"), (details or {}).get("text_tokens")
        cached_total = (details or {}).get("cached_tokens") or 0
        cached_by = (details or {}).get("cached_tokens_details") or {}
        if (
            total is not None and details and audio is not None and text is not None
            and audio + text == total and (not cached_total or {"audio_tokens", "text_tokens"} <= set(cached_by))
        ):
            add(f"audio_{side}_tokens", audio, cached_by.get("audio_tokens") if cached_total else None)
            add(f"text_{side}_tokens", text, cached_by.get("text_tokens") if cached_total else None)
        else:
            add(f"{side}_tokens_unclassified", total)
    seconds = raw.get("billable_audio_seconds")  # xAI bills voice per minute; report as the provider stated it
    if isinstance(seconds, int | float) and not isinstance(seconds, bool) and seconds >= 0:
        out.append({**base, "report_id": f"{scope_id}:billable_audio_seconds:{revision}",
                    "meter_id": meter_map.get("billable_audio_seconds", "billable_audio_seconds"),
                    "unit": "second", "quantity": format(Decimal(str(seconds)), "f"), "unknown_reason": None})
    return out


# ---------------------------------------------------------------- pricing

def _round_quantity(quantity: Decimal, tariff):
    if tariff.quantity_rounding == "none":
        return quantity
    increment = dec(tariff.quantity_increment)
    steps = quantity / increment
    rounding = {"up": ROUND_CEILING, "down": ROUND_FLOOR, "half_up": ROUND_HALF_UP}[tariff.quantity_rounding]
    return steps.to_integral_value(rounding=rounding) * increment


def price(report: dict, table: PricingTable):
    """Return (tariff or None, amount or None, calculation dict, unknown_reason)."""
    tariff = table.find(
        provider=report["provider"], service=report["service"], model=report["model"],
        region=report["region"], meter_id=report["meter_id"], usage_date=report["usage_date"],
    )
    if report["quantity"] is None:
        return tariff, None, None, report["unknown_reason"] or "USAGE_NOT_REPORTED"
    if tariff is None:
        return None, None, None, "NO_TARIFF_FOR_METER_DATE"
    if tariff.unit != report["unit"]:
        return tariff, None, None, "UNIT_MISMATCH"
    if tariff.unit_price is None:
        return tariff, None, None, tariff.unknown_reason
    quantity = dec(report["quantity"])
    cached = dec(report["cached_quantity"] or "0")
    billed = _round_quantity(quantity, tariff)
    if tariff.minimum_billed_quantity is not None:
        billed = max(billed, dec(tariff.minimum_billed_quantity))
    if cached and billed != quantity:
        return tariff, None, None, "CACHED_WITH_QUANTITY_ROUNDING_UNSUPPORTED"
    unit_price, rate_q = dec(tariff.unit_price), dec(tariff.rate_unit_quantity)
    with localcontext() as context:
        context.prec = 60
        if cached and tariff.cached_billing == "discounted" and tariff.cached_unit_price is not None:
            raw = (billed - cached) / rate_q * unit_price + cached / rate_q * dec(tariff.cached_unit_price)
        elif cached and tariff.cached_billing == "same_rate":
            raw = billed / rate_q * unit_price
        elif cached:
            return tariff, None, None, "CACHED_BILLING_NOT_DOCUMENTED"
        else:
            raw = billed / rate_q * unit_price
        amount = raw.quantize(Decimal(1).scaleb(-tariff.amount_scale), rounding=ROUNDING[tariff.amount_rounding])
    calculation = {
        "formula": "billed_quantity / rate_unit_quantity * unit_price (cached subset at cached_unit_price)",
        "reported_quantity": _text(quantity), "billed_quantity": _text(billed), "cached_quantity": _text(cached),
        "rate_unit_quantity": tariff.rate_unit_quantity, "unit_price": tariff.unit_price,
        "cached_unit_price": tariff.cached_unit_price, "unrounded_amount": _text(raw),
        "amount_scale": tariff.amount_scale, "amount_rounding": tariff.amount_rounding,
        "quantity_rounding": tariff.quantity_rounding, "quantity_increment": tariff.quantity_increment,
        "minimum_billed_quantity": tariff.minimum_billed_quantity,
    }
    return tariff, amount, calculation, None


# ---------------------------------------------------------------- derivation

def _reports(events):
    reports, expected, anomalies, seen = [], None, [], {}
    for event in events:
        if event["type"] == "cost.expectation_declared":
            expected = list(event["content"]["components"])
        if event["type"] != "cost.usage_reported":
            continue
        content = event["content"]
        key = (content["meter_id"], content["billing_scope_id"], content["revision"],
               "cumulative" if content["report_kind"] == "cumulative" else content["report_id"])
        if key in seen:
            first = seen[key]
            same = all(first["content"][k] == content[k] for k in ("quantity", "cached_quantity", "provenance"))
            anomalies.append({"event_id": event["event_id"], "duplicate_of": first["event_id"],
                              "kind": "duplicate_report" if same else "conflicting_duplicate_report"})
            continue
        seen[key] = {"event_id": event["event_id"], "content": content}
        reports.append(seen[key])
    return reports, expected, anomalies


def _line(run_id, item, table):
    report = item["content"]
    tariff, amount, calculation, reason = price(report, table)
    provenance = report["provenance"] if amount is not None else "unknown"
    return {
        "line_id": "cost_" + _digest([run_id, report["report_id"], report["meter_id"], report["billing_scope_id"],
                                       report["revision"], report["report_kind"]])[:24],
        "run_id": run_id, "component": report["component"], "provider": report["provider"],
        "service": report["service"], "model": report["model"], "region": report["region"],
        "generation_state": report["generation_state"], "billing_scope_id": report["billing_scope_id"],
        "scope_type": report["scope_type"], "meter_id": report["meter_id"],
        "billing_interval": {"start_ms": report["interval_start_ms"], "end_ms": report["interval_end_ms"]},
        "provenance": provenance, "usage_source": report["provenance"] if report["quantity"] is not None else None,
        "report_id": report["report_id"], "report_kind": report["report_kind"], "revision": report["revision"],
        "corrects_report_id": report["corrects_report_id"], "covers_scope_ids": report["covers_scope_ids"],
        "quantity": report["quantity"], "unit": report["unit"], "cached_quantity": report["cached_quantity"],
        "rate_unit_quantity": tariff.rate_unit_quantity if tariff else None,
        "unit_price": tariff.unit_price if tariff else None,
        "currency": tariff.currency if tariff and amount is not None else None,
        "amount": None if amount is None else _text(amount),
        "rounding": None if tariff is None else {
            "amount_scale": tariff.amount_scale, "amount_rounding": tariff.amount_rounding,
            "quantity_rounding": tariff.quantity_rounding, "quantity_increment": tariff.quantity_increment,
            "minimum_billed_quantity": tariff.minimum_billed_quantity},
        "tariff": None if tariff is None else {
            "tariff_id": tariff.tariff_id, "hash": table.hashes[tariff.tariff_id],
            "effective_from": tariff.effective_from, "effective_to": tariff.effective_to,
            "table_id": table.table_id, "table_version": table.version, "source_status": tariff.source_status,
            "evidence_ids": tariff.evidence_ids},
        "exclusivity_group": tariff.exclusivity_group if tariff else None,
        "exclusivity_priority": tariff.exclusivity_priority if tariff else None,
        "usage_event_ids": [item["event_id"], *report["evidence_event_ids"]],
        "invoice_reference": None, "allocation": None,
        "estimation_method": report["estimation_method"], "assumptions": report["assumptions"],
        "calculation": calculation, "unknown_reason": reason,
        "superseded_by": None, "superseded_reason": None,
    }


def _invoice_lines(run_id, allocations, lines):
    out, applied, unmatched = [], [], []
    for allocation in allocations:
        if allocation["run_id"] != run_id:
            continue
        matches = [x for x in lines if (x["meter_id"], x["billing_scope_id"]) == (
            allocation["meter_id"], allocation["billing_scope_id"])]
        if not matches:
            unmatched.append({**allocation, "reason": "NO_MATCHING_USAGE_METER_SCOPE"})
            continue
        first = matches[0]
        out.append({
            **{k: first[k] for k in ("run_id", "component", "provider", "service", "model", "region",
                                     "generation_state", "billing_scope_id", "scope_type", "meter_id", "unit",
                                     "exclusivity_group", "exclusivity_priority", "billing_interval")},
            "line_id": "cost_inv_" + _digest([allocation["record_id"], run_id, allocation["meter_id"],
                                              allocation["billing_scope_id"], allocation["record_revision"]])[:24],
            "provenance": "invoice_reconciled", "usage_source": None, "report_id": None, "report_kind": None,
            "revision": allocation["record_revision"], "corrects_report_id": None, "covers_scope_ids": [],
            "quantity": None, "cached_quantity": None, "rate_unit_quantity": None, "unit_price": None,
            "currency": allocation["currency"], "amount": allocation["amount"], "rounding": None, "tariff": None,
            "usage_event_ids": sorted({e for x in matches for e in x["usage_event_ids"]}),
            "invoice_reference": allocation["invoice_ref"],
            "allocation": {"record_id": allocation["record_id"], "method": allocation["method"],
                           "evidence_ids": allocation["evidence_ids"]},
            "estimation_method": None, "assumptions": [], "calculation": None, "unknown_reason": None,
            "superseded_by": None, "superseded_reason": None,
        })
        applied.append(allocation)
    return out, applied, unmatched


def _supersede(line, by, reason):
    if line["superseded_by"] is None and line is not by:
        line["superseded_by"], line["superseded_reason"] = by["line_id"], reason


def _active(lines):
    return [x for x in lines if x["superseded_by"] is None]


def _apply_supersession(lines):
    order = {x["line_id"]: i for i, x in enumerate(lines)}

    def newest(group):  # highest revision, then latest ledger position
        return max(group, key=lambda x: (x["revision"], order[x["line_id"]]))

    # A. same report id: later revision supersedes.
    groups = {}
    for x in _active(lines):
        if x["report_id"] is not None:
            groups.setdefault((x["meter_id"], x["billing_scope_id"], x["report_id"]), []).append(x)
    for group in groups.values():
        top = newest(group)
        for x in group:
            _supersede(x, top, "REVISED_BY_LATER_REPORT_REVISION")
    # B. explicit correction of another report id.
    for corrector in [x for x in _active(lines) if x["corrects_report_id"]]:
        for x in lines:
            if (x["meter_id"], x["billing_scope_id"], x["report_id"]) == (
                corrector["meter_id"], corrector["billing_scope_id"], corrector["corrects_report_id"]):
                _supersede(x, corrector, "CORRECTED_BY_SUPERSEDING_REPORT")
    # D. same meter/scope: highest provenance wins (reported over estimated; known over unknown).
    groups = {}
    for x in _active(lines):
        groups.setdefault((x["meter_id"], x["billing_scope_id"]), []).append(x)
    for group in groups.values():
        top = max(RANK[x["provenance"]] for x in group)
        winner = next(x for x in group if RANK[x["provenance"]] == top)
        for x in group:
            if RANK[x["provenance"]] < top:
                _supersede(x, winner, "SUPERSEDED_BY_HIGHER_PROVENANCE")
    # E. a report that declares it covers other scopes (e.g. session total over per-turn estimates).
    for cover in [x for x in _active(lines) if x["covers_scope_ids"] and RANK[x["provenance"]] > 0]:
        for x in _active(lines):
            if (x["meter_id"] == cover["meter_id"] and x["billing_scope_id"] in cover["covers_scope_ids"]
                    and RANK[x["provenance"]] <= RANK[cover["provenance"]]):
                _supersede(x, cover, "COVERED_BY_DECLARED_SCOPE")
    # C. cumulative reports: only the newest counts; incremental ones in that scope are not added to it.
    groups = {}
    for x in _active(lines):
        groups.setdefault((x["meter_id"], x["billing_scope_id"]), []).append(x)
    for group in groups.values():
        cumulative = [x for x in group if x["report_kind"] == "cumulative"]
        if cumulative:
            top = newest(cumulative)
            for x in group:
                _supersede(x, top, "CUMULATIVE_REVISION" if x in cumulative else "INCREMENTAL_COVERED_BY_CUMULATIVE")
    # F. exclusivity groups: one alternative per component/group/scope.
    groups = {}
    for x in _active(lines):
        if x["exclusivity_group"]:
            groups.setdefault((x["component"], x["exclusivity_group"], x["billing_scope_id"]), []).append(x)
    for group in groups.values():
        meters = {}
        for x in group:
            meters.setdefault(x["meter_id"], []).append(x)
        if len(meters) < 2:
            continue

        def preference(item):
            meter, members = item
            return (-max(RANK[x["provenance"]] for x in members), min(x["exclusivity_priority"] for x in members), meter)

        winner = min(meters.items(), key=preference)[1][0]
        for members in meters.values():
            for x in members:
                if x["meter_id"] != winner["meter_id"]:
                    _supersede(x, winner, "EXCLUSIVITY_GROUP_ALTERNATIVE")


def _component_label(line):
    state = line["generation_state"]
    return line["component"] if state == "completed" else f"{line['component']}_{state}_generation"


def summarize(run_id, lines, expected):
    active = _active(lines)
    known = [x for x in active if x["amount"] is not None]
    unknown = [x for x in active if x["amount"] is None]
    covered = {x["component"] for x in known}
    uncovered = sorted(set(expected or []) - {x["component"] for x in active})
    unknown_components = sorted({_component_label(x) for x in unknown} | set(uncovered))
    scopes_known = all(x["scope_type"] in KNOWN_SCOPES for x in active)
    if not known or not expected:
        completeness = "unknown"
    elif unknown or uncovered or not scopes_known or not set(expected) <= covered:
        completeness = "partial"
    else:
        completeness = "complete"
    provenances = sorted({x["provenance"] for x in known})
    if completeness == "unknown":
        label = "unknown"
    else:
        label = completeness + "_" + (provenances[0] if len(provenances) == 1 else "mixed_provenance")
    summaries = []
    for currency in sorted({x["currency"] for x in known}):
        mine = [x for x in known if x["currency"] == currency]
        scale = max([6] + [-dec(x["amount"]).as_tuple().exponent for x in mine])
        quantum = Decimal(1).scaleb(-scale)
        by = {p: sum((dec(x["amount"]) for x in mine if x["provenance"] == p), Decimal(0)).quantize(quantum)
              for p in PROVENANCES}
        subtotal = sum(by.values(), Decimal(0)).quantize(quantum)
        summaries.append({
            "run_id": run_id, "currency": currency, "known_subtotal": _text(subtotal),
            "by_provenance": {p: _text(v) for p, v in by.items()},
            "summary_label": label, "completeness": completeness,
            "total_cost": _text(subtotal) if completeness == "complete" else None,
            "unknown_line_count": len(unknown), "unknown_components": unknown_components,
            "line_ids": [x["line_id"] for x in mine] + [x["line_id"] for x in unknown],
        })
    return {
        "summaries": summaries, "completeness": completeness, "summary_label": label,
        "unknown_line_count": len(unknown), "unknown_components": unknown_components,
        "expected_components": sorted(expected or []), "uncovered_components": uncovered,
        "statement": "Amounts group non-superseded lines by currency and provenance. A known subtotal is "
        "not a total cost unless completeness is complete; zero in a provenance bucket means no recorded "
        "charge of that provenance, not free.",
    }


def derive(run_id, events, table: PricingTable, allocations=()):
    reports, expected, anomalies = _reports(events)
    lines = [_line(run_id, item, table) for item in reports]
    invoice, applied, unmatched = _invoice_lines(run_id, allocations, lines)
    lines = lines + invoice
    _apply_supersession(lines)
    return lines, summarize(run_id, lines, expected), {
        "applied": applied, "unmatched": unmatched, "anomalies": anomalies}


def build_section(run_id, events, allocations=(), table=None):
    """The cost section of an evidence bundle (hash-linked from the manifest)."""
    table = table or load_table()
    lines, summary, extra = derive(run_id, events, table, allocations)
    return {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "pricing": table.summary(),
        "pricing_document": table.document, "lines": lines, **summary,
        "reconciliation": {"allocations": list(allocations), "applied": extra["applied"],
                           "unmatched_allocations": extra["unmatched"]},
        "anomalies": extra["anomalies"],
        "estimated_vs_actual": estimated_vs_actual(lines),
    }


def estimated_vs_actual(lines):
    """Same-scope comparison of superseded estimates with the line that replaced them (disclosed, not added)."""
    by_id = {x["line_id"]: x for x in lines}
    out = []
    for x in lines:
        winner = by_id.get(x["superseded_by"]) if x["superseded_by"] else None
        if x["provenance"] == "estimated" and winner and winner["provenance"] in ("provider_reported_usage", "invoice_reconciled"):
            out.append({"meter_id": x["meter_id"], "billing_scope_id": x["billing_scope_id"],
                        "estimated_line_id": x["line_id"], "estimated_amount": x["amount"],
                        "actual_line_id": winner["line_id"], "actual_amount": winner["amount"],
                        "actual_provenance": winner["provenance"],
                        "same_scope": True, "unresolved_charges_disclosed": winner["amount"] is None})
    return out


def manifest_entry(section):
    """Compact manifest link to the cost section; ``section_hash`` is verified."""
    return {
        "section_hash": _digest(section), "pricing_table_id": section["pricing"]["table_id"],
        "pricing_table_version": section["pricing"]["version"],
        "pricing_document_hash": section["pricing"]["document_hash"], "line_count": len(section["lines"]),
        "completeness": section["completeness"], "summary_label": section["summary_label"],
        "unknown_line_count": section["unknown_line_count"],
        "unknown_components": section["unknown_components"],
    }


def verify_cost_section(bundle) -> bool:
    """Manifest link, pricing document hash, and deterministic re-derivation from the ledger."""
    manifest, section = bundle["manifest"], bundle.get("cost")
    entry = manifest.get("cost")
    if section is None:
        return entry is None or "source_manifest" in bundle  # derived views may omit the section
    if entry is None or entry.get("section_hash") != _digest(section) or section.get("run_id") != manifest["run_id"]:
        return False
    table = PricingTable(section["pricing_document"])
    if section["pricing"] != table.summary() or entry != manifest_entry(section):
        return False
    if any("tombstone" in e for e in bundle["events"]):
        return True  # purged payloads cannot be re-derived; the hash link above still binds the section
    rebuilt = build_section(manifest["run_id"], bundle["events"], section["reconciliation"]["allocations"], table)
    return canonical_json(rebuilt) == canonical_json(section)
