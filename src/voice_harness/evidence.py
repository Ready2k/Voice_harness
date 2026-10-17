"""Content-addressed artifacts, manifests and preservation-aware purge."""

import hashlib
import json
import os
import time
from pathlib import Path

from .canonical import canonical_json
from .coverage import FAMILIES, Coverage, build_registry, summarize
from .engine import digest, identifier
from .exports import embedded_schemas, schema_digests, versions
from .relational import upsert_hold
from .retention import is_tombstone, purge_metadata, tombstone_records, tombstone_valid


class Evidence:
    GRACE_SECONDS = 7 * 86400

    def __init__(self, database, root, clock=time.time, coverage=None, signer=None):
        self.database, self.root, self.clock = database, Path(root), clock
        # AC-08: optional manifest signer (signing.Signer); None keeps unsigned_local.
        self.signer = signer
        # AC-20: export-time snapshot of provider coverage (injectable for tests).
        self.coverage = coverage or (lambda: summarize(build_registry()))
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

    ACCESS_CLASSIFICATION = "restricted_review"
    DEFAULT_EXTENSION_SECONDS = 90 * 86400
    # Event-content keys that name another entity whose lifecycle the finding depends on.
    _GROUP_KEYS = (
        "call_id",
        "unit_id",
        "request_id",
        "confirmation_id",
        "token_id",
        "hold_id",
        "review_id",
    )

    @staticmethod
    def _events(tx, run_id):
        return [
            tx.database.decoded(row["payload"])
            for row in tx.db.execute(
                "SELECT payload FROM harness_events WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        ]

    @staticmethod
    def _references(content):
        """Event ids named by evidence/presentation arrays, excluding native payloads."""
        found = []
        if isinstance(content, dict):
            for key, value in content.items():
                if key in {"native_envelope", "provider_event"}:
                    continue
                if key in {"evidence_event_ids", "presentation_event_ids"}:
                    found.extend(value)
                else:
                    found.extend(Evidence._references(value))
        elif isinstance(content, list):
            for item in content:
                found.extend(Evidence._references(item))
        return found

    @staticmethod
    def _judge_finding_seeds(events):
        """Finding id -> closure seeds for Phase 3 judging events.

        Addressable ids: rubric (deterministic) and judge.offline_evaluated ``finding_id``s, online
        ``finding_id``s, and review ids. An evaluation-scoped finding pulls in the rubric, independence
        and adjudication events of its evaluation plus the review items for its criterion; review
        siblings (claimed/decided) join through the ``review_id`` group key in the closure walk.
        """
        by_eval = {}
        reviews = {}
        for event in events:
            if "tombstone" in event:
                continue
            content = event["content"]
            if event["type"] in {"rubric.evaluated", "judge.independence_recorded", "judge.adjudicated"}:
                by_eval.setdefault(content["evaluation_id"], []).append(event["event_id"])
            elif event["type"] == "review.opened":
                reviews.setdefault((content["evaluation_id"], content["criterion_id"]), []).append(event["event_id"])
        found = {}

        def evaluation_seeds(evaluation_id, criterion_id):
            return by_eval.get(evaluation_id, []) + reviews.get((evaluation_id, criterion_id), [])

        for event in events:
            if "tombstone" in event:
                continue
            content, kind = event["content"], event["type"]
            if kind in {"rubric.evaluated", "judge.offline_evaluated"}:
                for finding in content.get("findings", content.get("results", [])):
                    found[finding["finding_id"]] = [
                        event["event_id"], *finding["evidence_event_ids"],
                        *evaluation_seeds(content["evaluation_id"], finding["criterion_id"]),
                    ]
            elif kind == "judge.online_finding":
                found[content["finding_id"]] = [
                    event["event_id"], *content["evidence_event_ids"],
                    content["window_from_event_id"], content["window_to_event_id"],
                ]
            elif kind == "review.opened":
                found[content["review_id"]] = [
                    event["event_id"], *evaluation_seeds(content["evaluation_id"], content["criterion_id"]),
                ]
        return found

    def _scope(self, tx, run_id, finding_ids):
        """Resolve a hold's immutable effective scope and its dependency closure."""
        events = self._events(tx, run_id)
        by_id = {e["event_id"]: e for e in events}
        if finding_ids is None:
            # A run-wide hold covers every artifact, including ones stored later.
            return {
                "type": "run",
                "finding_ids": [],
                "event_ids": "all_run_events",
                "artifact_hashes": sorted(tx.state["artifacts"]),
                "fixture_snapshot_id": tx.state.get("fixture_snapshot_id"),
                "scenario_id": tx.state["config"].get("scenario_id"),
                "policy_profile": tx.state["config"].get("policy_profile"),
                "includes_manifest": True,
            }
        wanted, seeds = set(finding_ids), []
        known = set()
        for event in events:
            if event["type"] != "judge.completed":
                continue
            for finding in event["content"].get("findings", []):
                fid = event["event_id"] + ":" + finding["criterion_id"]
                known.add(fid)
                if fid in wanted:
                    seeds.append(event["event_id"])
                    seeds.extend(finding["evidence_event_ids"])
        judge_seeds = self._judge_finding_seeds(events)
        known.update(judge_seeds)
        for fid in wanted & set(judge_seeds):
            seeds.extend(judge_seeds[fid])
        if wanted - known:
            raise KeyError("FINDING_NOT_FOUND")
        # Run envelope events are always necessary to interpret any finding.
        seeds.extend(e["event_id"] for e in events if e["type"] in {"run.started", "session.opened"})
        closure = set()
        pending = list(seeds)
        while pending:
            event_id = pending.pop()
            if event_id in closure or event_id not in by_id:
                continue
            closure.add(event_id)
            event = by_id[event_id]
            pending.extend(event["causation_event_ids"])
            pending.extend(self._references(event["content"]))
            # Pull in sibling lifecycle events for the same call/unit/request.
            for key in self._GROUP_KEYS:
                value = event["content"].get(key)
                if value is None:
                    continue
                pending.extend(
                    other["event_id"]
                    for other in events
                    if other["content"].get(key) == value
                )
        hashes = set()
        for event_id in closure:
            content = by_id[event_id]["content"]
            for key in ("audio_sha256", "sha256"):
                if content.get(key) in tx.state["artifacts"]:
                    hashes.add(content[key])
        return {
            "type": "findings",
            "finding_ids": sorted(wanted),
            "event_ids": sorted(closure, key=lambda i: by_id[i]["sequence"]),
            "artifact_hashes": sorted(hashes),
            "fixture_snapshot_id": tx.state.get("fixture_snapshot_id"),
            "scenario_id": tx.state["config"].get("scenario_id"),
            "policy_profile": tx.state["config"].get("policy_profile"),
            "includes_manifest": True,
        }

    def _availability(self, run_id, state, hashes):
        return {
            sha: bool(
                state["artifacts"][sha]["available"]
                and (self.root / run_id / sha).exists()
            )
            for sha in hashes
        }

    def _view(self, run_id, state, hold):
        if hold["scope"]["type"] == "run":
            hold = {
                **hold,
                "scope": {**hold["scope"], "artifact_hashes": sorted(state["artifacts"])},
            }
        return {
            **{k: v for k, v in hold.items() if k not in {"idempotency_key", "body_hash"}},
            "run_id": run_id,
            "artifact_availability": self._availability(
                run_id, state, hold["scope"]["artifact_hashes"]
            ),
        }

    def hold(
        self,
        run_id,
        reason,
        owner_id,
        expires_at,
        idempotency_key,
        *,
        finding_ids=None,
        requester_id="operator",
    ):
        now = self.clock()
        expires_at = (
            now + self.DEFAULT_EXTENSION_SECONDS if expires_at is None else expires_at
        )
        if expires_at <= now:
            raise ValueError("INVALID_HOLD_EXPIRY")
        if finding_ids is not None and not finding_ids:
            raise ValueError("INVALID_HOLD_SCOPE")
        with self.database.transaction(run_id) as tx:
            body_hash = digest(
                {
                    "reason": reason,
                    "owner_id": owner_id,
                    "expires_at": expires_at,
                    "finding_ids": sorted(finding_ids) if finding_ids else None,
                }
            )
            for old in tx.state["holds"].values():
                if old["idempotency_key"] == idempotency_key:
                    if old["body_hash"] != body_hash:
                        raise ValueError("IDEMPOTENCY_CONFLICT")
                    return self._view(run_id, tx.state, old)
            hold_id = identifier("hold")
            hold = {
                "hold_id": hold_id,
                "reason": reason,
                "owner_id": owner_id,
                "requester_id": requester_id,
                "created_at": now,
                "expires_at": expires_at,
                "released_at": None,
                "release_reason": None,
                "access_classification": self.ACCESS_CLASSIFICATION,
                "scope": self._scope(tx, run_id, finding_ids),
                "history": [
                    {"action": "activated", "at": now, "by": requester_id,
                     "expires_at": expires_at}
                ],
                "idempotency_key": idempotency_key,
                "body_hash": body_hash,
            }
            hold["audit_event_id"] = tx.event(
                "orchestrator",
                "preservation.activated",
                {
                    k: v
                    for k, v in hold.items()
                    if k not in {"idempotency_key", "body_hash", "history"}
                },
            )
            tx.state["holds"][hold_id] = hold
            upsert_hold(tx.db, self.database, run_id, hold)
            return self._view(run_id, tx.state, hold)

    def release(self, run_id, hold_id, reason, *, by="operator"):
        with self.database.transaction(run_id) as tx:
            hold = tx.state["holds"][hold_id]
            if hold["released_at"] is None:
                hold["released_at"] = self.clock()
                hold["release_reason"] = reason
                hold["history"].append(
                    {"action": "released", "at": hold["released_at"], "by": by,
                     "reason": reason}
                )
                tx.event(
                    "orchestrator",
                    "preservation.released",
                    {"hold_id": hold_id, "reason": reason, "released_by": by},
                )
                upsert_hold(tx.db, self.database, run_id, hold)
            return self._view(run_id, tx.state, hold)

    def renew(self, run_id, hold_id, expires_at, reason, *, by="operator"):
        with self.database.transaction(run_id) as tx:
            hold = tx.state["holds"][hold_id]
            if (
                expires_at <= max(self.clock(), hold["expires_at"])
                or hold["released_at"] is not None
            ):
                raise ValueError("INVALID_HOLD_RENEWAL")
            old = hold["expires_at"]
            hold["expires_at"] = expires_at
            hold["expiry_notice_recorded"] = False
            hold["history"].append(
                {"action": "renewed", "at": self.clock(), "by": by,
                 "previous_expiry": old, "expires_at": expires_at, "reason": reason}
            )
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
            upsert_hold(tx.db, self.database, run_id, hold)
            return self._view(run_id, tx.state, hold)

    def purge(self, run_id):
        deleted = []
        with self.database.transaction(run_id) as tx:
            now = self.clock()
            holds = list(tx.state["holds"].values())
            for hold in holds:
                if (
                    hold["released_at"] is None
                    and not hold.get("expiry_notice_recorded")
                    and now >= hold["expires_at"] - 7 * 86400
                ):
                    hold["expiry_notice_recorded"] = True
                    hold["expiry_notice_event_id"] = tx.event(
                        "orchestrator",
                        "preservation.expiry_notice",
                        {
                            "hold_id": hold["hold_id"],
                            "owner_id": hold["owner_id"],
                            "expires_at": hold["expires_at"],
                            "channel": "authenticated_review_feed",
                        },
                    )

            def protected(sha):
                # Active holds, and the seven-day grace after release/expiry, shield
                # exactly the artifacts inside each hold's persisted scope.
                for hold in holds:
                    if (
                        hold["scope"]["type"] != "run"
                        and sha not in hold["scope"]["artifact_hashes"]
                    ):
                        continue
                    ended = (
                        hold["released_at"]
                        if hold["released_at"] is not None
                        else hold["expires_at"]
                    )
                    if (hold["released_at"] is None and hold["expires_at"] > now) or (
                        now < ended + self.GRACE_SECONDS
                    ):
                        return True
                return False

            for sha, artifact in tx.state["artifacts"].items():
                if (
                    artifact["available"]
                    and artifact["expires_at"] <= now
                    and not protected(sha)
                ):
                    # Tombstone is committed before filesystem garbage collection.
                    # A crash can leave an extra object, never a false available claim.
                    artifact["available"] = False
                    tx.event(
                        "orchestrator",
                        "artifact.purged",
                        {
                            "sha256": sha,
                            "reason": "retention_expired",
                            "tombstoned_at": now,
                        },
                    )
                    deleted.append(sha)
        for sha in deleted:
            (self.root / run_id / sha).unlink(missing_ok=True)
        return deleted

    def purge_metadata(self, run_id):
        """RT-02: tombstone ledger payloads past the 30-day metadata retention."""
        return purge_metadata(self, run_id)

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
                "holds": [
                    self._view(run_id, tx.state, h) for h in tx.state["holds"].values()
                ],
                "signature": None,
                "signature_status": "unsigned_local",
                "isolation_strategy": "transactional_run_namespace",
            }
            config = tx.state["config"]
            manifest.update(
                {
                    "export_type": "standard",
                    "scenario": {
                        "scenario_id": config.get("scenario_id"),
                        "version": config.get("scenario_version"),
                        "content_hash": next(
                            (
                                e["content"].get("scenario_hash")
                                for e in events
                                if e["type"] == "run.started"
                            ),
                            None,
                        ),
                    },
                    "provider": {
                        "adapter": "mock",
                        "provider_config_id": config.get("provider_config_id"),
                        "model_id": "scripted-fixture-v1",
                        "endpoint": None,
                        "region": None,
                        "sdk_version": None,
                        "version_resolution": "not_applicable_scripted_fixture",
                        "capability_report_id": "mock-fixture-v1",
                        "capability_status": "mock_only",
                    },
                    "policy": {
                        "profile": config.get("policy_profile"),
                        "version": "strict-v1",
                    },
                    "limits": tx.state.get("limits"),
                    "retention": {
                        "metadata_days_default": 30,
                        "synthetic_audio_days_default": 7,
                        "audio_opt_in": bool(config.get("retain_audio")),
                        "metadata_purge": "tombstoned_after_30d_unless_held",
                        "metadata_tombstones": tombstone_records(events),
                        "audio_purge": "tombstoned_after_ttl_unless_held",
                    },
                    "pricing": {
                        "table_version": None,
                        "reason": "NO_PROVIDER_BILLING_IN_MOCK",
                    },
                    "deterministic_check_and_judge_results": [
                        {
                            "event_id": e["event_id"],
                            "assessment_source": e["content"].get("assessment_source"),
                            "findings": e["content"].get("findings", []),
                        }
                        for e in events
                        if e["type"] == "judge.completed"
                    ],
                    "human_adjudication": [],
                    "final_disposition": tx.state["assurance"],
                    "omitted_from_standard_export": [
                        "challenge_answers",
                        "isolation_keys",
                        "provider_credentials",
                        "fixture_oracle",
                    ],
                    "known_limitations": [
                        "Scripted fixture provider: no live ASR, judge or provider behaviour",
                        "Audio is PCM silence with scripted transcripts; semantic safety not measured",
                        "Software playback boundary only; physical audibility unknown",
                        "Manifest is unsigned; tamper evidence is relative to an untrusted local root",
                    ],
                }
            )
            manifest["provider_coverage"] = json.loads(json.dumps(self.coverage()))
            manifest["schema_digests"] = schema_digests()
            manifest["versions"] = versions(config)
            manifest["source_hash"] = tx.state.get("source_hash")
            manifest["config_hash"] = tx.state["config_hash"]
            manifest["fixture_snapshot_id"] = tx.state.get("fixture_snapshot_id")
            from . import cost, cost_store

            cost_section = cost.build_section(
                run_id, events, cost_store.allocations_for_run(self.database, run_id, tx.db)
            )
            manifest["cost"] = cost.manifest_entry(cost_section)
            if cost_section["lines"]:
                manifest["pricing"] = {k: cost_section["pricing"][k] for k in ("table_id", "version", "document_hash")}
            cost_store.persist_lines(
                self.database, tx.db, run_id, manifest["cost"]["section_hash"], cost_section["lines"]
            )
            # Judging/human-review section: derived from the ledger and bound by hash into the manifest
            # before sealing, so the signature covers it. Must precede sign_manifest.
            from .judges import section as judging

            judging_section = judging.build_section(run_id, events)
            manifest["judging"] = judging.manifest_entry(judging_section)
            manifest["human_adjudication"] = judging_section["human_adjudication"]
            envelope = self.sign_manifest(manifest)
            return {
                "cost": cost_section,
                "judging": judging_section,
                "manifest": manifest,
                "manifest_hash": digest(manifest),
                "events": events,
                "artifacts": artifacts,
                "schemas": embedded_schemas(),
                "versions": manifest["versions"],
                **({"signature": envelope} if envelope else {}),
            }

    def sign_manifest(self, manifest):
        """Seal ``manifest`` in place (AC-08); returns the envelope or None if unsigned."""
        if self.signer is None:
            return None
        manifest["known_limitations"] = [
            x for x in manifest["known_limitations"] if not x.startswith("Manifest is unsigned")
        ]
        return self.signer.seal(manifest)

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


_COVERAGE_STATES = {
    "not_implemented",
    "implemented_but_unverified",
    "implemented_and_verified",
}
_COVERAGE_KEYS = {"items", "verified_count", "requested_count", "statement", "ranking"}


def coverage_section_valid(section):
    """AC-20 semantic check of an exported coverage snapshot.

    Strict: exact family set and order, closed key sets (no score/rank fields),
    verified only with a bound report and no reason codes, counts and statement
    derived from the items, ranking null. Bundles lacking the section fail.
    """
    if not isinstance(section, dict) or set(section) != _COVERAGE_KEYS:
        return False
    items = section["items"]
    if (
        section["ranking"] is not None
        or not isinstance(items, list)
        or [i.get("provider_family") if isinstance(i, dict) else None for i in items]
        != list(FAMILIES)
    ):
        return False
    fields = set(Coverage.__dataclass_fields__)
    verified = 0
    for item in items:
        if set(item) != fields or item["coverage_state"] not in _COVERAGE_STATES:
            return False
        reasons, state = item["reason_codes"], item["coverage_state"]
        if not isinstance(reasons, list) or not isinstance(item["evidence_ids"], list):
            return False
        if state == "implemented_and_verified":
            verified += 1
            if reasons or not item["capability_report_id"] or not item["checked_at"]:
                return False
        else:
            if not reasons or item["evidence_ids"]:
                return False
            if state == "not_implemented" and (
                item["model_id"] or item["api_surface"] or item["region"]
            ):
                return False
    return (
        section["verified_count"] == verified
        and section["requested_count"] == len(items)
        and section["statement"]
        == f"verified for {verified} of {len(items)} requested provider families"
    )


def _derivation_valid(bundle, manifest):
    """A derived view must name its source hash and keep ledger bytes verbatim."""
    source, derived = bundle["source_manifest"], manifest.get("derived_from")
    transform = manifest.get("transformation")
    return (
        isinstance(derived, dict)
        and isinstance(transform, dict)
        and derived["source_manifest_hash"] == digest(source)
        and derived["source_final_ledger_hash"] == source["final_ledger_hash"] == manifest["final_ledger_hash"]
        and derived["source_event_count"] == source["event_count"] == manifest["event_count"]
        and source["artifact_hashes"] == manifest["artifact_hashes"]
        and transform.get("events_modified") is False
        and transform.get("artifacts_modified") is False
    )


def verify_bundle(bundle, keyring=None, *, allow_unsigned=None, revoked=()):
    """Offline integrity check; with ``keyring`` (or ``allow_unsigned=False``) also
    enforces the AC-08 signature policy.  ``allow_unsigned=True`` accepts a bundle
    that honestly declares itself unsigned.  With neither argument only integrity
    is checked (legacy local use)."""
    if keyring is not None or allow_unsigned is not None:
        from .signing import SigningError, check_bundle_signature

        try:
            outcome = check_bundle_signature(
                bundle, keyring, allow_unsigned=bool(allow_unsigned), revoked=revoked
            )
        except (SigningError, AttributeError, TypeError):
            return False
        if outcome != "ok":
            return False
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
        if not coverage_section_valid(manifest.get("provider_coverage")):
            return False
        from .cost import verify_cost_section

        if not verify_cost_section(bundle):
            return False
        from .judges.section import verify_judging_section

        if not verify_judging_section(bundle):
            return False
        previous = None
        for sequence, payload in enumerate(events, 1):
            # Verify the exact historical keys; current dataclass defaults must
            # never change the hashed representation of an older envelope.
            if is_tombstone(payload):
                # Retained hash cannot be recomputed from a tombstoned payload;
                # chain linkage and the manifest's tombstone list still bind it.
                expected = payload["event_hash"]
                if not tombstone_valid(payload):
                    return False
            else:
                unsigned = {**payload, "event_hash": None}
                expected = hashlib.sha256(canonical_json(unsigned)).hexdigest()
            if (
                payload["run_id"] != manifest["run_id"]
                or payload["sequence"] != sequence
                or payload["prev_hash"] != previous
                or expected != payload["event_hash"]
            ):
                return False
            previous = payload["event_hash"]
        listed = manifest.get("retention", {}).get("metadata_tombstones")
        if listed is not None and listed != tombstone_records(events):
            return False
        if "schemas" in bundle and (
            {n: digest(s) for n, s in bundle["schemas"].items()}
            != manifest.get("schema_digests")
        ):
            return False
        if "source_manifest" in bundle and not _derivation_valid(bundle, manifest):
            return False
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
