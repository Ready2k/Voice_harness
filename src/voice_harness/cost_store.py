"""Cost persistence: migration 11 (SQLite and PostgreSQL), invoice reconciliation, line snapshots.

* ``harness_cost_reconciliations``: append-only account-period invoice records,
  one row per (record_id, revision). A record with no allocations is an
  unallocated account-period reconciliation record; allocation to a run needs a
  method and evidence ids and arrives as a new revision.
* ``harness_cost_lines``: immutable snapshot of the derived lines of each exported
  cost section (keyed by section hash), the spec's ``cost_lines`` projection.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Annotated

from pydantic import Field, model_validator

from .contracts import Closed, Identifier
from .pricing import DecimalText, dec

DateText = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
MIGRATION = 11


def migrate(db, payload_type):
    for statement in [
        f"CREATE TABLE IF NOT EXISTS harness_cost_reconciliations (record_id TEXT NOT NULL, revision INTEGER NOT NULL, provider TEXT NOT NULL, account_id TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL, invoice_ref TEXT NOT NULL, currency TEXT NOT NULL, amount TEXT NOT NULL, allocations {payload_type} NOT NULL, created_at DOUBLE PRECISION NOT NULL, PRIMARY KEY(record_id, revision))",
        f"CREATE TABLE IF NOT EXISTS harness_cost_lines (run_id TEXT NOT NULL REFERENCES harness_runs(run_id), section_hash TEXT NOT NULL, line_id TEXT NOT NULL, provenance TEXT NOT NULL, currency TEXT, amount TEXT, unknown_reason TEXT, superseded_by TEXT, definition {payload_type} NOT NULL, created_at DOUBLE PRECISION NOT NULL, PRIMARY KEY(run_id, section_hash, line_id))",
        "CREATE INDEX IF NOT EXISTS harness_cost_lines_by_run ON harness_cost_lines(run_id, provenance)",
    ]:
        db.execute(statement)
    db.execute(f"INSERT INTO harness_migrations VALUES ({MIGRATION}) ON CONFLICT DO NOTHING")


class Allocation(Closed):
    run_id: Identifier
    meter_id: Identifier
    billing_scope_id: Identifier
    amount: DecimalText
    method: Identifier
    evidence_ids: Annotated[list[Identifier], Field(min_length=1)]


class ReconciliationRecord(Closed):
    record_id: Identifier
    provider: Identifier
    account_id: Identifier
    period_start: DateText
    period_end: DateText
    invoice_ref: Identifier
    currency: Annotated[str, Field(pattern="^[A-Z]{3}$")]
    amount: DecimalText
    allocations: list[Allocation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _within_invoice(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end precedes period_start")
        if sum((dec(a.amount) for a in self.allocations), Decimal(0)) > dec(self.amount):
            raise ValueError("allocations exceed the invoiced amount")
        keys = [(a.run_id, a.meter_id, a.billing_scope_id) for a in self.allocations]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate allocation target")
        return self


_FIXED = ("provider", "account_id", "period_start", "period_end", "invoice_ref", "currency", "amount")


def _row(database, row):
    return {
        "record_id": row["record_id"], "revision": row["revision"], "provider": row["provider"],
        "account_id": row["account_id"], "period_start": row["period_start"], "period_end": row["period_end"],
        "invoice_ref": row["invoice_ref"], "currency": row["currency"], "amount": row["amount"],
        "allocations": database.decoded(row["allocations"]), "created_at": row["created_at"],
    }


def put_record(database, record: dict):
    """Append a record, or a new revision (allocations only; invoice facts are immutable)."""
    model = ReconciliationRecord.model_validate(record)
    for allocation in model.allocations:
        database.get(allocation.run_id)  # KeyError RUN_NOT_FOUND: cannot attribute to a missing run
    with database.connection() as db:
        previous = db.execute(
            "SELECT * FROM harness_cost_reconciliations WHERE record_id=? ORDER BY revision DESC LIMIT 1",
            (model.record_id,),
        ).fetchone()
        revision = 1
        if previous is not None:
            if any(previous[k] != getattr(model, k) for k in _FIXED):
                raise ValueError("RECONCILIATION_FACTS_IMMUTABLE")
            revision = previous["revision"] + 1
        db.execute(
            "INSERT INTO harness_cost_reconciliations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (model.record_id, revision, model.provider, model.account_id, model.period_start, model.period_end,
             model.invoice_ref, model.currency, model.amount,
             database.encoded([a.model_dump() for a in model.allocations]), time.time()),
        )
        row = db.execute(
            "SELECT * FROM harness_cost_reconciliations WHERE record_id=? AND revision=?", (model.record_id, revision)
        ).fetchone()
    return view(_row(database, row))


def view(row):
    allocated = sum((dec(a["amount"]) for a in row["allocations"]), Decimal(0))
    remaining = dec(row["amount"]) - allocated
    status = "unallocated" if not row["allocations"] else ("allocated" if remaining == 0 else "partially_allocated")
    return {**row, "allocated_amount": format(allocated, "f"), "unallocated_amount": format(remaining, "f"),
            "status": status}


def records(database, db=None):
    """Latest revision of every record."""
    def read(conn):
        rows = conn.execute(
            "SELECT r.* FROM harness_cost_reconciliations r JOIN (SELECT record_id, MAX(revision) AS revision "
            "FROM harness_cost_reconciliations GROUP BY record_id) m "
            "ON r.record_id=m.record_id AND r.revision=m.revision ORDER BY r.record_id"
        ).fetchall()
        return [view(_row(database, r)) for r in rows]

    if db is not None:
        return read(db)
    with database.connection() as conn:
        return read(conn)


def allocations_for_run(database, run_id, db=None):
    """Flat allocation inputs for one run from latest record revisions (no other run's data)."""
    out = []
    for record in records(database, db):
        for a in record["allocations"]:
            if a["run_id"] == run_id:
                out.append({**a, "record_id": record["record_id"], "record_revision": record["revision"],
                            "invoice_ref": record["invoice_ref"], "currency": record["currency"]})
    return out


def persist_lines(database, db, run_id, section_hash, lines):
    now = time.time()
    for line in lines:
        db.execute(
            "INSERT INTO harness_cost_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (run_id, section_hash, line["line_id"], line["provenance"], line["currency"], line["amount"],
             line["unknown_reason"], line["superseded_by"], database.encoded(line), now),
        )
