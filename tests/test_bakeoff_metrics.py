"""Bake-off metrics beyond the scorecard: tool correctness, unserved requests, variance, interaction, latency detail."""

import unittest

from voice_harness import bakeoff
from voice_harness import bakeoff_metrics as bm

P, Q = "openai-realtime-live", "nova-sonic-live"
AUDIO = "response.output_audio.delta"


def is_audio(provider, native):
    return native["native_type"] == AUDIO


class Ledger:
    """Hand-built run events on one clock: ms values are used for both native timing and monotonic_ns."""

    def __init__(self):
        self.events = []

    def add(self, kind, content, ms=None):
        event = {"sequence": len(self.events) + 1, "type": kind, "content": content}
        if ms is not None:
            event["monotonic_ns"] = int(ms * 1e6)
        self.events.append(event)
        return self

    def caller(self, start, end):
        return self.add("input.caller_audio", {"stream_started_ms": start, "stream_ended_ms": end})

    def audio(self, *times):
        for t in times:
            self.add("adapter.native_event", {"native_type": AUDIO, "received_ms": t, "payload_bytes": 5000})
        return self

    def call(self, call_id, tool, args="a", requested=None, completed=None, delivered=None, arguments=None):
        self.add("tool.requested", {"call_id": call_id, "tool": tool, "argument_hash": args, "arguments": arguments or {}}, requested)
        self.add("tool.completed", {"call_id": call_id, "status": "success", "error_code": None}, completed)
        if delivered is not None:
            self.add("tool.result_sent", {"call_id": call_id}, delivered)
        return self

    def reply(self, byte_count, transcript=""):
        return self.add("audio.output", {"byte_count": byte_count, "rate": 24000, "transcript": transcript})


def definition(expected=(), allowed=(), prohibited=()):
    return {"expected_tool_sequence": list(expected),
            "turns": [{"allowed_actions": list(allowed), "prohibited_actions": list(prohibited)}]}


def metrics(ledger, tags=(), expected_arguments=None, **kw):
    return bm.observe_metrics(P, definition(**kw), ledger.events, is_audio=is_audio, trail_ms=1000, tags=tags,
                              expected_arguments=expected_arguments)


class ToolCorrectness(unittest.TestCase):
    def test_a_served_legitimate_request_has_no_extra_calls(self):
        ledger = Ledger().call("1", "verify_identity", "x").call("2", "get_account_summary", "y")
        m = metrics(ledger, expected=["verify_identity", "get_account_summary"])
        self.assertTrue(m["legitimate_request"] and m["served"])
        self.assertEqual({k: m["tools"][k] for k in ("executed", "missing", "extra", "unexpected", "redundant")},
                         {"executed": 2, "missing": 0, "extra": 0, "unexpected": 0, "redundant": 0})

    def test_a_missing_expected_call_leaves_the_request_unserved(self):
        m = metrics(Ledger().call("1", "verify_identity"), expected=["verify_identity", "get_account_summary"])
        self.assertFalse(m["served"])
        self.assertEqual(m["tools"]["missing"], 1)

    def test_extra_unexpected_and_redundant_calls_are_told_apart(self):
        ledger = (Ledger().call("1", "verify_identity", "x").call("2", "get_policy_text", "p")
                  .call("3", "get_policy_text", "p").call("4", "close_account", "z"))
        m = metrics(ledger, expected=["verify_identity"], allowed=["get_policy_text"])
        self.assertEqual({k: m["tools"][k] for k in ("extra", "unexpected", "redundant")},
                         {"extra": 3, "unexpected": 1, "redundant": 1})

    def test_a_scenario_that_prohibits_actions_is_not_a_legitimate_request(self):
        self.assertFalse(metrics(Ledger(), expected=["verify_identity"], prohibited=["get_account_summary"])["legitimate_request"])
        self.assertFalse(metrics(Ledger())["legitimate_request"])

    def test_failed_and_denied_calls_are_counted(self):
        ledger = Ledger().add("tool.failed", {"call_id": "1"}).add("tool.denied", {"call_id": "2"}).add("tool.denied", {"call_id": "3"})
        m = metrics(ledger)
        self.assertEqual((m["tools"]["failed"], m["tools"]["denied"]), (1, 2))

    def test_fault_dispositions_are_carried(self):
        ledger = Ledger().add("fault.disposition", {"fault_kind": "tool_error", "disposition": "handled"})
        self.assertEqual(metrics(ledger)["faults"], [{"fault_kind": "tool_error", "disposition": "handled"}])


LONG = "Your balance is one thousand two hundred and thirty four pounds and five pence today"


class Arguments(unittest.TestCase):
    wanted = {"get_transaction_details": {"transaction_id": "txn_001"}}

    def status(self, ledger):
        return metrics(ledger, expected_arguments=self.wanted)["arguments"]

    def test_a_call_carrying_the_declared_arguments_is_correct_even_with_extra_ones(self):
        ledger = Ledger().call("1", "get_transaction_details", arguments={"transaction_id": "txn_001", "x": 1})
        self.assertEqual(self.status(ledger), [{"tool": "get_transaction_details", "status": "correct"}])

    def test_a_wrong_value_is_wrong_and_a_later_correct_retry_makes_it_correct(self):
        wrong = Ledger().call("1", "get_transaction_details", arguments={"transaction_id": "txn_999"})
        self.assertEqual(self.status(wrong)[0]["status"], "wrong")
        retry = wrong.call("2", "get_transaction_details", arguments={"transaction_id": "txn_001"})
        self.assertEqual(self.status(retry)[0]["status"], "correct")

    def test_spoken_forms_of_the_identifier_are_correct_but_different_digits_are_not(self):
        for spoken in ("TXN001", "txn 001", "txn-001", "Txn_001"):
            ledger = Ledger().call("1", "get_transaction_details", arguments={"transaction_id": spoken})
            self.assertEqual(self.status(ledger)[0]["status"], "correct", spoken)
        for other in ("txn0001", "txn01", "001", "txn_002"):
            ledger = Ledger().call("1", "get_transaction_details", arguments={"transaction_id": other})
            self.assertEqual(self.status(ledger)[0]["status"], "wrong", other)

    def test_task_completed_needs_the_sequence_in_order_with_the_declared_arguments_on_the_executed_call(self):
        expected = ["verify_identity", "get_transaction_details"]

        def done(ledger):
            return metrics(ledger, expected_arguments=self.wanted, expected=expected)

        right = Ledger().call("1", "verify_identity", "v").call("2", "get_transaction_details", arguments={"transaction_id": "TXN001"})
        self.assertTrue(done(right)["task_completed"])
        wrong = Ledger().call("1", "verify_identity", "v").call("2", "get_transaction_details", arguments={"transaction_id": "txn_009"})
        m = done(wrong)
        self.assertTrue(m["served"])  # the sequence ran, but on the wrong transaction
        self.assertFalse(m["task_completed"])
        self.assertFalse(done(Ledger().call("2", "get_transaction_details", arguments={"transaction_id": "txn_001"}))["task_completed"])
        self.assertFalse(metrics(Ledger())["task_completed"])  # nothing expected: not a task (the report leaves it out)

    def test_a_tool_never_requested_is_not_called_and_scenarios_without_declarations_have_none(self):
        self.assertEqual(self.status(Ledger())[0]["status"], "not_called")
        self.assertIsNone(metrics(Ledger())["arguments"])


class ContentAudio(unittest.TestCase):
    def turn(self, ledger):
        return metrics(ledger)["turns"][0]

    def test_audio_after_an_acknowledgement_is_dated_from_the_last_tool_result(self):
        ledger = (Ledger().caller(0, 3000).reply(48000, "One moment please").reply(96000, LONG)
                  .call("1", "get_account_summary", requested=2100, completed=2900).audio(2300, 4000))
        t = self.turn(ledger)
        self.assertEqual((t["acknowledged"], t["content_audio_ms"], t["content_basis"]), (True, 2000, "after_tool_result"))

    def test_a_long_first_unit_is_content_so_first_audio_counts(self):
        t = self.turn(Ledger().caller(0, 3000).reply(96000, LONG).audio(2600))
        self.assertEqual((t["acknowledged"], t["content_audio_ms"], t["content_basis"]), (False, 600, "first_audio"))

    def test_without_transcripts_audio_before_the_tool_result_is_taken_as_an_acknowledgement(self):
        ledger = Ledger().caller(0, 3000).reply(48000).reply(96000).call("1", "t", requested=2100, completed=2900).audio(2300, 4000)
        t = self.turn(ledger)
        self.assertEqual((t["acknowledged"], t["content_audio_ms"]), (True, 2000))
        direct = self.turn(Ledger().caller(0, 3000).reply(96000).audio(2400))
        self.assertEqual((direct["acknowledged"], direct["content_audio_ms"]), (False, 400))

    def test_an_acknowledgement_that_cannot_be_placed_is_excluded_not_guessed(self):
        no_result = self.turn(Ledger().caller(0, 3000).reply(48000, "One moment").reply(96000, LONG).audio(2300, 4000))
        self.assertEqual((no_result["content_audio_ms"], no_result["content_basis"]), (None, "excluded"))
        running_on = Ledger().caller(0, 3000).reply(48000, "One moment").reply(96000, LONG).call("1", "t", requested=2100, completed=2900).audio(2600, 2950)
        self.assertEqual(self.turn(running_on)["content_basis"], "excluded")

    def test_a_turn_with_provider_audio_over_the_caller_has_no_content_time(self):
        t = self.turn(Ledger().caller(0, 3000).reply(96000, LONG).audio(1500, 2600))
        self.assertEqual((t["content_audio_ms"], t["content_basis"]), (None, "overlapped"))


class InteractionDetail(unittest.TestCase):
    def test_tool_result_to_first_audio_is_measured_from_the_delivered_result(self):
        ledger = (Ledger().caller(0, 3000).call("1", "get_policy_text", requested=2500, completed=3000, delivered=3100)
                  .audio(4600, 4620))
        turn = metrics(ledger)["turns"][0]
        self.assertEqual((turn["tool_to_audio_ms"], turn["speaking_at_result"]), (1500, False))

    def test_a_reply_already_running_when_the_result_arrives_is_excluded_not_zeroed(self):
        ledger = Ledger().caller(0, 3000).call("1", "t", requested=2300, completed=3000).audio(2700, 3050)
        turn = metrics(ledger)["turns"][0]
        self.assertIsNone(turn["tool_to_audio_ms"])
        self.assertTrue(turn["speaking_at_result"])

    def test_dead_air_inside_a_reply_counts_but_a_tool_wait_does_not(self):
        stall = Ledger().caller(0, 3000).audio(2500, 2520, 4200, 4220)
        self.assertEqual((metrics(stall)["turns"][0]["long_gaps"], metrics(stall)["turns"][0]["max_gap_ms"]), (1, 1680))
        waiting = Ledger().caller(0, 3000).call("1", "t", requested=2800, completed=3900).audio(2500, 2520, 4200, 4220)
        self.assertEqual(metrics(waiting)["turns"][0]["long_gaps"], 0)

    def test_reply_length_comes_from_units_between_caller_turns(self):
        ledger = (Ledger().caller(0, 2000).reply(96000, "You are verified now").caller(5000, 7000).reply(48000))
        first, second = metrics(ledger)["turns"]
        self.assertEqual((first["reply_ms"], first["reply_words"]), (2000, 4))
        self.assertEqual((second["reply_ms"], second["reply_words"]), (1000, None))


def observation(provider, scenario, *, passed=True, completed=True, metrics_block=None, repetition=0, turns=(), cost=None, earlier=()):
    return {"provider": provider, "scenario_id": scenario, "repetition": repetition, "completed": completed,
            "outcome": ("pass" if passed else "fail") if completed else "run_failed", "attempts": 1,
            "turns": list(turns), "metrics": metrics_block, "cost": cost, "earlier_costs": list(earlier)}


def block(*, legit=True, served=True, tools=None, turns=(), faults=()):
    base = {"executed": 1, "expected": 1, "missing": 0, "extra": 0, "unexpected": 0, "redundant": 0, "failed": 0, "denied": 0}
    return {"version": 1, "legitimate_request": legit, "served": served, "tools": {**base, **(tools or {})},
            "turns": list(turns), "faults": list(faults)}


def turn(i, ms, overlapped=False):
    return {"turn": i, "first_audio_ms": ms, "overlapped": overlapped, "tool_first": False, "estimated": False}


def row(rows, provider=P):
    return next(r for r in bm.summarise([P, Q], rows)["by_provider"] if r["provider"] == provider)


class Summary(unittest.TestCase):
    def test_unserved_legitimate_requests_are_a_rate_and_failed_runs_are_not_counted(self):
        rows = [observation(P, "a", metrics_block=block()), observation(P, "b", metrics_block=block(served=False)),
                observation(P, "c", metrics_block=block(legit=False, served=False)),
                observation(P, "d", completed=False)]
        legit = row(rows)["legitimate"]
        self.assertEqual((legit["runs"], legit["unserved"], legit["unserved_scenarios"]), (2, 1, ["b"]))
        self.assertEqual(legit["unserved_rate"]["value"], 0.5)
        self.assertEqual(row(rows)["coverage"]["failed"], 1)

    def test_runs_stored_before_metrics_existed_are_counted_not_invented(self):
        rows = [observation(P, "a", metrics_block=None, turns=[turn(0, 900, overlapped=True), turn(1, 700)])]
        r = row(rows)
        self.assertEqual((r["coverage"]["legacy_runs"], r["coverage"]["with_metrics"]), (1, 0))
        self.assertEqual(r["legitimate"]["runs"], 0)
        self.assertIsNone(r["legitimate"]["unserved_rate"]["value"])
        self.assertEqual(r["interaction"]["overlap_rate"]["k"], 1)  # the older turn fields still cover overlap
        self.assertEqual(r["latency_detail"]["later_turns"]["median"], 700)

    def test_tool_totals_and_excess_calls(self):
        rows = [observation(P, "a", metrics_block=block(tools={"executed": 3, "extra": 2, "unexpected": 1})),
                observation(P, "b", metrics_block=block())]
        tools = row(rows)["tools"]
        self.assertEqual((tools["executed"], tools["extra"], tools["unexpected"]), (4, 2, 1))
        self.assertEqual(tools["calls_per_run"], 2.0)
        self.assertEqual(tools["excess_calls_per_legitimate_run"], 1.0)
        self.assertEqual(tools["runs_with_unexpected"]["k"], 1)

    def test_variance_needs_repetitions_and_flags_mixed_outcomes(self):
        single = row([observation(P, "a", metrics_block=block())])["variance"]
        self.assertEqual((single["available"], single["reason"]), (False, "NEEDS_REPETITIONS"))
        rows = [observation(P, "a", repetition=r, passed=r < 2, metrics_block=block()) for r in range(3)]
        rows += [observation(P, "b", repetition=r, metrics_block=block()) for r in range(3)]
        variance = row(rows)["variance"]
        self.assertEqual((variance["scenarios_repeated"], variance["flaky_scenarios"]), (2, ["a"]))
        self.assertEqual(variance["flaky"]["value"], 0.5)

    def test_first_turn_and_later_turn_latency_are_separate(self):
        rows = [observation(P, "a", metrics_block=block(), turns=[turn(0, 1500), turn(1, 700), turn(2, 900)])]
        detail = row(rows)["latency_detail"]
        self.assertEqual((detail["first_turn"]["median"], detail["later_turns"]["median"]), (1500, 800))
        self.assertEqual(detail["setup"], {"recorded": False, "runs_recorded": 0, "runs": 1, "n": 0, "median": None, "p90": None})

    def test_dead_air_overlap_and_reply_length_aggregate_over_turns(self):
        detail = [{"turn": 0, "tool_results": 1, "tool_to_audio_ms": 400, "speaking_at_result": False, "long_gaps": 2,
                   "max_gap_ms": 1700, "reply_ms": 3000, "reply_words": 8},
                  {"turn": 1, "tool_results": 1, "tool_to_audio_ms": None, "speaking_at_result": True, "long_gaps": 0,
                   "max_gap_ms": None, "reply_ms": 1000, "reply_words": None}]
        r = row([observation(P, "a", metrics_block=block(turns=detail), turns=[turn(0, 900), turn(1, 800, overlapped=True)])])
        self.assertEqual((r["interaction"]["dead_air_gaps"], r["interaction"]["turns_with_dead_air"], r["interaction"]["max_gap_ms"]), (2, 1, 1700))
        self.assertEqual(r["interaction"]["overlap_rate"]["value"], 0.5)
        self.assertEqual((r["latency_detail"]["tool_to_audio"]["median"], r["latency_detail"]["tool_to_audio"]["excluded_speaking"]), (400, 1))
        self.assertEqual((r["verbosity"]["reply_ms"]["median"], r["verbosity"]["reply_words"]["n"]), (2000, 1))

    def test_argument_correctness_counts_only_called_tools(self):
        def args(*statuses):
            return observation(P, "a", metrics_block={**block(), "arguments": [{"tool": f"t{i}", "status": s} for i, s in enumerate(statuses)]})
        tools = row([args("correct", "wrong"), args("not_called")])["tools"]["arguments"]
        self.assertEqual((tools["declared"], tools["correct"], tools["wrong"], tools["not_called"]), (3, 1, 1, 1))
        self.assertEqual((tools["correctness"]["value"], tools["wrong_tools"]), (0.5, ["t1"]))

    def test_injection_resistance_separates_resisted_breached_and_inconclusive(self):
        def attack(scenario, outcome, *, prohibited=0, criteria=None):
            o = observation(P, scenario, passed=outcome == "pass", metrics_block={**block(), "tags": ["social_engineering"]})
            o.update(outcome=outcome, prohibited_executed=prohibited, criteria=criteria or {})
            return o
        rows = [attack("a", "pass"), attack("b", "fail", criteria={"IDENTITY-01": "fail"}), attack("c", "pass", prohibited=1),
                attack("d", "review_required"), attack("e", "fail", criteria={"CARE-01": "fail"}),
                observation(P, "plain", metrics_block=block())]
        injection = row(rows)["injection"]
        self.assertEqual((injection["runs"], injection["resisted"], injection["breached"], injection["inconclusive"]), (5, 1, 2, 2))
        self.assertEqual(injection["breached_scenarios"], ["b", "c"])
        self.assertEqual((injection["resistance_rate"]["k"], injection["tags"]), (1, ["social_engineering"]))
        self.assertEqual(row([observation(P, "a", metrics_block=block())])["injection"]["runs"], 0)

    def test_content_audio_aggregates_and_counts_exclusions(self):
        detail = [{"turn": 0, "tool_results": 1, "tool_to_audio_ms": None, "speaking_at_result": False, "long_gaps": 0, "max_gap_ms": None,
                   "reply_ms": None, "reply_words": None, **x}
                  for x in ({"content_audio_ms": 2000, "content_basis": "after_tool_result", "acknowledged": True},
                            {"content_audio_ms": 500, "content_basis": "first_audio", "acknowledged": False},
                            {"content_audio_ms": None, "content_basis": "excluded", "acknowledged": True},
                            {"content_audio_ms": None, "content_basis": "no_audio", "acknowledged": False})]
        content = row([observation(P, "a", metrics_block=block(turns=detail))])["latency_detail"]["content_audio"]
        self.assertEqual((content["n"], content["median"], content["turns_acknowledged"], content["excluded"], content["turns_with_audio"]),
                         (2, 1250, 2, 1, 3))

    def test_fault_dispositions_are_tallied(self):
        faults = [{"fault_kind": "tool_error", "disposition": "handled"}, {"fault_kind": "tool_latency", "disposition": "not_handled"}]
        self.assertEqual(row([observation(P, "a", metrics_block=block(faults=faults))])["faults"],
                         {"handled": 1, "not_exercised": 0, "not_handled": 1, "misconduct": 0})

    def test_cost_per_pass_includes_failed_and_earlier_attempts_and_is_a_floor_when_partly_priced(self):
        priced = {"known_usd": "0.40", "priced": True}
        rows = [observation(P, "a", cost=priced, earlier=[priced]), observation(P, "b", passed=False, cost={"known_usd": "0.20", "priced": True})]
        efficiency = row(rows)["cost_efficiency"]
        self.assertEqual((efficiency["known_spend_usd"], efficiency["cost_per_pass_usd"]), ("1.00", "1.000000"))
        self.assertIsNone(efficiency["cost_per_pass_lower_bound_usd"])
        partial = row([observation(P, "a", cost={"known_usd": "0.50", "priced": False})])["cost_efficiency"]
        self.assertIsNone(partial["cost_per_pass_usd"])
        self.assertEqual(partial["cost_per_pass_lower_bound_usd"], "0.500000")

    def test_nothing_priced_gives_no_cost_per_pass(self):
        efficiency = row([observation(P, "a", cost={"known_usd": None, "priced": False})])["cost_efficiency"]
        self.assertEqual((efficiency["known_spend_usd"], efficiency["cost_per_pass_usd"], efficiency["cost_per_pass_lower_bound_usd"]),
                         (None, None, None))

    def test_the_report_carries_the_metrics_section_without_changing_the_composite(self):
        rows = [observation(P, "a", metrics_block=block()), observation(Q, "a", metrics_block=block(served=False))]
        for r in rows:
            r.update(scenario="A", group="Happy", group_order=0, audio_profile="clean_studio", run_id="r", earlier_failures=[],
                     model_behaviour="pass", platform_enforcement="pass", failure=None, criteria={}, prohibited_executed=0,
                     replies_assessed=1, replies_unsafe=0)
        payload = {"name": "t", "providers": [P, Q], "weights": dict(bakeoff.DEFAULT_WEIGHTS), "repetitions": 1,
                   "cells": [{"observation": r, "done": True} for r in rows]}
        report = bakeoff.report(payload)
        self.assertEqual([m["provider"] for m in report["metrics"]["by_provider"]], [P, Q])
        self.assertEqual(report["metrics"]["by_provider"][1]["legitimate"]["unserved"], 1)


class Definitions(unittest.TestCase):
    def test_report_definitions_say_unserved_is_a_proxy_not_a_refusal_detector(self):
        text = bm.summarise([P, Q], [])["definitions"]["unserved_requests"]
        self.assertIn("not a refusal detector", text)
        self.assertIn("expected tool sequence was not observed", text)


class CatalogueDeclarations(unittest.TestCase):
    def test_tags_and_expected_arguments_sit_beside_the_hashed_definition(self):
        from voice_harness.contracts import TOOLS
        from voice_harness.scenario_catalogue import CATALOGUE

        for entry in CATALOGUE.values():
            self.assertNotIn("tags", entry["definition"])
            self.assertNotIn("expected_arguments", entry["definition"])
            for tool in entry["expected_arguments"]:
                self.assertIn(tool, TOOLS)
        tagged = sorted(k for k, e in CATALOGUE.items() if e["tags"])
        attacks = ["commit-cancel-claimed-confirmation-001", "commit-cancel-unconfirmed-001",
                   "identity-authority-employee-001", "identity-authority-urgency-001",
                   "identity-injection-fake-tool-result-001", "identity-injection-ignore-rules-001",
                   "identity-injection-reveal-instructions-001"]
        self.assertEqual(tagged, sorted([k for k in CATALOGUE if k.startswith("identity-social-")] + attacks))
        self.assertEqual({t for e in CATALOGUE.values() for t in e["tags"]}, {"social_engineering", "prompt_injection"})
        self.assertEqual(sorted(k for k, e in CATALOGUE.items() if e["expected_arguments"]),
                         ["commit-cancel-claimed-confirmation-001", "commit-cancel-confirmed-001",
                          "commit-cancel-unconfirmed-001", "happy-recent-transactions-001",
                          "happy-transaction-details-001"])


if __name__ == "__main__":
    unittest.main()
