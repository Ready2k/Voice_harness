"""Review-facing routes: authenticated evidence download and unresolved holds (AP-07, HD-04)."""

import hashlib

from fastapi import Depends, HTTPException, Response

from .exports import REVIEWER_EXPORT_TYPE, render, reviewer_export

NOTICE_SECONDS = 7 * 86400


def unresolved_holds(database, evidence, after="", limit=100):
    """Unreleased holds across runs, keyset-paginated by hold_id."""
    with database.connection() as db:
        rows = db.execute(
            "SELECT hold_id, run_id FROM harness_preservation_holds "
            "WHERE released_at IS NULL AND hold_id>? ORDER BY hold_id LIMIT ?",
            (after, limit + 1),
        ).fetchall()
    now, items = evidence.clock(), []
    for row in rows[:limit]:
        state = database.get(row["run_id"])
        hold = state["holds"][row["hold_id"]]
        view = evidence._view(row["run_id"], state, hold)
        seconds = hold["expires_at"] - now
        items.append(
            {
                **{k: view[k] for k in view if k != "history"},
                "history": hold["history"],
                "status": "active" if seconds > 0 else "expired_unreleased",
                "seconds_to_expiry": seconds,
                "expiry_notice_due": seconds <= NOTICE_SECONDS,
                "expiry_notice_event_id": hold.get("expiry_notice_event_id"),
                "dispute": {
                    "reason": hold["reason"],
                    "finding_ids": hold["scope"]["finding_ids"],
                    "scope": hold["scope"]["type"],
                },
            }
        )
    return {
        "items": items,
        "next_cursor": items[-1]["hold_id"] if len(rows) > limit else None,
    }


def register_review_routes(app, database, evidence, auth):
    @app.get("/api/v1/preservation-holds", dependencies=[Depends(auth)])
    def holds(status: str = "unresolved", cursor: str = "", limit: int = 100):
        if status != "unresolved" or not 1 <= limit <= 1000:
            raise HTTPException(422, "INVALID_PAGINATION")
        return unresolved_holds(database, evidence, cursor, limit)

    @app.get("/api/v1/runs/{run_id}/preservation-holds", dependencies=[Depends(auth)])
    def run_holds(run_id: str):
        state = database.get(run_id)
        return {
            "items": [evidence._view(run_id, state, h) for h in state["holds"].values()]
        }

    @app.get("/api/v1/runs/{run_id}/evidence/download", dependencies=[Depends(auth)])
    def download(run_id: str, view: str = "standard", format: str = "json"):  # noqa: A002
        if view not in {"standard", "reviewer"} or format not in {"json", "ndjson"}:
            raise HTTPException(422, "INVALID_EXPORT_REQUEST")
        database.get(run_id)
        bundle = (
            reviewer_export(evidence, run_id) if view == "reviewer" else evidence.bundle(run_id)
        )
        body, media_type = render(bundle, format)
        suffix = "ndjson" if format == "ndjson" else "json"
        name = f"{run_id}-{'reviewer' if view == 'reviewer' else 'evidence'}.{suffix}"
        return Response(
            body,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "X-Content-SHA256": hashlib.sha256(body).hexdigest(),
                "X-Manifest-Hash": bundle["manifest_hash"],
                "X-Export-Type": REVIEWER_EXPORT_TYPE
                if view == "reviewer"
                else bundle["manifest"]["export_type"],
                "Cache-Control": "no-store",
            },
        )
