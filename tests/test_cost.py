"""Phase 3 cost accounting: AC-12 (known tariff fixtures, exact totals) and AC-19 (mixed/missing/cumulative/corrected)."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from pydantic import ValidationError

from voice_harness import cost, cost_store
from voice_harness.api import create_app
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.event_contracts import EVENT_CONTENT_CONTRACTS
from voice_harness.evidence import Evidence, verify_bundle
from voice_harness.exports import from_ndjson, reviewer_export, to_ndjson
from voice_harness.pricing import PricingTable, dec, load_table
from voice_harness.storage import Database

FIXTURE = Path(__file__).parent / "fixtures" / "pricing_fixture.json"
TABLE = load_table(FIXTURE)


def rep(report_id="r1", meter="tok_in", scope="resp_1", quantity="1000000", **kw):
    base = {
        "report_id": report_id, "component": "voice", "provider": "fixture", "service": "rt", "model": None,
        "region": None, "meter_id": meter, "billing_scope_id": scope, "scope_type": "request",
        "report_kind": "incremental", "revision": 1, "corrects_report_id": None, "covers_scope_ids": [],
        "provenance": "provider_reported_usage", "quantity": quantity, "unit": "token", "cached_quantity": None,
        "generation_state": "completed", "usage_date": "2026-10-17", "interval_start_ms": None,
        "interval_end_ms": None, "estimation_method": None, "assumptions": [], "unknown_reason": None,
        "evidence_event_ids": [],
    }
    base.update(kw)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(self.root / "t.sqlite")))
        self.engine = Engine(self.database)
        self.evidence = Evidence(self.database, self.root / "objects")
        self.run = self.engine.create(RunConfig().model_dump())
        patcher = mock.patch.dict(os.environ, {"HARNESS_PRICING_TABLE": str(FIXTURE)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, *reports):
        return [cost.record_usage(self.database, self.run, r) for r in reports]

    def section(self, allocations=()):
        return cost.build_section(self.run, self.database.events(self.run), allocations, TABLE)

    def lines(self, section=None):
        return {x["line_id"]: x for x in (section or self.section())["lines"]}

    def active(self, section=None):
        return [x for x in (section or self.section())["lines"] if x["superseded_by"] is None]

    def amounts(self, section=None):
        return {(x["meter_id"], x["billing_scope_id"]): x["amount"] for x in self.active(section)}


class Ac12KnownTariffs(Base):
    def test_token_only_exact(self):
        self.add(rep(quantity="1000000"), rep("o1", "tok_out", "resp_1", "500000"))
        section = self.section()
        self.assertEqual(self.amounts(section), {("tok_in", "resp_1"): "10.000000", ("tok_out", "resp_1"): "10.000000"})
        self.assertEqual(section["summaries"][0]["known_subtotal"], "20.000000")
        line = self.active(section)[0]
        self.assertEqual(line["tariff"]["tariff_id"], "fx-tok-in")
        self.assertEqual(len(line["tariff"]["hash"]), 64)
        self.assertEqual(line["calculation"]["billed_quantity"], "1000000")

    def test_minute_only_with_increment_and_minimum(self):
        self.add(rep("a", "min_in", "s61", "61", unit="second"), rep("b", "min_in", "s3", "3", unit="second"),
                 rep("c", "min_in", "s60", "60", unit="second"))
        self.assertEqual(
            self.amounts(),
            {("min_in", "s61"): "0.075000", ("min_in", "s3"): "0.015000", ("min_in", "s60"): "0.060000"},
        )

    def test_cached_usage_is_a_subset_not_added(self):
        self.add(rep(quantity="1000000", cached_quantity="400000"))
        line = self.active()[0]
        # 600000 uncached at 10/1M + 400000 cached at 2.50/1M; the cached tokens are not billed twice.
        self.assertEqual(line["amount"], "7.000000")
        self.assertEqual(line["calculation"]["cached_quantity"], "400000")
        with self.assertRaises(ValidationError):
            cost.record_usage(self.database, self.run, rep("bad", quantity="10", cached_quantity="11"))

    def test_cached_without_documented_billing_is_unknown_not_guessed(self):
        self.add(rep("o", "tok_out", "resp_1", "1000", cached_quantity="10"))
        line = self.active()[0]
        self.assertIsNone(line["amount"])
        self.assertEqual(line["unknown_reason"], "CACHED_BILLING_NOT_DOCUMENTED")
        self.assertEqual(line["provenance"], "unknown")

    def test_rounding_rules(self):
        self.add(rep("a", "asr_sec", "asr1", "7", unit="second", component="asr", service="asr"),
                 rep("j", "judge_tok", "j1", "1", component="online_judge", service="judge"))
        amounts = self.amounts()
        self.assertEqual(amounts[("asr_sec", "asr1")], "0.0014")  # 0.001435 half-up at scale 4
        self.assertEqual(amounts[("judge_tok", "j1")], "0.000001")  # 0.0000005 rounded up at scale 6

    def test_exact_totals_have_no_binary_float_error(self):
        self.add(*[rep(f"r{i}", "judge_tok", f"s{i}", "2000", component="online_judge", service="judge")
                   for i in range(10)])
        summary = self.section()["summaries"][0]
        self.assertEqual(summary["known_subtotal"], "0.010000")  # ten lines of 0.001; floats would drift
        self.assertEqual(summary["by_provenance"]["provider_reported_usage"], "0.010000")

    def test_missing_usage_is_null_with_reason_never_zero(self):
        self.add(rep("m", quantity=None, unknown_reason="PROVIDER_DID_NOT_REPORT"))
        line = self.active()[0]
        self.assertIsNone(line["amount"])
        self.assertEqual((line["provenance"], line["unknown_reason"]), ("unknown", "PROVIDER_DID_NOT_REPORT"))
        self.assertEqual(self.section()["summaries"], [])  # no known amount: no zero subtotal is invented
        self.assertEqual(self.section()["unknown_line_count"], 1)
        with self.assertRaises(ValidationError):
            cost.record_usage(self.database, self.run, rep("n", quantity=None, unknown_reason=None))

    def test_unknown_price_and_missing_tariff_and_tariff_dates(self):
        self.add(rep("t", "tts_chars", "t1", "500", unit="character", component="tts", service="tts"),
                 rep("x", "no_such_meter", "x1", "5"),
                 rep("old", "eu_tok", "e24", "1000000", service="eu", usage_date="2025-06-01"),
                 rep("new", "eu_tok", "e26", "1000000", service="eu", usage_date="2026-06-01"),
                 rep("pre", "eu_tok", "e23", "1000000", service="eu", usage_date="2024-06-01"))
        lines = {x["billing_scope_id"]: x for x in self.active()}
        self.assertEqual(lines["t1"]["unknown_reason"], "FIXTURE_PRICE_UNKNOWN")
        self.assertEqual(lines["x1"]["unknown_reason"], "NO_TARIFF_FOR_METER_DATE")
        self.assertEqual((lines["e24"]["amount"], lines["e24"]["currency"]), ("9.000000", "EUR"))
        self.assertEqual((lines["e26"]["amount"], lines["e26"]["currency"]), ("11.000000", "EUR"))
        self.assertEqual(lines["e23"]["unknown_reason"], "NO_TARIFF_FOR_METER_DATE")

    def test_no_floats_anywhere(self):
        with self.assertRaises(ValidationError):
            cost.record_usage(self.database, self.run, rep("f", quantity=1.5))
        with self.assertRaises(TypeError):
            dec(0.1)
        with self.assertRaises(ValidationError):
            PricingTable({**json.loads(FIXTURE.read_text()), "tariffs": [
                {**json.loads(FIXTURE.read_text())["tariffs"][0], "unit_price": 10.0}]})

    def test_shipped_table_has_no_invented_prices(self):
        shipped = load_table(Path(cost.__file__).parent / "data" / "pricing_table.json")
        self.assertTrue(shipped.tariffs)
        for tariff in shipped.tariffs:
            self.assertIsNone(tariff.unit_price)
            self.assertTrue(tariff.unknown_reason)
        reports = cost.reports_from_adapter_usage(
            "openai", {"input_tokens": 100, "output_tokens": 50, "raw": {}}, scope_id="resp_9",
            usage_date="2026-10-17", service="realtime")
        self.add(*reports)
        section = cost.build_section(self.run, self.database.events(self.run), (), shipped)
        self.assertTrue(all(x["amount"] is None for x in section["lines"]))
        self.assertEqual(section["summaries"], [])
        self.assertEqual(section["completeness"], "unknown")

    def test_tariff_validation(self):
        base = json.loads(FIXTURE.read_text())
        bad = copy.deepcopy(base)
        bad["tariffs"][0]["unit_price"] = None
        bad["tariffs"][0]["unknown_reason"] = None
        with self.assertRaises(ValidationError):
            PricingTable(bad)
        overlap = copy.deepcopy(base)
        overlap["tariffs"].append({**overlap["tariffs"][-2], "tariff_id": "dup", "effective_from": "2025-06-01"})
        with self.assertRaises(ValueError):
            PricingTable(overlap)


class Ac19Provenance(Base):
    def test_exclusivity_group_never_adds_alternative_meters(self):
        self.add(rep("t", "tok_in", "resp_1", "1000000"), rep("m", "min_in", "resp_1", "60", unit="second"))
        section = self.section()
        self.assertEqual(self.amounts(section), {("tok_in", "resp_1"): "10.000000"})
        self.assertEqual(section["summaries"][0]["known_subtotal"], "10.000000")
        loser = next(x for x in section["lines"] if x["meter_id"] == "min_in")
        self.assertEqual(loser["superseded_reason"], "EXCLUSIVITY_GROUP_ALTERNATIVE")
        # independent meters (input vs output groups) coexist
        self.add(rep("o", "tok_out", "resp_1", "1000000"))
        self.assertEqual(self.section()["summaries"][0]["known_subtotal"], "30.000000")

    def test_duplicate_reports_are_deduplicated_by_meter_scope_revision(self):
        self.add(rep(), rep(), rep(quantity="999"))
        section = self.section()
        self.assertEqual(len(section["lines"]), 1)
        self.assertEqual([a["kind"] for a in section["anomalies"]], ["duplicate_report", "conflicting_duplicate_report"])
        self.assertEqual(section["summaries"][0]["known_subtotal"], "10.000000")

    def test_cumulative_session_usage_counts_once(self):
        cumulative = dict(scope_type="session", report_kind="cumulative")
        self.add(rep("c1", scope="sess_1", quantity="400000", revision=1, **cumulative),
                 rep("c2", scope="sess_1", quantity="1000000", revision=2, **cumulative),
                 rep("i1", scope="sess_1", quantity="300000", scope_type="session"))
        section = self.section()
        self.assertEqual(self.amounts(section), {("tok_in", "sess_1"): "10.000000"})
        reasons = sorted(x["superseded_reason"] for x in section["lines"] if x["superseded_by"])
        self.assertEqual(reasons, ["CUMULATIVE_REVISION", "INCREMENTAL_COVERED_BY_CUMULATIVE"])

    def test_corrected_usage_supersedes_not_adds(self):
        self.add(rep("a", quantity="1000000"), rep("a", quantity="500000", revision=2),
                 rep("b", scope="resp_2", quantity="1000000"),
                 rep("b2", scope="resp_2", quantity="200000", corrects_report_id="b"))
        section = self.section()
        self.assertEqual(self.amounts(section), {("tok_in", "resp_1"): "5.000000", ("tok_in", "resp_2"): "2.000000"})
        self.assertEqual(section["summaries"][0]["known_subtotal"], "7.000000")
        self.assertEqual(len(section["lines"]), 4)  # history retained as superseded lines

    def test_reported_supersedes_estimate_and_comparison_is_same_scope(self):
        estimate = dict(provenance="estimated", estimation_method="mean_tokens_per_second",
                        assumptions=["16 tokens/s"])
        self.add(rep("e", quantity="800000", **estimate), rep("r", quantity="1000000"))
        section = self.section()
        self.assertEqual(self.amounts(section), {("tok_in", "resp_1"): "10.000000"})
        self.assertEqual(section["summaries"][0]["by_provenance"]["estimated"], "0.000000")
        comparison = section["estimated_vs_actual"][0]
        self.assertEqual((comparison["estimated_amount"], comparison["actual_amount"], comparison["same_scope"]),
                         ("8.000000", "10.000000", True))
        with self.assertRaises(ValidationError):
            cost.record_usage(self.database, self.run, rep("z", provenance="estimated"))

    def test_session_report_covers_per_turn_estimates(self):
        estimate = dict(provenance="estimated", estimation_method="per_turn_model", assumptions=[])
        self.add(rep("t1", scope="turn_1", quantity="100000", **estimate), rep("t2", scope="turn_2", quantity="100000", **estimate),
                 rep("s", scope="sess_1", quantity="250000", scope_type="session", report_kind="cumulative",
                     covers_scope_ids=["turn_1", "turn_2"]))
        section = self.section()
        self.assertEqual(self.amounts(section), {("tok_in", "sess_1"): "2.500000"})

    def test_mixed_provenance_partial_with_unknown_cancelled_generation(self):
        cost.declare_expected_components(self.database, self.run, ["voice", "online_judge"])
        self.add(rep(quantity="1000000"),
                 rep("c", "tok_out", "resp_2", None, generation_state="cancelled", unknown_reason="CANCELLED_BILLING_UNDOCUMENTED"),
                 rep("j", "judge_tok", "j1", "20000", component="online_judge", service="judge",
                     provenance="estimated", estimation_method="prompt_size_estimate", assumptions=["2000 tokens/turn"]))
        section = self.section()
        summary = section["summaries"][0]
        self.assertEqual(summary["by_provenance"], {
            "estimated": "0.010000", "provider_reported_usage": "10.000000", "invoice_reconciled": "0.000000"})
        self.assertEqual(summary["known_subtotal"], "10.010000")
        self.assertEqual((summary["completeness"], summary["summary_label"]), ("partial", "partial_mixed_provenance"))
        self.assertIsNone(summary["total_cost"])  # a partial known sum is not a total
        self.assertEqual((summary["unknown_line_count"], summary["unknown_components"]), (1, ["voice_cancelled_generation"]))

    def test_complete_requires_all_expected_components_known(self):
        cost.declare_expected_components(self.database, self.run, ["voice", "online_judge"])
        self.add(rep())
        section = self.section()
        self.assertEqual(section["completeness"], "partial")
        self.assertEqual(section["uncovered_components"], ["online_judge"])
        self.add(rep("j", "judge_tok", "j1", "1000", component="online_judge", service="judge"))
        summary = self.section()["summaries"][0]
        self.assertEqual((summary["completeness"], summary["total_cost"]), ("complete", "10.000500"))
        self.assertEqual(summary["summary_label"], "complete_provider_reported_usage")

    def test_unknown_metering_scope_is_never_complete(self):
        cost.declare_expected_components(self.database, self.run, ["voice"])
        self.add(rep(scope_type="unknown"))
        self.assertEqual(self.section()["completeness"], "partial")

    def test_amounts_are_grouped_by_currency(self):
        self.add(rep(), rep("e", "eu_tok", "e1", "1000000", service="eu"))
        by_currency = {s["currency"]: s["known_subtotal"] for s in self.section()["summaries"]}
        self.assertEqual(by_currency, {"EUR": "11.000000", "USD": "10.000000"})

    def test_invoice_reconciliation_requires_allocation_and_matching_scope(self):
        self.add(rep(quantity="1000000"))
        other = self.engine.create(RunConfig().model_dump())
        record = {"record_id": "inv_1", "provider": "fixture", "account_id": "acct_x", "period_start": "2026-10-01",
                  "period_end": "2026-10-31", "invoice_ref": "INV-2026-10", "currency": "USD", "amount": "55.500000"}
        stored = cost_store.put_record(self.database, record)
        self.assertEqual((stored["status"], stored["unallocated_amount"]), ("unallocated", "55.500000"))
        # unallocated account-period charges never reach a run
        self.assertFalse(any(x["provenance"] == "invoice_reconciled" for x in self.section()["lines"]))
        self.assertEqual(cost_store.allocations_for_run(self.database, self.run), [])
        for bad in (
            {"run_id": self.run, "meter_id": "tok_in", "billing_scope_id": "resp_1", "amount": "9.5", "method": "m",
             "evidence_ids": []},
            {"run_id": self.run, "meter_id": "tok_in", "billing_scope_id": "resp_1", "amount": "999", "method": "m",
             "evidence_ids": ["e"]},
        ):
            with self.assertRaises(ValidationError):
                cost_store.put_record(self.database, {**record, "allocations": [bad]})
        with self.assertRaises(KeyError):
            cost_store.put_record(self.database, {**record, "allocations": [
                {"run_id": "run_missing", "meter_id": "tok_in", "billing_scope_id": "resp_1", "amount": "1",
                 "method": "m", "evidence_ids": ["e"]}]})
        allocation = {"run_id": self.run, "meter_id": "tok_in", "billing_scope_id": "resp_1", "amount": "9.800000",
                      "method": "invoice_line_to_response_id", "evidence_ids": ["invoice_line_17"]}
        unmatched = {"run_id": self.run, "meter_id": "tok_in", "billing_scope_id": "nowhere", "amount": "1",
                     "method": "manual", "evidence_ids": ["x"]}
        wrong_run = {"run_id": other, "meter_id": "tok_in", "billing_scope_id": "resp_1", "amount": "2",
                     "method": "manual", "evidence_ids": ["y"]}
        cost_store.put_record(self.database, {**record, "allocations": [allocation, unmatched, wrong_run]})
        with self.assertRaises(ValueError):
            cost_store.put_record(self.database, {**record, "amount": "1.000000"})
        flat = cost_store.allocations_for_run(self.database, self.run)
        section = self.section(flat)
        invoice = [x for x in section["lines"] if x["provenance"] == "invoice_reconciled"]
        self.assertEqual(len(invoice), 1)
        self.assertEqual((invoice[0]["amount"], invoice[0]["invoice_reference"]), ("9.800000", "INV-2026-10"))
        self.assertEqual(self.amounts(section), {("tok_in", "resp_1"): "9.800000"})
        self.assertEqual(section["summaries"][0]["by_provenance"]["invoice_reconciled"], "9.800000")
        self.assertEqual(section["summaries"][0]["by_provenance"]["provider_reported_usage"], "0.000000")
        self.assertEqual([u["billing_scope_id"] for u in section["reconciliation"]["unmatched_allocations"]], ["nowhere"])
        other_section = cost.build_section(other, self.database.events(other),
                                           cost_store.allocations_for_run(self.database, other), TABLE)
        self.assertEqual(other_section["lines"], [])  # an allocation for a run with no such usage attributes nothing
        records = {r["record_id"]: r for r in cost_store.records(self.database)}
        self.assertEqual(records["inv_1"]["status"], "partially_allocated")
        self.assertEqual(records["inv_1"]["revision"], 2)

    def test_event_validation_and_evidence_references(self):
        self.assertIn("cost.usage_reported", EVENT_CONTENT_CONTRACTS)
        with self.assertRaises(ValueError):
            cost.record_usage(self.database, self.run, rep("r", evidence_event_ids=["evt_missing"]))
        with self.assertRaises(ValidationError):
            cost.record_usage(self.database, self.run, {**rep("r"), "surprise": 1})

    def test_adapter_usage_translation(self):
        openai = cost.reports_from_adapter_usage(
            "openai", {"input_tokens": 150, "output_tokens": 40, "raw": {
                "input_token_details": {"audio_tokens": 100, "text_tokens": 50, "cached_tokens": 20,
                                        "cached_tokens_details": {"audio_tokens": 20, "text_tokens": 0}},
                "output_token_details": {"audio_tokens": 30, "text_tokens": 10}}},
            scope_id="resp_1", usage_date="2026-10-17")
        by = {r["meter_id"]: r for r in openai}
        self.assertEqual(set(by), {"audio_input_tokens", "text_input_tokens", "audio_output_tokens", "text_output_tokens"})
        self.assertEqual((by["audio_input_tokens"]["quantity"], by["audio_input_tokens"]["cached_quantity"]), ("100", "20"))
        plain = cost.reports_from_adapter_usage(
            "openai", {"input_tokens": 5, "output_tokens": 2, "raw": {}}, scope_id="resp_2", usage_date="2026-10-17")
        self.assertEqual({r["meter_id"] for r in plain}, {"input_tokens_unclassified", "output_tokens_unclassified"})
        nova = cost.reports_from_adapter_usage(
            "aws_nova_sonic", {"input_tokens": 9, "output_tokens": 4, "raw": {"cumulative": True}},
            scope_id="sess_1", usage_date="2026-10-17", revision=3)
        self.assertEqual({(r["report_kind"], r["scope_type"], r["revision"]) for r in nova}, {("cumulative", "session", 3)})
        for report in openai + plain + nova:
            cost.record_usage(self.database, self.run, report)


class Export(Base):
    def prepare(self):
        cost.declare_expected_components(self.database, self.run, ["voice"])
        self.add(rep(quantity="1000000", cached_quantity="400000"), rep("u", "tok_out", "resp_1", None,
                                                                         unknown_reason="NOT_REPORTED"))

    def test_bundle_contains_manifest_linked_cost_section_and_verifies(self):
        self.prepare()
        bundle = self.evidence.bundle(self.run)
        self.assertTrue(verify_bundle(bundle))
        link = bundle["manifest"]["cost"]
        self.assertEqual(link["section_hash"], cost.manifest_entry(bundle["cost"])["section_hash"])
        self.assertEqual(link["completeness"], "partial")
        self.assertEqual(bundle["manifest"]["pricing"]["table_id"], TABLE.table_id)
        with self.database.connection() as db:
            rows = db.execute("SELECT COUNT(*) AS n FROM harness_cost_lines WHERE run_id=?", (self.run,)).fetchone()
            self.assertEqual(rows["n"], 2)
            self.assertIsNotNone(db.execute("SELECT 1 FROM harness_migrations WHERE version=11").fetchone())
        self.evidence.bundle(self.run)  # idempotent snapshot
        with self.database.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) AS n FROM harness_cost_lines").fetchone()["n"], 2)

    def test_tampered_cost_section_fails_verification(self):
        self.prepare()
        for mutate in (
            lambda b: b["cost"]["lines"][0].update(amount="0.000001"),
            lambda b: b["cost"].update(completeness="complete"),
            lambda b: b["cost"]["pricing_document"]["tariffs"][0].update(unit_price="0.000001"),
            lambda b: b.pop("cost"),
        ):
            bundle = copy.deepcopy(self.evidence.bundle(self.run))
            mutate(bundle)
            self.assertFalse(verify_bundle(bundle))

    def test_resealed_forgery_is_caught_by_rederivation(self):
        from voice_harness.engine import digest

        self.prepare()
        bundle = copy.deepcopy(self.evidence.bundle(self.run))
        bundle["cost"]["lines"][0]["amount"] = "0.000001"
        bundle["manifest"]["cost"]["section_hash"] = digest(bundle["cost"])
        bundle["manifest_hash"] = digest(bundle["manifest"])
        self.assertFalse(verify_bundle(bundle))

    def test_ndjson_and_reviewer_export_round_trip(self):
        self.prepare()
        bundle = self.evidence.bundle(self.run)
        self.assertTrue(verify_bundle(from_ndjson(to_ndjson(bundle))))
        derived = reviewer_export(self.evidence, self.run)
        self.assertTrue(verify_bundle(derived))
        self.assertTrue(verify_bundle(from_ndjson(to_ndjson(derived))))

    def test_mock_run_reports_unknown_with_no_lines(self):
        bundle = self.evidence.bundle(self.run)
        self.assertEqual(bundle["cost"]["lines"], [])
        self.assertEqual(bundle["cost"]["completeness"], "unknown")
        self.assertTrue(verify_bundle(bundle))


class Api(unittest.TestCase):
    def test_cost_endpoint_and_reconciliation_routes(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"HARNESS_PRICING_TABLE": str(FIXTURE)}
        ):
            database = Database(str(Path(directory) / "t.sqlite"))
            app = create_app(database, directory, "op")
            run = Engine(database).create(RunConfig().model_dump())
            cost.record_usage(database, run, rep(quantity="2000000"))
            with TestClient(app) as client:
                h = {"Authorization": "Bearer op"}
                self.assertEqual(client.get(f"/api/v1/runs/{run}/cost").status_code, 401)
                body = client.get(f"/api/v1/runs/{run}/cost", headers=h).json()
                self.assertEqual(body["summaries"][0]["known_subtotal"], "20.000000")
                self.assertEqual(body["pricing"]["table_id"], TABLE.table_id)
                self.assertEqual(client.get("/api/v1/runs/run_nope/cost", headers=h).status_code, 404)
                record = {"record_id": "inv_9", "provider": "fixture", "account_id": "a", "period_start": "2026-10-01",
                          "period_end": "2026-10-31", "invoice_ref": "INV-9", "currency": "USD", "amount": "30"}
                self.assertEqual(client.post("/api/v1/cost/reconciliations", headers=h, json=record).status_code, 201)
                listing = client.get("/api/v1/cost/reconciliations?status=unallocated", headers=h).json()
                self.assertEqual([r["record_id"] for r in listing["items"]], ["inv_9"])
                self.assertEqual(client.get("/api/v1/cost/reconciliations?status=bogus", headers=h).status_code, 422)
                bad = {**record, "allocations": [{"run_id": run, "meter_id": "tok_in", "billing_scope_id": "resp_1",
                                                  "amount": "1", "method": "m", "evidence_ids": []}]}
                self.assertEqual(client.post("/api/v1/cost/reconciliations", headers=h, json=bad).status_code, 422)
                self.assertEqual(client.post("/api/v1/cost/reconciliations", headers=h,
                                             json={**record, "amount": "31"}).status_code, 409)
                after = client.get(f"/api/v1/runs/{run}/cost", headers=h).json()
                self.assertFalse(any(x["provenance"] == "invoice_reconciled" for x in after["lines"]))
                evidence = client.get(f"/api/v1/runs/{run}/evidence", headers=h).json()
                self.assertEqual(evidence["cost"]["run_id"], run)


if __name__ == "__main__":
    unittest.main()
