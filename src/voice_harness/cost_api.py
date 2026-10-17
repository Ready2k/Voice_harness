"""Cost routes (spec section 11/13): per-run cost statement and invoice reconciliation records."""

from fastapi import Depends, HTTPException
from pydantic import ValidationError

from . import cost, cost_store


def register_cost_routes(app, database, auth):
    @app.get("/api/v1/runs/{run_id}/cost", dependencies=[Depends(auth)])
    def run_cost(run_id: str):
        database.get(run_id)  # 404 via the shared KeyError handler
        events = database.events(run_id)
        section = cost.build_section(run_id, events, cost_store.allocations_for_run(database, run_id))
        return {**section, "manifest_link": cost.manifest_entry(section)}

    @app.get("/api/v1/cost/reconciliations", dependencies=[Depends(auth)])
    def reconciliations(status: str = "all"):
        items = cost_store.records(database)
        if status != "all":
            if status not in {"unallocated", "partially_allocated", "allocated"}:
                raise HTTPException(422, "INVALID_STATUS")
            items = [x for x in items if x["status"] == status]
        return {"items": items}

    @app.post("/api/v1/cost/reconciliations", status_code=201, dependencies=[Depends(auth)])
    def put_reconciliation(body: dict):
        try:
            return cost_store.put_record(database, body)
        except ValidationError as error:
            raise HTTPException(422, "INVALID_RECONCILIATION_RECORD: " + str(error.error_count()) + " errors") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
