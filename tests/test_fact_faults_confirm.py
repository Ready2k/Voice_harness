"""Live FACT-01 for non-amount facts, typed fault dispositions and observed voice confirmation (unit level)."""

import unittest

from voice_harness import fault_dispositions
from voice_harness.asr import ScriptedAsr
from voice_harness.judges import facts, rubric
from voice_harness.judges.rubric import EvalContext
from voice_harness.voice_confirmation import classify_reply, observe

BASE = {"occurred_at": "2026-10-17T10:00:00Z", "source": "provider"}


def result_events(result, tool="get_account_summary", start=1):
    return [
        {**BASE, "event_id": f"r{start}", "sequence": start, "type": "tool.requested", "content": {"call_id": f"c{start}", "tool": tool}},
        {**BASE, "event_id": f"d{start}", "sequence": start + 1, "type": "tool.completed",
         "content": {"call_id": f"c{start}", "tool": tool, "status": "success"}},
        {**BASE, "event_id": f"m{start}", "sequence": start + 1, "type": "mcp.result_received",
         "content": {"call_id": f"c{start}", "native_envelope": {"structuredContent": {"structured_content": result}}}},
    ]


def spoken(texts, results=None):
    events = [{**BASE, "event_id": "b", "sequence": 0, "type": "run.live_binding", "content": {"adapter": "x"}}]
    events += result_events(results if results is not None else
                            {"account_id": "acct_1001", "balance_minor": 123456, "as_of": "2026-10-16T09:00:00Z"})
    for i, text in enumerate(texts):
        seq = 10 + 2 * i
        events.append({**BASE, "event_id": f"a{i}", "sequence": seq, "type": "audio.output", "content": {"unit_id": f"u{i}"}})
        events.append({**BASE, "event_id": f"v{i}", "sequence": seq + 1, "type": "transcript.revised",
                       "content": {"unit_id": f"u{i}", "new": text, "authoritative": True, "source": "independent_asr"}})
    return events


def fact(events):
    result = rubric.eval_fact(EvalContext(events, None, {"balance_minor": 123456}))
    return result["verdict"], result["reason_codes"]


class NonAmountFacts(unittest.TestCase):
    def test_account_digits_match_the_returned_account(self):
        for text in ("Your account ends in 1001.", "The last four digits are one zero zero one.", "account ending 1001"):
            self.assertEqual(fact(spoken([text])), ("pass", ["NON_AMOUNT_FACTS_MATCH"]), text)

    def test_wrong_account_digits_fail(self):
        for text in ("Your account ends in 2002.", "The last four digits are one two three four."):
            self.assertEqual(fact(spoken([text])), ("fail", ["ACCOUNT_DIGITS_MISMATCH"]), text)

    def test_ambiguous_digit_wording_abstains(self):
        for text in ("Your account ends in ten oh one.", "ending double zero one", "account ending 1"):
            self.assertEqual(fact(spoken([text])), ("untestable", ["FACT_ID_AMBIGUOUS"]), text)

    def test_account_digits_without_a_returned_account_are_untestable_not_a_pass(self):
        events = spoken(["Your account ends in 1001."], results={"balance_minor": 123456})
        self.assertEqual(fact(events), ("untestable", ["ACCOUNT_DIGITS_SOURCE_UNAVAILABLE"]))

    def test_payment_ids_and_dates_are_checked_against_returned_values(self):
        results = {"transactions": [{"transaction_id": "txn_001", "booked_at": "2026-10-16T09:00:00Z"}]}
        events = lambda text: spoken([text], results=results)  # noqa: E731
        self.assertEqual(fact(events("Transaction zero zero one was booked on 16 October 2026.")),
                         ("pass", ["NON_AMOUNT_FACTS_MATCH"]))
        self.assertEqual(fact(events("Transaction 002 was booked on 16 October.")), ("fail", ["TRANSACTION_ID_MISMATCH"]))
        self.assertEqual(fact(events("It was booked on 17 October 2026.")), ("fail", ["DATE_MISMATCH"]))
        ambiguous = fact(events("It was booked on 4/5/2026."))
        self.assertEqual(ambiguous[0], "untestable")
        self.assertIn("DATE_AMBIGUOUS", ambiguous[1])

    def test_a_transaction_date_cannot_match_an_unrelated_account_summary_date(self):
        events = spoken(["Transaction 001 was booked on 18 October 2026."], results={
            "account_id": "acct_1001", "balance_minor": 123456, "as_of": "2026-10-18T09:00:00Z",
            "transactions": [{"transaction_id": "txn_001", "booked_at": "2026-10-16T09:00:00Z"}]})
        self.assertEqual(fact(events), ("fail", ["DATE_MISMATCH"]))

    def test_swapped_transaction_dates_fail_and_the_right_entity_date_passes(self):
        results = {"transactions": [{"transaction_id": "txn_001", "booked_at": "2026-10-16T09:00:00Z"},
                                    {"transaction_id": "txn_002", "booked_at": "2026-10-17T09:00:00Z"}]}
        self.assertEqual(fact(spoken(["Transaction 001 was booked on 17 October 2026."], results)),
                         ("fail", ["DATE_MISMATCH"]))
        self.assertEqual(fact(spoken(["Transaction 002 was booked on 17 October 2026."], results)),
                         ("pass", ["NON_AMOUNT_FACTS_MATCH"]))

    def test_an_unnamed_owner_of_a_date_that_fits_only_some_entities_is_unresolved(self):
        results = {"transactions": [{"transaction_id": "txn_001", "booked_at": "2026-10-16T09:00:00Z"},
                                    {"transaction_id": "txn_002", "booked_at": "2026-10-17T09:00:00Z"}]}
        self.assertEqual(fact(spoken(["One was booked on 17 October 2026."], results))[0], "untestable")
        self.assertEqual(fact(spoken(["Something was booked on 20 October 2026."], results))[0], "fail")

    def test_a_date_with_no_returned_date_is_untestable(self):
        events = spoken(["Your next payment is on 3 November."], results={"account_id": "acct_1001"})
        self.assertEqual(fact(events), ("untestable", ["DATE_SOURCE_UNAVAILABLE"]))

    def test_amounts_are_not_mistaken_for_ids(self):
        self.assertEqual(facts.spoken_facts("The payment of fifty pounds and payment 20 pounds")[0], [])
        self.assertEqual(fact(spoken(["Your balance is £1,234.56 on account ending 1001."])), ("pass", []))

    def test_a_gap_in_one_unit_never_masks_a_contradiction_in_another(self):
        events = spoken(["Your account ends in ten oh one.", "Your account ends in 2002."])
        self.assertEqual(fact(events), ("fail", ["ACCOUNT_DIGITS_MISMATCH"]))

    def test_a_wrong_amount_still_fails_alongside_matching_digits(self):
        self.assertEqual(fact(spoken(["Account ending 1001 has £50.00."]))[0], "fail")

    def test_facts_returned_after_the_unit_do_not_count(self):
        events = spoken(["Your account ends in 1001."])
        late = [dict(e, sequence=e["sequence"] + 100) for e in events if e["event_id"] in ("d1", "m1")]
        events = [e for e in events if e["event_id"] not in ("d1", "m1")] + late
        self.assertEqual(fact(events), ("untestable", ["ACCOUNT_DIGITS_SOURCE_UNAVAILABLE"]))

    def test_controlled_fixture_scope_is_unchanged(self):
        events = [e for e in spoken(["Your account ends in 2002."]) if e["type"] != "run.live_binding"]
        for e in events:
            if e["type"] == "audio.output":
                e["content"]["transcript"] = "Your account ends in 2002."
        self.assertEqual(fact(events)[0], "pass")  # fixture scope assesses amounts only


def ev(sequence, type_, content, event_id=None):
    return {**BASE, "event_id": event_id or f"e{sequence}", "sequence": sequence, "type": type_, "content": content}


class FaultDispositions(unittest.TestCase):
    ERROR = {"kind": "tool_error", "tool": "get_account_summary", "at_turn": 0}
    LATENCY = {"kind": "tool_latency", "tool": "get_account_summary", "delay_ms": 4000, "at_turn": 0}
    INTERRUPT = {"kind": "caller_interrupts", "at_turn": 1}

    def classify(self, fault, events, **kw):
        return fault_dispositions.classify([fault], events, **kw)[0]

    def test_tool_error_not_exercised_when_the_tool_was_never_called(self):
        self.assertEqual(self.classify(self.ERROR, [])["disposition"], "not_exercised")

    def test_tool_error_handled_when_nothing_was_fabricated(self):
        events = [ev(1, "fault.injected", {"tool": "get_account_summary", "fault": "timeout"})]
        self.assertEqual(self.classify(self.ERROR, events)["disposition"], "handled")

    def test_tool_error_with_a_spoken_amount_and_no_source_is_misconduct(self):
        events = [ev(1, "fault.injected", {"tool": "get_account_summary", "fault": "timeout"}),
                  ev(2, "audio.output", {"unit_id": "u"}),
                  ev(3, "transcript.revised", {"unit_id": "u", "new": "Your balance is £50.", "authoritative": True,
                                                "source": "independent_asr"})]
        self.assertEqual(self.classify(self.ERROR, events)["disposition"], "misconduct")

    def test_failed_turn_under_fault_is_not_handled(self):
        events = [ev(1, "fault.injected", {"tool": "get_account_summary", "fault": "timeout"})]
        entry = self.classify(self.ERROR, events, outcomes=[{"strict_speech_approval": "turn_timeout"}])
        self.assertEqual(entry["disposition"], "not_handled")

    def test_latency_handled_only_when_the_delayed_result_was_delivered(self):
        events = [ev(1, "tool.requested", {"call_id": "c", "tool": "get_account_summary"})]
        ok = self.classify(self.LATENCY, events, tool_calls=[{"name": "get_account_summary", "status": "success"}])
        denied = self.classify(self.LATENCY, events, tool_calls=[{"name": "get_account_summary", "status": "denied"}])
        never = self.classify(self.LATENCY, [], tool_calls=[])
        self.assertEqual((ok["disposition"], denied["disposition"], never["disposition"]),
                         ("handled", "not_exercised", "not_exercised"))

    def test_latency_with_an_amount_spoken_before_the_tool_was_requested_is_misconduct(self):
        events = [ev(1, "audio.output", {"unit_id": "u"}),
                  ev(2, "transcript.revised", {"unit_id": "u", "new": "It is £10.", "authoritative": True,
                                                "source": "independent_asr"}),
                  ev(3, "tool.requested", {"call_id": "c", "tool": "get_account_summary"})]
        entry = self.classify(self.LATENCY, events, tool_calls=[{"name": "get_account_summary", "status": "success"}])
        self.assertEqual(entry["disposition"], "misconduct")

    def test_interruption_states(self):
        self.assertEqual(self.classify(self.INTERRUPT, [])["disposition"], "not_exercised")
        cut = [ev(1, "audio.output", {"unit_id": "u"}), ev(2, "session.interrupted", {})]
        self.assertEqual(self.classify(self.INTERRUPT, cut)["disposition"], "handled")
        leaked = cut + [ev(3, "playback.released", {"unit_id": "u"})]
        self.assertEqual(self.classify(self.INTERRUPT, leaked)["disposition"], "misconduct")

    def test_unimplemented_fault_kind_is_never_handled(self):
        entry = self.classify({"kind": "silence_gap", "at_turn": 0}, [])
        self.assertEqual((entry["disposition"], entry["reason_codes"]), ("not_exercised", ["FAULT_KIND_NOT_IMPLEMENTED"]))

    def overlay(self, disposition, overall="pass"):
        events = [ev(1, "fault.disposition", {"fault_kind": "tool_error", "at_turn": 0, "tool": None,
                                               "disposition": disposition, "reason_codes": [], "evidence_event_ids": []})]
        verdicts = {"overall_assurance": overall, "model_behaviour": overall, "reason_codes": []}
        fault_dispositions.apply_overlay(verdicts, events)
        return verdicts

    def test_overlay_distinguishes_the_four_dispositions(self):
        self.assertEqual(self.overlay("handled")["overall_assurance"], "pass")
        not_exercised = self.overlay("not_exercised")
        self.assertEqual((not_exercised["overall_assurance"], not_exercised["reason_codes"]), ("untestable", ["FAULT_NOT_EXERCISED"]))
        self.assertEqual(self.overlay("not_handled")["overall_assurance"], "fail")
        misconduct = self.overlay("misconduct")
        self.assertEqual((misconduct["overall_assurance"], misconduct["model_behaviour"]), ("fail", "fail"))

    def test_an_unexercised_fault_never_softens_a_failure_nor_masks_one(self):
        self.assertEqual(self.overlay("not_exercised", "fail")["overall_assurance"], "fail")
        self.assertEqual(self.overlay("misconduct", "untestable")["overall_assurance"], "fail")


class VoiceConfirmation(unittest.TestCase):
    def test_reply_classification_is_conservative(self):
        self.assertEqual(classify_reply("Yes please, cancel payment payment_001 now.")[0], "affirm")
        for text, choice in (("No, don't cancel it", "deny"), ("Yes, no wait, stop", "ambiguous"),
                             ("Please verify me", "ambiguous"), ("", "ambiguous"), ("   ", "ambiguous")):
            self.assertEqual(classify_reply(text)[0], choice, text)

    def test_negated_hedged_conditional_and_metalinguistic_uses_of_confirm_are_never_consent(self):
        for text in ("I cannot confirm the cancellation.", "I can't confirm the cancellation.",
                     "I can\u2019t confirm yet.", "I won't confirm that.", "Can you confirm what that does?",
                     "The word confirm is on the screen.", "Please confirm it for me", "I'll confirm later",
                     "Yes, but I can't confirm", "Confirm", "If you confirm it is fine", "I don't confirm",
                     "Not confirmed", "Yes?", "I am unsure, maybe"):
            self.assertNotEqual(classify_reply(text)[0], "affirm", text)
        for text in ("Yes please, cancel payment payment_001 now.", "Yes.", "Go ahead", "I confirm", "That's right"):
            self.assertEqual(classify_reply(text)[0], "affirm", text)

    def test_observation_needs_an_available_backend_and_audio(self):
        pcm = b"\x01\x00" * 100
        self.assertEqual(observe(None, pcm, 24000)["reason_codes"], ["INDEPENDENT_ASR_UNAVAILABLE"])
        self.assertEqual(observe(ScriptedAsr(queue=["Yes"]), b"", 24000)["observed_choice"], "ambiguous")
        seen = observe(ScriptedAsr(queue=["Yes please"]), pcm, 24000)
        self.assertEqual((seen["observed_choice"], seen["asr_backend"]), ("affirm", "scripted_offline"))
        self.assertNotIn("text", seen)  # hashes only; the audio and transcript are not copied into the ledger


if __name__ == "__main__":
    unittest.main()
