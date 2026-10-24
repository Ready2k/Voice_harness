"""Extract scoped, optional spoken facts from successful trusted banking tool results."""

from datetime import date


def allowed_tool_claims(result, call_id, now_ms):
    amounts, dates = [], []
    if result.get("status") != "success":
        return amounts, dates
    content = result.get("structured_content") or {}
    rows = [content, *content.get("transactions", [])]
    for row in rows:
        source = {"account_id": row.get("account_id"), "source_call_id": call_id, "source_ts_ms": now_ms}
        for field, value in row.items():
            if field.endswith("_minor") and isinstance(value, int) and not isinstance(value, bool):
                amounts.append({**source, "field": field, "amount_minor": value,
                                "currency": row.get("currency", "GBP")})
            if field in {"booked_at", "created_at", "scheduled_at"} and isinstance(value, str):
                try:
                    day = date.fromisoformat(value[:10]).isoformat()
                except ValueError:
                    continue
                dates.append({**source, "field": field, "date": day})
    return amounts, dates
