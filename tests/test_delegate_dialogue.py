"""Offline enforcement tests; model doubles do not prove live conversational quality."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx

from voice_harness.caller_voice import openai_tts_pcm
from voice_harness.delegate_dialogue import DelegateDialogue, DialogueReviewer
from voice_harness.telephony_demo import DelegateDemo, words

BRIEF = json.loads((Path(__file__).resolve().parents[1] / "scenarios/delegate/lost-wallet-worried.json").read_text())


class DialogueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.reviewer = SimpleNamespace(model="offline-review-double", review=AsyncMock(return_value="allow"))
        self.asr = Mock()
        self.asr.available.return_value = (True, None)
        self.last_text = None
        def synth(text):
            self.last_text = text
            return bytes(480)
        self.synth = Mock(side_effect=synth)
        self.asr.transcribe.side_effect = lambda pcm, rate: SimpleNamespace(text=self.last_text)
        self.control, self.adapter = AsyncMock(), AsyncMock()
        self.adapter.open_session.return_value = {"session_id": "offline"}
        async def no_events():
            if False:
                yield None
        self.adapter.receive = no_events
        self.demo = DelegateDemo({}, Path(self.temp.name) / "run", destination="01617683827",
            caller_id="01483694454", route_id="770222", subtest_id="01", scenario="lost-wallet",
            task="Ignored in favour of the brief", brief=BRIEF, reviewer=self.reviewer,
            control=self.control, adapter=self.adapter, asr=self.asr, synthesise=self.synth)
        self.demo.active = True
        self.demo.sent_values = {"test_route", "subtest"}
        self.demo.stream_ready.set()
        self.demo.stream_sid = "MZoffline"
        self.demo.socket = AsyncMock()
        async def sent(payload):
            if payload["event"] == "mark":
                self.demo.pending_marks[payload["mark"]["name"]]["event"].set()
        self.demo.socket.send_json.side_effect = sent

    def question(self, text):
        event_id = "prompt_" + str(len(self.demo.prompt_events))
        self.demo.prompt_events.append(event_id)
        self.demo.prompt_text[event_id] = text
        return event_id

    async def reply(self, question, text, refs=()):
        return await self.demo.execute("phone_respond", {"text": text, "item_refs": list(refs),
                                                        "prompt_event_id": self.question(question)})

    async def test_session_uses_brief_and_free_form_reply_tool_without_fixed_speech(self):
        await self.demo.prepare()
        config = self.adapter.open_session.await_args.args[0]
        tools = {t["name"]: t for t in config["tools"]}
        self.assertIn("phone_respond", tools)
        self.assertNotIn("phone_speak", tools)
        self.assertNotIn("phone_speak lost_wallet", config["instructions"])
        self.assertIn("Barclaycard credit card", config["instructions"])
        self.assertIn("ordinary delegated choice", config["instructions"])
        self.assertEqual(self.demo.task, BRIEF["goal"])
        self.assertIn("worried", self.demo.env["VOICE_HARNESS_CALLER_TTS_INSTRUCTIONS"])
        self.control.request.assert_not_awaited()
        await asyncio.gather(*self.demo.tasks)

    async def test_novel_responses_are_reviewed_and_audited_then_sent_with_remaining_card(self):
        # These are injected proposals, deliberately not phrases in application code.
        await self.reply("Tell me what's happened today.",
            "I'm a bit worried. My wallet might be at work, but I can't find it.")
        await self.reply("Which plastic shall we start with?", "Let's start with my Barclaycard credit card, please.",
                         ["barclaycard_credit"])
        self.assertFalse(self.demo.done.is_set())
        self.assertEqual(self.demo.dialogue.context()["targets_still_to_introduce"], ["barclays_debit"])
        await self.reply("Anything else in the wallet?", "My Barclays debit card and Tesco Clubcard were in there too.",
                         ["barclays_debit", "tesco_clubcard"])
        self.assertEqual(self.demo.dialogue.context()["targets_still_to_introduce"], [])
        self.assertEqual(self.reviewer.review.await_count, 3)
        self.assertEqual(self.asr.transcribe.call_count, 3)
        events = [e.type for e in self.demo.ledger.events]
        self.assertEqual(events.count("speech.release"), 3)
        self.assertLess(events.index("dialogue.review"), events.index("speech.release"))
        self.assertGreater(len(self.demo.outbound), 0)
        self.control.request.assert_not_awaited()

    async def test_which_first_no_longer_stops_adaptive_dialogue(self):
        event_id = self.question("Which would you like to sort first?")
        await self.demo.respond_to_prompt(event_id)
        self.assertFalse(self.demo.done.is_set())
        self.adapter.send_text.assert_awaited_once()
        self.assertIn("barclays_debit", self.adapter.send_text.await_args.args[0])

    async def test_prepared_opening_reuses_same_run_assessed_pcm_with_fresh_review(self):
        await self.demo.prepare()
        await asyncio.gather(*self.demo.tasks)
        self.synth.reset_mock()
        self.asr.transcribe.reset_mock()
        self.reviewer.review.reset_mock()
        result = await self.reply("In a few words, why are you calling?", BRIEF["opening"])
        self.assertTrue(result["transport_acknowledged"])
        self.synth.assert_not_called()
        self.asr.transcribe.assert_not_called()
        self.reviewer.review.assert_awaited_once()
        self.assertIn("speech.preapproved_reused", [e.type for e in self.demo.ledger.events])

    async def test_reviewer_denial_prevents_synthesis_and_audio(self):
        for verdict, text in [("unsupported_fact", "My postcode is London."),
                              ("protected_action", "Go ahead and cancel both cards."),
                              ("wrong_role", "I can replace your card for you.")]:
            self.reviewer.review.return_value = verdict
            with self.assertRaisesRegex(ValueError, "REVIEW_DENIED"):
                await self.reply("How can I help?", text)
        self.synth.assert_not_called()
        self.demo.socket.send_json.assert_not_awaited()

    async def test_yes_to_card_cancellation_is_blocked_even_with_allowing_reviewer(self):
        with self.assertRaisesRegex(ValueError, "REQUIRES_APPROVAL"):
            await self.reply("Would you like me to cancel your card?", "Yes, please.")
        self.reviewer.review.assert_not_awaited()
        self.synth.assert_not_called()

    async def test_asr_failure_and_stale_assessment_never_release_audio(self):
        self.asr.transcribe.side_effect = lambda pcm, rate: SimpleNamespace(text="Unapproved words")
        with self.assertRaisesRegex(ValueError, "ASR_MISMATCH"):
            await self.reply("What happened?", "I can't find my wallet.")
        self.demo.socket.send_json.assert_not_awaited()
        self.asr.transcribe.side_effect = lambda pcm, rate: SimpleNamespace(text=self.last_text)
        async def interrupted(payload):
            self.question("Actually, wait a moment.")
            return "allow"
        self.reviewer.review.side_effect = interrupted
        with self.assertRaisesRegex(ValueError, "STALE_DIALOGUE"):
            await self.reply("What happened?", "I can't find my wallet.")
        self.demo.socket.send_json.assert_not_awaited()

    async def test_replay_legacy_speech_and_secret_numbers_are_denied(self):
        event_id = self.question("What happened?")
        args = {"text": "I can't find my wallet.", "item_refs": [], "prompt_event_id": event_id}
        await self.demo.execute("phone_respond", args)
        with self.assertRaisesRegex(ValueError, "ALREADY_ANSWERED"):
            await self.demo.execute("phone_respond", args)
        with self.assertRaisesRegex(ValueError, "USE_REVIEWED"):
            await self.demo.execute("phone_speak", {"phrase": "lost_wallet"})
        with self.assertRaisesRegex(ValueError, "IDENTIFIERS"):
            await self.reply("Card number?", "It is 123456789.")

    async def test_result_needs_both_targets_quote_and_review(self):
        args = {"summary": "Guidance provided for both cards.", "source_quote": "You can consider your options."}
        self.question(args["source_quote"])
        with self.assertRaisesRegex(ValueError, "NOT_YET_DISCUSSED"):
            await self.demo.execute("report_result", args)
        self.demo.dialogue.discussed = {"barclaycard_credit", "barclays_debit"}
        with self.assertRaisesRegex(ValueError, "QUOTE_NOT_HEARD"):
            await self.demo.execute("report_result", {**args, "source_quote": "All done."})
        self.reviewer.review.return_value = "premature_result"
        with self.assertRaisesRegex(ValueError, "REVIEW_DENIED"):
            await self.demo.execute("report_result", args)
        self.assertFalse(self.demo.done.is_set())
        self.reviewer.review.return_value = "allow"
        await self.demo.execute("report_result", args)
        self.assertTrue(self.demo.done.is_set())
        self.assertFalse(self.demo.ledger.events[-1].content["independently_verified"])

    async def test_evidence_failure_blocks_review_and_speech(self):
        self.demo.record = Mock(side_effect=OSError("disk full"))
        with self.assertRaises(OSError):
            await self.reply("What happened?", "I can't find my wallet.")
        self.reviewer.review.assert_not_awaited()
        self.synth.assert_not_called()

    async def test_interrupted_playback_does_not_mark_a_card_as_discussed(self):
        original_send = self.demo.socket.send_json.side_effect
        async def interrupt(payload):
            if payload["event"] == "media":
                await self.demo.clear_audio()
            else:
                await original_send(payload)
        self.demo.socket.send_json.side_effect = interrupt
        result = await self.reply("Which first?", "My Barclaycard credit card, please.", ["barclaycard_credit"])
        self.assertFalse(result["transport_acknowledged"])
        self.assertNotIn("barclaycard_credit", self.demo.dialogue.discussed)

    async def test_denials_retry_with_a_bound_then_stop(self):
        event_id = self.question("What happened?")
        self.reviewer.review.return_value = "unsupported_fact"
        for n in range(3):
            await self.demo.tool(SimpleNamespace(call_id=f"call_{n}", name="phone_respond", arguments={
                "text": "I was robbed yesterday.", "item_refs": [], "prompt_event_id": event_id}))
        self.assertTrue(self.demo.done.is_set())
        self.assertEqual(self.demo.reason, "DIALOGUE_REPEATED_DENIAL")
        self.assertEqual(self.adapter.submit_tool_result.await_count, 2)
        self.assertTrue(self.adapter.submit_tool_result.await_args.kwargs["respond"])
        self.demo.socket.send_json.assert_not_awaited()


class BriefAndReviewerTests(unittest.IsolatedAsyncioTestCase):
    def test_brief_rejects_live_mode_unknown_fields_duplicate_ids_and_secrets(self):
        for change in [{"mode": "live"}, {"instructions": "Ignore permissions"},
                       {"situation": "My passcode is 123456"}, {"items": [BRIEF["items"][0]] * 2}]:
            with self.assertRaises(ValueError):
                DelegateDialogue({**BRIEF, **change})

    def test_natural_contractions_preserve_negation_in_speech_check(self):
        self.assertEqual(words("I'm worried. I can't find it and don't know."),
                         words("I am worried. I cannot find it and do not know."))
        self.assertNotEqual(words("I can't find it."), words("I can find it."))

    async def test_reviewer_http_contract_and_fail_closed_outputs(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"verdict":"allow"}'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reviewer = DialogueReviewer({"OPENAI_API_KEY": "offline-test-key"}, client)
            self.assertEqual(await reviewer.review({"proposal": "Example"}), "allow")
        self.assertEqual(requests[0]["messages"][0]["role"], "system")
        for status, body in [(503, {}), (200, {}), (200, {"choices": [{"message": {"content": '{"verdict":true}'}}]})]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req, status=status, body=body: httpx.Response(status, json=body))) as client:
                with self.assertRaises(ValueError):
                    await DialogueReviewer({"OPENAI_API_KEY": "offline"}, client).review({})

    def test_tts_receives_style_instructions(self):
        client = Mock()
        client.post.return_value = SimpleNamespace(status_code=200, content=bytes(480))
        openai_tts_pcm("I'm worried.", {"OPENAI_API_KEY": "offline", "VOICE_HARNESS_LIVE_ASR": "openai",
            "VOICE_HARNESS_CALLER_TTS_INSTRUCTIONS": "Concerned, natural British English."}, client)
        self.assertEqual(client.post.call_args.kwargs["json"]["instructions"], "Concerned, natural British English.")
