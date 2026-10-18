"""Comparison persistence (migration 13) and routes for AC-11 reports."""

import time

from fastapi import Depends, HTTPException

from .comparison import STATEMENT, ComparisonRequest, build_report, verify_export
from .engine import identifier

MIGRATION_ID = 13


class ComparisonStore:
    def __init__(self, database):
        self.database = database
        payload_type = "JSONB" if database.postgres else "TEXT"
        with database.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS harness_comparisons (comparison_id TEXT PRIMARY KEY, "
                f"created_at DOUBLE PRECISION NOT NULL, report_hash TEXT NOT NULL, payload {payload_type} NOT NULL)"
            )
            db.execute("INSERT INTO harness_migrations VALUES (?) ON CONFLICT DO NOTHING", (MIGRATION_ID,))

    def create(self, request: ComparisonRequest, clock=time.time):
        bound, unknown = 0, []
        for observation in request.observations:
            if observation.run_id is None:
                continue
            try:
                self.database.get(observation.run_id)
                bound += 1
            except KeyError:
                unknown.append(observation.run_id)
        if unknown:
            raise ValueError("UNKNOWN_RUN_ID:" + ",".join(sorted(set(unknown))[:5]))
        report = build_report(request)
        comparison_id = identifier("cmp")
        payload = {
            "comparison_id": comparison_id,
            "request": request.model_dump(mode="json"),
            "report": report,
            # Run IDs prove an observation names a stored run, not that its values were extracted from it.
            "provenance": {"observations": len(request.observations), "run_id_resolved": bound,
                           "unbound_diagnostic": len(request.observations) - bound},
        }
        with self.database.connection() as db:
            db.execute(
                "INSERT INTO harness_comparisons VALUES (?, ?, ?, ?)",
                (comparison_id, clock(), report["report_hash"], self.database.encoded(payload)),
            )
        return payload

    def get(self, comparison_id):
        with self.database.connection() as db:
            row = db.execute(
                "SELECT payload FROM harness_comparisons WHERE comparison_id=?", (comparison_id,)
            ).fetchone()
        if row is None:
            raise KeyError("COMPARISON_NOT_FOUND")
        return self.database.decoded(row["payload"])

    def list(self):
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT comparison_id, created_at, report_hash FROM harness_comparisons "
                "ORDER BY created_at, comparison_id"
            ).fetchall()
        return [{k: r[k] for k in ("comparison_id", "created_at", "report_hash")} for r in rows]


def export_payload(stored):
    return {
        "export_type": "provider_comparison",
        "comparison_id": stored["comparison_id"],
        "statement": STATEMENT,
        "request": stored["request"],
        "report": stored["report"],
        "report_hash": stored["report"]["report_hash"],
    }


def verify_stored_export(export):
    return verify_export(export, ComparisonRequest.model_validate(export["request"]))


def register_comparison_routes(app, database, auth):
    store = ComparisonStore(database)

    @app.post("/api/v1/comparisons", status_code=201, dependencies=[Depends(auth)])
    def create_comparison(body: ComparisonRequest):
        try:
            stored = store.create(body)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {"comparison_id": stored["comparison_id"], "report": stored["report"],
                "provenance": stored["provenance"]}

    @app.get("/api/v1/comparisons", dependencies=[Depends(auth)])
    def list_comparisons():
        return {"items": store.list()}

    @app.get("/api/v1/comparisons/{comparison_id}", dependencies=[Depends(auth)])
    def get_comparison(comparison_id: str):
        try:
            stored = store.get(comparison_id)
        except KeyError:
            raise HTTPException(404, "COMPARISON_NOT_FOUND") from None
        return {"comparison_id": comparison_id, "report": stored["report"]}

    @app.get("/api/v1/comparisons/{comparison_id}/export", dependencies=[Depends(auth)])
    def export_comparison(comparison_id: str):
        try:
            return export_payload(store.get(comparison_id))
        except KeyError:
            raise HTTPException(404, "COMPARISON_NOT_FOUND") from None
