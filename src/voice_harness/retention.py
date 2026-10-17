"""Metadata retention (spec section 10, RT-02): tombstone expired ledger payloads.

The ledger stays hash-chained.  An expired event keeps its envelope identity
(`event_id`, `sequence`, `prev_hash`, `event_hash`, `type`, `source`, timestamps)
but its `content`, native provider payload and causation edges are replaced by a
`tombstone` record.  The retained `event_hash` is the hash of the *original*
envelope, so it can no longer be recomputed; `verify_bundle` therefore checks
chain linkage for tombstones and cross-checks the manifest's tombstone list.
Nothing here rewrites a payload while pretending its hash still covers it.
"""

import time
from calendar import timegm

from .engine import digest

METADATA_TTL_SECONDS = 30 * 86400
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
TOMBSTONE_REASON = "retention_expired"
_BLANK = {"content": {}, "provider_event": {}, "causation_event_ids": [], "trace_context": {}}


def _epoch(occurred_at):
    return timegm(time.strptime(occurred_at, "%Y-%m-%dT%H:%M:%SZ"))


def is_tombstone(payload):
    return isinstance(payload, dict) and "tombstone" in payload


def tombstone_of(payload, now, reason=TOMBSTONE_REASON):
    """Return the tombstoned form of an event payload (pure function)."""
    return {
        **payload,
        **_BLANK,
        "tombstone": {
            "reason": reason,
            "tombstoned_at": now,
            "retention_class": "metadata_30d",
            "content_sha256": digest(payload["content"]),
            "chain_fields_retained": ["event_hash", "prev_hash", "sequence", "event_id"],
        },
    }


def _cited(content):
    found = []
    if isinstance(content, dict):
        for key, value in content.items():
            if key in {"native_envelope", "provider_event"}:
                continue
            found.extend(value if key == "evidence_event_ids" else _cited(value))
    elif isinstance(content, list):
        for item in content:
            found.extend(_cited(item))
    return found


def _late_review_events(events, protected):
    """A hold's scope is fixed at activation; review claims/decisions appended afterwards for a
    protected review item (and the evidence they cite) stay readable too."""
    reviews = {e["content"]["review_id"] for e in events if e["event_id"] in protected
               and e["type"] == "review.opened"}
    extra = set()
    for e in events:
        if e["type"] in {"review.claimed", "review.decided"} and e["content"].get("review_id") in reviews:
            extra.add(e["event_id"])
            extra.update(_cited(e["content"]))
    return extra


def _protection(state, events, now, grace):
    """Return (all_events_protected, protected_event_ids) from hold scopes."""
    everything, ids, live_holds = False, set(), set()
    for hold in state["holds"].values():
        ended = hold["released_at"] if hold["released_at"] is not None else hold["expires_at"]
        live = (hold["released_at"] is None and hold["expires_at"] > now) or now < ended + grace
        if not live:
            continue
        live_holds.add(hold["hold_id"])
        ids.add(hold.get("audit_event_id"))
        if hold["scope"]["type"] == "run":
            everything = True
        else:
            ids.update(hold["scope"]["event_ids"])
    ids.discard(None)
    if not everything:
        ids.update(_late_review_events(events, ids))
    # Lifecycle events naming a still-protecting hold stay readable too.
    ids.update(e["event_id"] for e in events if e["content"].get("hold_id") in live_holds)
    return everything, ids


def purge_metadata(evidence, run_id, *, ttl=METADATA_TTL_SECONDS):
    """Tombstone expired, unheld event payloads of a terminal run; returns sequences."""
    now = evidence.clock()
    with evidence.database.transaction(run_id) as tx:
        if tx.state["state"] not in TERMINAL_STATES:
            return []
        rows = tx.db.execute(
            "SELECT sequence, payload FROM harness_events WHERE run_id=? ORDER BY sequence",
            (run_id,),
        ).fetchall()
        events = [evidence.database.decoded(r["payload"]) for r in rows]
        live = [e for e in events if not is_tombstone(e)]
        everything, protected = _protection(tx.state, live, now, evidence.GRACE_SECONDS)
        if everything:
            return []
        eligible = [
            e
            for e in live
            if e["event_id"] not in protected and _epoch(e["occurred_at"]) + ttl <= now
        ]
        if not eligible:
            return []
        if evidence.database.postgres:
            # Only this transaction may pass the append-only trigger, and only for
            # payload tombstoning (see storage.harness_reject_evidence_edit).
            tx.db.execute("SELECT set_config('harness.retention_purge', 'on', true)")
        for event in eligible:
            tx.db.execute(
                "UPDATE harness_events SET payload=? WHERE run_id=? AND sequence=?",
                (
                    evidence.database.encoded(tombstone_of(event, now)),
                    run_id,
                    event["sequence"],
                ),
            )
        sequences = [e["sequence"] for e in eligible]
        tx.event(
            "orchestrator",
            "retention.metadata_purged",
            {
                "reason": TOMBSTONE_REASON,
                "retention_days": ttl // 86400,
                "tombstoned_count": len(sequences),
                "sequences": sequences,
                "purged_at": now,
            },
        )
        return sequences


def tombstone_records(events):
    """Manifest listing derived from the ledger itself (ordered by sequence)."""
    return [
        {
            "sequence": e["sequence"],
            "event_id": e["event_id"],
            "event_hash": e["event_hash"],
            "tombstoned_at": e["tombstone"]["tombstoned_at"],
            "reason": e["tombstone"]["reason"],
        }
        for e in events
        if is_tombstone(e)
    ]


def tombstone_valid(payload):
    t = payload.get("tombstone")
    return (
        isinstance(t, dict)
        and t.get("reason") == TOMBSTONE_REASON
        and all(payload.get(k) == v for k, v in _BLANK.items())
        and isinstance(payload.get("event_hash"), str)
    )
