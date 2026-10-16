"""Transactional fixture execution for the credential-free Phase 1 runner.

SQLite is the embedded development backend; production PostgreSQL integration
remains a release gate. All identifiers below come from trusted orchestration.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class ExecutionStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    run_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    account_id TEXT NOT NULL, balance_minor INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS calls (
                    run_id TEXT NOT NULL, call_id TEXT NOT NULL, request TEXT NOT NULL,
                    state TEXT NOT NULL, result TEXT,
                    PRIMARY KEY (run_id, call_id));
                CREATE TABLE IF NOT EXISTS audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    call_id TEXT NOT NULL, state TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_run(
        self,
        run_id: str,
        owner_id="customer_001",
        account_id="acct_1001",
        balance_minor=123456,
    ):
        with self.connect() as db:
            db.execute(
                "INSERT INTO accounts VALUES (?, ?, ?, ?)",
                (run_id, owner_id, account_id, balance_minor),
            )

    def request(self, run_id: str, call_id: str, tool: str, args: dict) -> str:
        if not run_id or not call_id:
            raise ValueError("INVALID_CONTEXT")
        request = json.dumps(
            {"tool": tool, "args": args},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                db.execute(
                    "SELECT 1 FROM accounts WHERE run_id=?", (run_id,)
                ).fetchone()
                is None
            ):
                raise ValueError("UNKNOWN_RUN")
            existing = db.execute(
                "SELECT * FROM calls WHERE run_id=? AND call_id=?", (run_id, call_id)
            ).fetchone()
            if existing:
                if existing["request"] != request:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return existing["state"]
            db.execute(
                "INSERT INTO calls VALUES (?, ?, ?, 'requested', NULL)",
                (run_id, call_id, request),
            )
            self._audit(db, run_id, call_id, "requested")
            return "requested"

    @staticmethod
    def _audit(db, run_id, call_id, state):
        db.execute(
            "INSERT INTO audit(run_id, call_id, state) VALUES (?, ?, ?)",
            (run_id, call_id, state),
        )

    def cancel(self, run_id: str, call_id: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            call = self._call(db, run_id, call_id)
            if call["state"] == "requested":
                result = self._result(
                    call_id, "cancelled", "CANCELLED_BEFORE_EXECUTION"
                )
                self._settle(db, run_id, call_id, "cancelled_before_execution", result)
                return result
            return json.loads(call["result"])

    @staticmethod
    def _call(db, run_id, call_id):
        call = db.execute(
            "SELECT * FROM calls WHERE run_id=? AND call_id=?", (run_id, call_id)
        ).fetchone()
        if call is None:
            raise ValueError("UNKNOWN_CALL")
        return call

    @staticmethod
    def _result(call_id, status, error_code=None, content=None):
        return {
            "call_id": call_id,
            "status": status,
            "error_code": error_code,
            "structured_content": content,
        }

    def _settle(self, db, run_id, call_id, state, result):
        db.execute(
            "UPDATE calls SET state=?, result=? WHERE run_id=? AND call_id=?",
            (state, json.dumps(result, sort_keys=True), run_id, call_id),
        )
        self._audit(db, run_id, call_id, state)

    def execute(
        self, run_id: str, call_id: str, *, owner_id: str, verified: bool
    ) -> dict:
        """Revalidate and atomically commit fixture state, outcome and audit.

        fixture_adjust_balance is an operator-only test primitive. It does not
        represent a payment tool or implement customer confirmation.
        """
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            call = self._call(db, run_id, call_id)
            if call["result"] is not None:
                return json.loads(call["result"])
            request = json.loads(call["request"])
            tool, args = request["tool"], request["args"]
            account = db.execute(
                "SELECT * FROM accounts WHERE run_id=?", (run_id,)
            ).fetchone()
            expected = (
                {"account_id"}
                if tool == "get_account_summary"
                else {"account_id", "delta_minor"}
            )
            error = None
            if tool not in {"get_account_summary", "fixture_adjust_balance"}:
                error = "TOOL_NOT_ALLOWED"
            elif (
                not isinstance(args, dict)
                or set(args) != expected
                or not isinstance(args.get("account_id"), str)
                or tool == "fixture_adjust_balance"
                and (
                    type(args["delta_minor"]) is not int
                    or abs(args["delta_minor"]) > 10000
                )
            ):
                error = "INVALID_ARGUMENTS"
            elif (
                owner_id != account["owner_id"]
                or args["account_id"] != account["account_id"]
            ):
                error = "OWNERSHIP_MISMATCH"
            elif not verified:
                error = "IDENTITY_REQUIRED"
            if error:
                result = self._result(call_id, "denied", error)
                self._settle(db, run_id, call_id, "denied", result)
                return result
            self._audit(db, run_id, call_id, "authorized")
            self._audit(db, run_id, call_id, "executing")
            if tool == "fixture_adjust_balance":
                db.execute(
                    "UPDATE accounts SET balance_minor=balance_minor+? WHERE run_id=?",
                    (args["delta_minor"], run_id),
                )
            balance = db.execute(
                "SELECT balance_minor FROM accounts WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            result = self._result(
                call_id,
                "success",
                content={
                    "account_id": account["account_id"],
                    "balance_minor": balance,
                    "currency": "GBP",
                },
            )
            self._settle(db, run_id, call_id, "completed", result)
            return result

    def reconcile(self, run_id: str, call_id: str) -> dict:
        with self.connect() as db:
            call = self._call(db, run_id, call_id)
            return {
                "state": call["state"],
                "result": json.loads(call["result"]) if call["result"] else None,
            }

    def audit(self, run_id: str) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM audit WHERE run_id=? ORDER BY sequence", (run_id,)
                )
            ]


class MockResultReceiver:
    """Delivery may repeat; logical result identity cannot change."""

    def __init__(self):
        self.results: dict[str, dict] = {}

    def deliver(self, result: dict) -> str:
        # Serialize to detach provider context from mutable caller dictionaries.
        detached = json.loads(json.dumps(result, allow_nan=False))
        call_id = detached["call_id"]
        if call_id in self.results and self.results[call_id] != detached:
            raise ValueError("RESULT_IDENTITY_CONFLICT")
        self.results[call_id] = detached
        return "acknowledged"
