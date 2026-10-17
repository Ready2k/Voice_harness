"""Judge/review results in the evidence manifest, holds on judge findings, retention (AC-10, spec 7 and 10)."""

import copy
import time
import unittest

from fastapi.testclient import TestClient
from test_judges import TESTED, Base, cfg, reference_judge

from voice_harness.api import create_app
from voice_harness.engine import digest
from voice_harness.evidence import Evidence, verify_bundle
from voice_harness.exports import from_ndjson, reviewer_export, to_ndjson
from voice_harness.judges import calibration, review, service
from voice_harness.judges import section as judging
from voice_harness.judges.backends import ScriptedBackend
from voice_harness.signing import KeyStore

CRITERION = "SCENARIO-PROPOSAL-01"


def reseal(bundle):
    bundle["manifest_hash"] = digest(bundle["manifest"])
    return bundle


class JudgingEvidence(Base):
    def assessed_run(self, scenario="unsafe-disclosure-001", decide=True):
        run = self.run_of(scenario)
        out = service.assess_run(self.database, run, tested=TESTED, backends=[reference_judge()])
        request = next(e for e in self.events(run) if e["type"] == "tool.requested")
        script = {"findings": [{
            "category": "identity_verification_bypass", "severity": "critical", "confidence": 0.9,
            "evidence_event_ids": [request["event_id"]], "recommended_action": "hold",
            "blocking_capability": "deny_tool", "rationale": "Protected tool while unverified."}]}
        service.assess_online(self.database, run, ScriptedBackend(script, cfg("on")), tested=TESTED)
        if decide:
            rid = self.review_id(out)
            review.claim(self.database, rid, "r1", "reviewer")
            review.decide(self.database, rid, reviewer_id="r1", role="reviewer", disposition="overturn_fail",
                          rationale="Looked fine.", evidence_event_ids=[request["event_id"]])
        return run, out

    def review_id(self, out, criterion=CRITERION):
        return next(i for i in out["review_ids"] if review.get_review(self.database, i)["criterion_id"] == criterion)

    def test_manifest_lists_judge_and_human_results_with_deterministic_precedence(self):
        run, out = self.assessed_run()
        bundle = self.evidence.bundle(run)
        manifest, section = bundle["manifest"], bundle["judging"]
        self.assertTrue(verify_bundle(bundle))
        entry = manifest["judging"]
        self.assertEqual(entry["section_hash"], judging.section_digest(section))
        self.assertEqual((entry["evaluation_count"], entry["online_finding_count"], entry["human_decision_count"]),
                         (1, 1, 1))
        evaluation = section["evaluations"][0]
        self.assertEqual(len(evaluation["offline"]), 1)
        self.assertTrue(evaluation["rubric"]["findings"] and evaluation["independence"] and evaluation["adjudication"])
        self.assertEqual(manifest["human_adjudication"], section["human_adjudication"])
        decision = manifest["human_adjudication"][0]
        self.assertEqual((decision["evidence_class"], decision["effect"], decision["independent_model_validation"]),
                         ("human_adjudication", "not_applied_deterministic_failure", False))
        self.assertEqual(section["effective"]["overall_assurance"], "fail")
        self.assertEqual(entry["effective_overall_assurance"], "fail")
        self.assertIn(CRITERION, entry["deterministic_failure_criteria"])
        failure = next(f for f in section["deterministic_precedence"]["deterministic_failures"]
                       if f["criterion_id"] == CRITERION)
        self.assertFalse(failure["overturned"])
        self.assertEqual(failure["human_decision_effects"], ["not_applied_deterministic_failure"])
        self.assertEqual(section["online_findings"][0]["calibration"]["status"], "no_calibration_report")
        self.assertEqual(section["calibration"]["policy_hashes"], [])
        self.assertEqual(section["judge_coverage"], out["judge_coverage"])

    def test_resealed_forgery_of_section_or_manifest_fails(self):
        run, _ = self.assessed_run()
        bundle = self.evidence.bundle(run)
        plain = copy.deepcopy(bundle)
        plain["judging"]["effective"]["overall_assurance"] = "pass"
        self.assertFalse(verify_bundle(plain))
        resealed = copy.deepcopy(bundle)
        resealed["judging"]["effective"]["overall_assurance"] = "pass"
        resealed["manifest"]["judging"] = judging.manifest_entry(resealed["judging"])
        self.assertFalse(verify_bundle(reseal(resealed)))  # hash-consistent, not derivable from the ledger
        dropped = copy.deepcopy(bundle)
        dropped["judging"]["human_adjudication"] = []
        dropped["manifest"]["human_adjudication"] = []
        dropped["manifest"]["judging"] = judging.manifest_entry(dropped["judging"])
        self.assertFalse(verify_bundle(reseal(dropped)))
        stripped = copy.deepcopy(bundle)
        del stripped["judging"]
        self.assertFalse(verify_bundle(stripped))
        listed = copy.deepcopy(bundle)
        listed["manifest"]["human_adjudication"] = []
        self.assertFalse(verify_bundle(reseal(listed)))
        removed = copy.deepcopy(bundle)  # claiming "legacy" while the versions marker is present is detected
        del removed["manifest"]["judging"], removed["judging"]
        removed["manifest"]["human_adjudication"] = []
        self.assertFalse(verify_bundle(reseal(removed)))

    def test_section_is_covered_by_the_manifest_signature(self):
        keys = KeyStore(self.root)
        evidence = Evidence(self.database, self.root / "signed", signer=keys.signer())
        run, _ = self.assessed_run()
        bundle = evidence.bundle(run)
        ring = keys.public_keys()
        self.assertEqual(bundle["manifest"]["signature_status"], "signed_ed25519")
        self.assertTrue(verify_bundle(bundle, ring))
        tampered = copy.deepcopy(bundle)
        tampered["manifest"]["judging"]["effective_overall_assurance"] = "pass"
        self.assertFalse(verify_bundle(reseal(tampered), ring))  # manifest changed after signing
        again = copy.deepcopy(bundle)
        again["judging"]["assessed"] = False
        self.assertFalse(verify_bundle(again, ring))

    def test_older_bundles_without_the_section_still_verify(self):
        run = self.run_of("balance-verified-001")
        bundle = self.evidence.bundle(run)
        self.assertFalse(bundle["judging"]["assessed"])
        self.assertTrue(verify_bundle(bundle))
        del bundle["judging"], bundle["manifest"]["judging"]
        bundle["manifest"]["human_adjudication"] = []
        del bundle["manifest"]["versions"]["judging_section"]  # bundle["versions"] is the same mapping
        self.assertTrue(verify_bundle(reseal(bundle)))

    def test_reviewer_export_and_ndjson_carry_the_section(self):
        run, _ = self.assessed_run()
        derived = reviewer_export(self.evidence, run)
        self.assertEqual(derived["judging"]["human_adjudication"], derived["manifest"]["human_adjudication"])
        self.assertTrue(verify_bundle(derived))
        for bundle in (self.evidence.bundle(run), derived):
            restored = from_ndjson(to_ndjson(bundle))
            self.assertEqual(restored["judging"], bundle["judging"])
            self.assertTrue(verify_bundle(restored))
        forged = from_ndjson(to_ndjson(derived))
        forged["judging"]["online_findings"] = []
        self.assertFalse(verify_bundle(forged))

    def test_calibration_policy_hash_is_recorded_and_bound(self):
        run = self.run_of("balance-verified-001")
        judge = ScriptedBackend({"results": []}, cfg("cal"))
        policy = calibration.default_policy(calibration.fingerprint(judge.config))
        digest_ = calibration.save_policy(self.database, policy)
        calibration.save_report(self.database, calibration.evaluate_holdout(judge, policy))
        service.assess_run(self.database, run, tested=TESTED, backends=[judge])
        bundle = self.evidence.bundle(run)
        binding = bundle["judging"]["evaluations"][0]["adjudication"]["judge_calibration"][0]
        self.assertEqual((binding["status"], binding["policy_hash"], binding["policy_version"], binding["approved"]),
                         ("report_bound", digest_, policy.version, False))
        self.assertEqual(bundle["judging"]["calibration"]["policy_hashes"], [digest_])
        self.assertEqual(bundle["manifest"]["judging"]["calibration_policy_hashes"], [digest_])
        self.assertTrue(verify_bundle(bundle))

    def test_holds_resolve_judge_findings_and_review_items_over_the_api(self):
        run, out = self.assessed_run(decide=False)
        offline = next(f for e in self.events(run, "judge.offline_evaluated") for f in e["content"]["results"]
                       if f["evidence_event_ids"])
        online = self.events(run, "judge.online_finding")[0]["content"]
        rid = self.review_id(out)
        by_id = {e["event_id"]: e for e in self.database.events(run)}
        app = create_app(self.database, str(self.root), "op")
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer op", "Idempotency-Key": "x"}

            def hold(key, ids):
                return client.post(f"/api/v1/runs/{run}/preservation-holds", headers=headers, json={
                    "reason": "dispute", "owner_id": "reviewer_001", "expires_at": time.time() + 1000,
                    "idempotency_key": key, "finding_ids": ids})

            self.assertEqual(hold("n", ["nope"]).status_code, 404)
            scopes = {}
            for key, fid, cited in (
                ("h-off", offline["finding_id"], offline["evidence_event_ids"]),
                ("h-on", online["finding_id"], online["evidence_event_ids"]),
                ("h-rev", rid, []),
            ):
                response = hold(key, [fid])
                self.assertEqual(response.status_code, 201, response.text)
                scope = scopes[key] = response.json()["scope"]
                self.assertEqual(scope["type"], "findings")
                self.assertTrue(set(cited) <= set(scope["event_ids"]))
                types = {by_id[i]["type"] for i in scope["event_ids"]}
                self.assertTrue({"run.started", "session.opened"} <= types)
                if key != "h-on":
                    self.assertTrue({"judge.adjudicated", "rubric.evaluated"} <= types, key)
                if key == "h-rev":
                    self.assertIn("review.opened", types)
            # Online window bounds are part of the closure.
            self.assertTrue({online["window_from_event_id"], online["window_to_event_id"]}
                            <= set(scopes["h-on"]["event_ids"]))
            bundle = client.get(f"/api/v1/runs/{run}/evidence", headers=headers).json()
            self.assertTrue(verify_bundle(bundle))
            self.assertEqual(len(bundle["manifest"]["holds"]), 3)

    def test_disputed_finding_hold_keeps_review_items_and_evidence_out_of_tombstoning(self):
        run, out = self.assessed_run(decide=False)
        rid = self.review_id(out)
        now = [time.time()]
        evidence = Evidence(self.database, self.root / "ret", clock=lambda: now[0])
        view = evidence.hold(run, "dispute", "owner", now[0] + 90 * 86400, "k", finding_ids=[rid])
        protected = set(view["scope"]["event_ids"]) | {view["audit_event_id"]}
        # A decision recorded after activation joins the review item's protection (scope is fixed at activation).
        request = next(e for e in self.events(run) if e["type"] == "tool.requested")
        review.claim(self.database, rid, "r1", "reviewer")
        review.decide(self.database, rid, reviewer_id="r1", role="reviewer", disposition="confirm_fail",
                      rationale="Confirmed.", evidence_event_ids=[request["event_id"]])
        late = {e["event_id"] for e in self.events(run, "review.") if e["type"] != "review.opened"}
        self.assertFalse(late & protected)
        now[0] += 31 * 86400
        by_sequence = {e["sequence"]: e["event_id"] for e in self.database.events(run)}
        tombstoned = {by_sequence[s] for s in evidence.purge_metadata(run)}
        self.assertTrue(tombstoned)
        self.assertFalse(tombstoned & (protected | late | {request["event_id"]}))
        bundle = evidence.bundle(run)
        self.assertTrue(verify_bundle(bundle))
        kept = {e["event_id"] for e in bundle["events"] if "tombstone" not in e}
        self.assertTrue((protected | late) <= kept)
        self.assertTrue(bundle["judging"]["human_adjudication"])  # the disputed item and its decision survive
        self.assertTrue(any(e["type"] == "judge.online_finding" and "tombstone" in e for e in bundle["events"]))
        self.assertEqual(bundle["judging"]["online_findings"], [])  # purged payloads are not invented back


if __name__ == "__main__":
    unittest.main()
