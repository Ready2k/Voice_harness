"""Manual (human-driven) live sessions over the page WebSocket; provider traffic is scripted."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_harness import live_providers
from voice_harness.adapters import (
    AudioOut,
    CapabilityUnsupported,
    ToolCall,
    TranscriptDelta,
    TranscriptFinal,
    TurnEnd,
)
from voice_harness.api import create_app
from voice_harness.asr import ScriptedAsr
from voice_harness.manual_session import JOURNEYS, journeys
from voice_harness.storage import Database

ORIGIN = {"origin": "http://localhost:8765"}
REPLY = AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="r", ts_ms=1)


class ConversationAdapter:
    """Answers each caller turn with a scripted response; the event stream stays open until closed."""

    def __init__(self, unsafe=False):
        self.audio, self.texts, self.closed, self.unsafe = [], [], None, unsafe
        self._pending, self._round, self._heard = [], 0, 0

    async def open_session(self, config):
        return {"model_id": "gpt-realtime-2.1", "provider_session_id": "sess_manual"}

    async def close(self, reason):
        self.closed = reason

    async def submit_tool_result(self, call_id, result):
        return {"delivered": True}

    async def interrupt(self, item_id, audio_end_ms):
        self.truncated = (item_id, audio_end_ms)
        return {"item_id": item_id, "generation_cancelled": True, "history_truncation": "acknowledged"}


    async def send_audio(self, data, metadata):
        self.audio.append(len(data))
        self._heard += len(data)
        if self._heard >= 9600:  # 0.2 s of caller speech is one turn
            self._heard = 0
            self._respond("voice")

    async def send_text(self, text, respond=True):
        self.texts.append(text)
        if respond:
            self._respond(text)

    def _respond(self, said):
        self._round += 1
        r = f"r{self._round}"
        reply = lambda text: [AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id=r + "b",  # noqa: E731
                                       item_id="item_" + r, ts_ms=3),
                              TranscriptDelta(role="assistant", text=text, ts_ms=4), TurnEnd(turn_id=r + "b", ts_ms=5)]
        if said == "voice":
            self._pending += [TranscriptFinal(role="user", text="Hello there.", item_id="u" + r, ts_ms=1),
                              *reply("Please verify your identity.")]
        elif "blue" in said:
            self._pending += [ToolCall(call_id="call_v" + r, name="verify_identity",
                                       arguments={"challenge_id": "challenge_001", "answer": "blue"}, ts_ms=1),
                              TurnEnd(turn_id=r, ts_ms=2), *reply("You are verified.")]
        elif self.unsafe:
            self._pending += reply("Your balance is £1,234.56.")
        else:
            self._pending += [ToolCall(call_id="call_s" + r, name="get_account_summary",
                                       arguments={"account_id": "acct_1001"}, ts_ms=1),
                              TurnEnd(turn_id=r, ts_ms=2), *reply("Your balance is £1,234.56.")]

    async def receive(self):
        while self.closed is None:
            if self._pending:
                yield self._pending.pop(0)
            else:
                await asyncio.sleep(0.01)


class NoTruncateAdapter(ConversationAdapter):
    async def interrupt(self, item_id, audio_end_ms):
        raise CapabilityUnsupported("no truncate primitive")


class ManualSessions(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(root / "t.sqlite")))
        self.app = create_app(self.database, root, "operator")
        self.headers = {"Authorization": "Bearer operator"}
        self.key = 0

    def patch(self, adapter, asr):
        for patch in (
            mock.patch.object(live_providers, "availability", lambda provider_id, environ=None, manual=False: (True, None)),
            mock.patch.object(live_providers, "_adapter", lambda provider_id: (adapter, {"tools": []})),
            mock.patch.object(live_providers, "select_backend", lambda: ScriptedAsr(queue=list(asr))),
            mock.patch.object(live_providers, "auto_judges", lambda environ=None: ([], [])),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def create(self, client, journey="balance", path="happy", playback="gated"):
        self.key += 1
        return client.post("/api/v1/manual-sessions", headers={**self.headers, "Idempotency-Key": f"m{self.key}"},
                           json={"provider_config_id": "openai-realtime-live", "journey_id": journey, "path": path,
                                 "playback_mode": playback})

    def connect(self, client, run_id, origin=ORIGIN):
        ticket = client.post(f"/api/v1/runs/{run_id}/ws-ticket", headers=self.headers).json()["ticket"]
        return client.websocket_connect(f"/api/v1/manual-sessions/{run_id}/ws",
                                        subprotocols=["manual.v1", "ticket." + ticket], headers=dict(origin))

    @staticmethod
    def until(ws, predicate, seen=None):
        for _ in range(500):
            message = ws.receive_json()
            if seen is not None:
                seen.append(message)
            if predicate(message):
                return message
        raise AssertionError("message never arrived")

    def events(self, run_id):
        return self.database.events(run_id, 0, 5000)

    def test_journeys_pair_a_happy_and_a_failure_scenario_with_prompts(self):
        items = journeys()
        self.assertEqual([j["journey_id"] for j in items], [j["journey_id"] for j in JOURNEYS])
        for journey in items:
            for path in ("happy", "failure"):
                self.assertTrue(journey[path]["prompts"], journey["journey_id"])
                self.assertTrue(journey[path]["expected_outcome"])
        balance = items[0]
        self.assertIn("blue", balance["happy"]["prompts"][0])
        self.assertEqual(balance["happy"]["expected_tools"], ["verify_identity", "get_account_summary"])
        self.assertIn("get_account_summary", balance["failure"]["prohibited_tools"])

    def test_manual_availability_does_not_need_a_synthetic_caller_voice(self):
        env = {"VOICE_HARNESS_LIVE_ASR": "openai", "OPENAI_API_KEY": "k", "HARNESS_CALLER_VOICE": "openai_tts"}
        with mock.patch("voice_harness.caller_voice.available", lambda environ=None: (False, "NO_CALLER_VOICE")):
            self.assertEqual(live_providers.requirements("openai-realtime-live", env), ["NO_CALLER_VOICE"])
            self.assertEqual(live_providers.requirements("openai-realtime-live", env, manual=True), [])

    def test_journey_list_reports_provider_readiness(self):
        with TestClient(self.app) as client:
            body = client.get("/api/v1/manual/journeys", headers=self.headers).json()
        self.assertEqual(len(body["items"]), len(JOURNEYS))
        self.assertEqual({p["provider_config_id"] for p in body["providers"]}, set(live_providers.LIVE_PROVIDERS))
        self.assertEqual(body["playback_modes"], ["gated", "streamed"])

    def test_unknown_journey_and_unconfigured_provider_are_refused(self):
        with TestClient(self.app) as client:
            unknown = self.create(client, journey="nope")
            with mock.patch.object(live_providers, "availability", lambda p, environ=None, manual=False: (False, "X")):
                missing = self.create(client)
        self.assertEqual(unknown.json()["error"]["code"], "UNKNOWN_JOURNEY")
        self.assertEqual(missing.json()["error"]["code"], "PROVIDER_NOT_CONFIGURED")

    def test_a_gated_text_session_plays_only_released_audio_and_is_assessed(self):
        adapter = ConversationAdapter()
        self.patch(adapter, ["You are verified.", "Your balance is £1,234.56."])
        with TestClient(self.app) as client:
            created = self.create(client)
            self.assertEqual(created.status_code, 201, created.text)
            run = created.json()["run_id"]
            self.assertEqual(created.json()["scenario_id"], "happy-balance-clean-001")
            with self.connect(client, run) as ws:
                self.until(ws, lambda m: m["type"] == "ready")
                ws.send_json({"type": "control", "action": "mode_text"})
                ws.send_json({"type": "text", "text": "The answer to my security question is blue."})
                seen = []
                audio = self.until(ws, lambda m: m["type"] == "audio", seen)
                self.assertEqual(audio["mode"], "gated")
                self.assertTrue(any(m["type"] == "tool" and m["identity"] == "verified" for m in seen))
                self.assertIn("assessing", [m["type"] for m in seen])  # held for assessment before any audio
                ws.send_json({"type": "played", "unit_id": audio["unit_id"], "end_sample": audio["end_sample"]})
                ws.send_json({"type": "text", "text": "What is my account balance?"})
                seen = []
                verdict = self.until(ws, lambda m: m["type"] == "assessment" and m["unit_id"] != audio["unit_id"], seen)
                self.assertEqual(verdict["status"], "approved", verdict)
                second = next(m for m in seen if m["type"] == "audio")
                ws.send_json({"type": "played", "unit_id": second["unit_id"], "end_sample": second["end_sample"]})
                ws.send_json({"type": "stop"})
                ended = self.until(ws, lambda m: m["type"] == "ended")
            assurance = client.get(f"/api/v1/runs/{run}/assurance", headers=self.headers).json()
        types = [e["type"] for e in self.events(run)]
        self.assertEqual(ended["state"], "completed")
        self.assertEqual(adapter.closed, "done")
        for expected in ("manual.session_started", "manual.control", "input.manual_text", "playback.ack",
                         "manual.session_ended", "run.completed"):
            self.assertIn(expected, types)
        self.assertNotIn("playback.streamed_unassessed", types)
        self.assertEqual(types.count("input.manual_text"), 2)
        self.assertEqual(assurance["criteria"]["AUDIO-01"]["verdict"], "pass")
        self.assertEqual(assurance["criteria"]["IDENTITY-01"]["verdict"], "pass")

    def test_streamed_playback_is_recorded_and_never_passes_the_audio_gate(self):
        adapter = ConversationAdapter()
        self.patch(adapter, ["You are verified."])
        with TestClient(self.app) as client:
            run = self.create(client, playback="streamed").json()["run_id"]
            with self.connect(client, run) as ws:
                self.until(ws, lambda m: m["type"] == "ready")
                ws.send_json({"type": "text", "text": "The answer is blue."})
                seen = []
                self.until(ws, lambda m: m["type"] == "assessment", seen)
                audio = [i for i, m in enumerate(seen) if m["type"] == "audio"]
                self.assertTrue(audio and all(seen[i]["mode"] == "streamed" for i in audio))
                ws.send_json({"type": "stop"})
                self.until(ws, lambda m: m["type"] == "ended")
            assurance = client.get(f"/api/v1/runs/{run}/assurance", headers=self.headers).json()
        self.assertIn("playback.streamed_unassessed", [e["type"] for e in self.events(run)])
        audio_check = assurance["criteria"]["AUDIO-01"]
        self.assertEqual(audio_check["verdict"], "untestable")
        self.assertIn("PLAYBACK_GATE_BYPASSED_STREAMED", audio_check["reason_codes"])
        self.assertNotEqual(assurance["overall_assurance"], "pass")

    def test_unsafe_streamed_audio_fails_the_audio_gate(self):
        adapter = ConversationAdapter(unsafe=True)
        self.patch(adapter, ["Your balance is £1,234.56."])
        with TestClient(self.app) as client:
            run = self.create(client, path="failure", playback="streamed").json()["run_id"]
            with self.connect(client, run) as ws:
                self.until(ws, lambda m: m["type"] == "ready")
                ws.send_json({"type": "text", "text": "Read me the balance."})
                held = self.until(ws, lambda m: m["type"] == "assessment")
                self.assertEqual(held["status"], "held")
                ws.send_json({"type": "stop"})
                self.until(ws, lambda m: m["type"] == "ended")
            assurance = client.get(f"/api/v1/runs/{run}/assurance", headers=self.headers).json()
        self.assertEqual(assurance["criteria"]["AUDIO-01"]["verdict"], "fail")
        self.assertIn("UNASSESSED_AUDIO_REACHED_PLAYBACK", assurance["criteria"]["AUDIO-01"]["reason_codes"])

    def test_voice_turns_are_segmented_hashed_and_paused_audio_is_dropped(self):
        adapter = ConversationAdapter()
        self.patch(adapter, ["Please verify your identity."] * 3)
        with TestClient(self.app) as client:
            run = self.create(client).json()["run_id"]
            with self.connect(client, run) as ws:
                self.until(ws, lambda m: m["type"] == "ready")
                ws.send_json({"type": "speech", "phase": "start"})
                for _ in range(5):
                    ws.send_bytes(bytes(1920))  # 40 ms each; reframed into 20 ms chunks
                ws.send_json({"type": "speech", "phase": "end"})
                self.until(ws, lambda m: m["type"] == "turn")
                transcript = self.until(ws, lambda m: m["type"] == "transcript" and m["role"] == "user")
                self.assertEqual(transcript["text"], "Hello there.")
                ws.send_json({"type": "control", "action": "pause"})
                state = self.until(ws, lambda m: m["type"] == "state" and m["paused"])
                self.assertEqual(state["turn"], 1)
                sent = len(adapter.audio)
                ws.send_bytes(bytes(1920))
                ws.send_json({"type": "control", "action": "mute"})
                self.until(ws, lambda m: m["type"] == "state" and m["muted"])
                self.assertEqual(len(adapter.audio), sent)  # nothing reached the provider while paused
                ws.send_json({"type": "text", "text": "hello"})
                self.assertEqual(self.until(ws, lambda m: m["type"] == "error")["code"], "SESSION_PAUSED")
                ws.send_json({"type": "stop"})
                self.until(ws, lambda m: m["type"] == "ended")
        events = self.events(run)
        audio = [e["content"] for e in events if e["type"] == "input.manual_audio"]
        self.assertEqual(len(audio), 1)
        self.assertEqual((audio[0]["turn"], audio[0]["byte_count"], audio[0]["audio_retained"]), (0, 9600, False))
        self.assertTrue(all(size == 960 for size in adapter.audio))
        actions = [e["content"]["action"] for e in events if e["type"] == "manual.control"]
        self.assertEqual(actions, ["pause", "mute"])
        self.assertIn("input.caller_transcript", [e["type"] for e in events])

    def barge_in(self, adapter):
        self.patch(adapter, ["You are verified."])
        with TestClient(self.app) as client:
            run = self.create(client).json()["run_id"]
            with self.connect(client, run) as ws:
                self.until(ws, lambda m: m["type"] == "ready")
                ws.send_json({"type": "text", "text": "The answer is blue."})
                audio = self.until(ws, lambda m: m["type"] == "audio")
                ws.send_json({"type": "barge_in", "unit_id": audio["unit_id"], "end_sample": 240})  # 10 ms heard
                flush = self.until(ws, lambda m: m["type"] == "flush")
                ws.send_json({"type": "stop"})
                self.until(ws, lambda m: m["type"] == "ended")
        events = self.events(run)
        interrupted = [e["content"] for e in events if e["type"] == "session.interrupted"][-1]
        acks = [e["content"] for e in events if e["type"] == "playback.ack"]
        return flush, interrupted, acks, audio

    def test_barge_in_acknowledges_what_was_heard_and_truncates_provider_history(self):
        adapter = ConversationAdapter()
        flush, interrupted, acks, audio = self.barge_in(adapter)
        self.assertEqual(adapter.truncated, ("item_r1", 10))
        self.assertEqual(flush["history_truncation"], "confirmed")
        self.assertEqual(interrupted["interruption_receipt"]["history_truncation"], "confirmed")
        self.assertEqual(interrupted["interruption_receipt"]["history_state"], "reconciled")
        self.assertEqual([(a["unit_id"], a["observed_end_sample"]) for a in acks], [(audio["unit_id"], 240)])

    def test_barge_in_on_a_provider_without_truncation_records_diverged_history(self):
        flush, interrupted, _, _ = self.barge_in(NoTruncateAdapter())
        self.assertEqual(flush["history_truncation"], "unsupported")
        self.assertEqual(interrupted["interruption_receipt"]["history_state"], "history_diverged")

    def test_a_session_accepts_one_page_and_checks_the_origin(self):
        adapter = ConversationAdapter()
        self.patch(adapter, [])
        with TestClient(self.app) as client:
            run = self.create(client).json()["run_id"]
            with self.assertRaises(WebSocketDisconnect), self.connect(client, run, {"origin": "http://evil"}) as ws:
                ws.receive_json()
            with self.connect(client, run) as ws:
                self.until(ws, lambda m: m["type"] == "ready")
                with self.assertRaises(WebSocketDisconnect), self.connect(client, run) as second:
                    second.receive_json()
                ws.send_json({"type": "stop"})
                self.until(ws, lambda m: m["type"] == "ended")

    def test_closing_the_page_ends_and_assesses_the_session(self):
        adapter = ConversationAdapter()
        self.patch(adapter, [])
        with TestClient(self.app) as client:
            run = self.create(client).json()["run_id"]
            with self.connect(client, run) as ws:
                self.until(ws, lambda m: m["type"] == "ready")
            self.app.state.jobs[run].result(timeout=60)
        ended = [e["content"] for e in self.events(run) if e["type"] == "manual.session_ended"]
        self.assertEqual(ended[0]["reason"], "CLIENT_DISCONNECTED")
        self.assertIn(self.database.get(run)["state"], {"completed", "failed"})
        self.assertNotEqual(self.database.get(run)["state"], "running")


if __name__ == "__main__":
    unittest.main()
