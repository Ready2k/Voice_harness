"""Content-addressed artifacts, manifests and preservation-aware purge."""

import hashlib
import json
import os
import time
from pathlib import Path

from .engine import digest, identifier


class Evidence:
    GRACE_SECONDS = 7 * 86400

    def __init__(self, database, root, clock=time.time):
        self.database, self.root, self.clock = database, Path(root), clock
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, run_id, data: bytes, kind="audio", ttl=7 * 86400):
        sha = hashlib.sha256(data).hexdigest()
        directory = self.root / run_id
        directory.mkdir(exist_ok=True)
        path = directory / sha
        # File publication precedes ledger reference. Failure leaves an unreferenced
        # object, never a manifest claiming a missing object was written.
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        with self.database.transaction(run_id) as tx:
            tx.state["artifacts"][sha] = {
                "sha256": sha,
                "kind": kind,
                "size": len(data),
                "available": True,
                "expires_at": self.clock() + ttl,
            }
            tx.event(
                "orchestrator",
                "artifact.stored",
                {"sha256": sha, "kind": kind, "size": len(data)},
            )
        return sha

    def hold(self, run_id, reason, owner_id, expires_at, idempotency_key):
        if expires_at <= self.clock():
            raise ValueError("INVALID_HOLD_EXPIRY")
        with self.database.transaction(run_id) as tx:
            body_hash = digest(
                {"reason": reason, "owner_id": owner_id, "expires_at": expires_at}
            )
            for old in tx.state["holds"].values():
                if old["idempotency_key"] == idempotency_key:
                    if old["body_hash"] != body_hash:
                        raise ValueError("IDEMPOTENCY_CONFLICT")
                    return old
            hold_id = identifier("hold")
            hold = {
                "hold_id": hold_id,
                "reason": reason,
                "owner_id": owner_id,
                "expires_at": expires_at,
                "released_at": None,
                "idempotency_key": idempotency_key,
                "body_hash": body_hash,
            }
            tx.state["holds"][hold_id] = hold
            tx.event(
                "orchestrator",
                "preservation.activated",
                {k: v for k, v in hold.items() if k != "idempotency_key"},
            )
            return hold

    def release(self, run_id, hold_id, reason):
        with self.database.transaction(run_id) as tx:
            hold = tx.state["holds"][hold_id]
            if hold["released_at"] is None:
                hold["released_at"] = self.clock()
                tx.event(
                    "orchestrator",
                    "preservation.released",
                    {"hold_id": hold_id, "reason": reason},
                )

    def renew(self, run_id, hold_id, expires_at, reason):
        with self.database.transaction(run_id) as tx:
            hold = tx.state["holds"][hold_id]
            if (
                expires_at <= max(self.clock(), hold["expires_at"])
                or hold["released_at"] is not None
            ):
                raise ValueError("INVALID_HOLD_RENEWAL")
            old = hold["expires_at"]
            hold["expires_at"] = expires_at
            tx.event(
                "orchestrator",
                "preservation.renewed",
                {
                    "hold_id": hold_id,
                    "previous_expiry": old,
                    "expires_at": expires_at,
                    "reason": reason,
                },
            )
            return hold

    def purge(self, run_id):
        deleted = []
        with self.database.transaction(run_id) as tx:
            now = self.clock()
            for hold in tx.state["holds"].values():
                if (
                    hold["released_at"] is None
                    and not hold.get("expiry_notice_recorded")
                    and now >= hold["expires_at"] - 7 * 86400
                ):
                    hold["expiry_notice_recorded"] = True
                    tx.event(
                        "orchestrator",
                        "preservation.expiry_notice",
                        {
                            "hold_id": hold["hold_id"],
                            "owner_id": hold["owner_id"],
                            "expires_at": hold["expires_at"],
                            "channel": "authenticated_review_feed",
                        },
                    )
            holds = tx.state["holds"].values()
            if any(h["released_at"] is None and h["expires_at"] > now for h in holds):
                return deleted
            grace = max(
                (
                    h["released_at"]
                    if h["released_at"] is not None
                    else h["expires_at"]
                    for h in holds
                ),
                default=0,
            )
            if grace and now < grace + self.GRACE_SECONDS:
                return deleted
            for sha, artifact in tx.state["artifacts"].items():
                if artifact["available"] and artifact["expires_at"] <= now:
                    # Tombstone is committed before filesystem garbage collection.
                    # A crash can leave an extra object, never a false available claim.
                    artifact["available"] = False
                    tx.event(
                        "orchestrator",
                        "artifact.purged",
                        {"sha256": sha, "reason": "retention_expired"},
                    )
                    deleted.append(sha)
        for sha in deleted:
            (self.root / run_id / sha).unlink(missing_ok=True)
        return deleted

    def bundle(self, run_id):
        # Read root/state/events under one locked transaction to prevent mixed versions.
        with self.database.transaction(run_id) as tx:
            rows = tx.db.execute(
                "SELECT payload FROM harness_events WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
            events = [self.database.decoded(row["payload"]) for row in rows]
            artifacts = []
            for sha, item in tx.state["artifacts"].items():
                path = self.root / run_id / sha
                available = item["available"] and path.exists()
                artifacts.append(
                    {
                        **item,
                        "available": available,
                        "data_hex": path.read_bytes().hex() if available else None,
                    }
                )
            manifest = {
                "schema_version": "0.3",
                "run_id": run_id,
                "state": tx.state["state"],
                "event_count": len(events),
                "final_ledger_hash": events[-1]["event_hash"] if events else None,
                "artifact_hashes": [a["sha256"] for a in artifacts],
                "missing_artifacts": [
                    a["sha256"] for a in artifacts if not a["available"]
                ],
                "audio_capture": "retained" if artifacts else "not_captured",
                "holds": list(tx.state["holds"].values()),
                "signature": None,
                "signature_status": "unsigned_local",
                "isolation_strategy": "transactional_run_namespace",
            }
            manifest["source_hash"] = tx.state.get("source_hash")
            manifest["config_hash"] = tx.state["config_hash"]
            manifest["fixture_snapshot_id"] = tx.state.get("fixture_snapshot_id")
            return {
                "manifest": manifest,
                "manifest_hash": digest(manifest),
                "events": events,
                "artifacts": artifacts,
            }

    def save_manifest(self, run_id):
        bundle = self.bundle(run_id)
        directory = self.root / run_id
        directory.mkdir(exist_ok=True)
        path = directory / ("manifest-" + bundle["manifest_hash"] + ".json")
        if not path.exists():
            with path.open("x") as stream:
                json.dump(
                    {
                        "manifest": bundle["manifest"],
                        "manifest_hash": bundle["manifest_hash"],
                    },
                    stream,
                    sort_keys=True,
                )
                stream.flush()
                os.fsync(stream.fileno())
        with self.database.connection() as db:
            manifest = bundle["manifest"]
            db.execute(
                "INSERT INTO harness_evidence_manifests VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (
                    bundle["manifest_hash"],
                    run_id,
                    manifest["state"],
                    manifest["final_ledger_hash"],
                    manifest["event_count"],
                    self.database.encoded(manifest),
                ),
            )
        return path


def verify_bundle(bundle):
    try:
        manifest, events, artifacts = (
            bundle["manifest"],
            bundle["events"],
            bundle["artifacts"],
        )
        if (
            digest(manifest) != bundle["manifest_hash"]
            or len(events) != manifest["event_count"]
        ):
            return False
        previous = None
        for sequence, payload in enumerate(events, 1):
            # Verify the exact historical keys; current dataclass defaults must
            # never change the hashed representation of an older envelope.
            unsigned = {**payload, "event_hash": None}
            expected = hashlib.sha256(
                json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if (
                payload["run_id"] != manifest["run_id"]
                or payload["sequence"] != sequence
                or payload["prev_hash"] != previous
                or expected != payload["event_hash"]
            ):
                return False
            previous = payload["event_hash"]
        if (
            previous != manifest["final_ledger_hash"]
            or [a["sha256"] for a in artifacts] != manifest["artifact_hashes"]
        ):
            return False
        for artifact in artifacts:
            if artifact["available"]:
                data = bytes.fromhex(artifact["data_hex"])
                if (
                    hashlib.sha256(data).hexdigest() != artifact["sha256"]
                    or len(data) != artifact["size"]
                ):
                    return False
            elif artifact["sha256"] not in manifest["missing_artifacts"]:
                return False
        return True
    except (KeyError, ValueError, TypeError):
        return False
