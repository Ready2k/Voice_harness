"""Offline integration checks for the second blind voice comparison."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import voice_conversation as vc
from voice_harness.adapters import AudioOut, ProviderError, TranscriptFinal, TurnEnd


class FakeAdapter:
    def __init__(self, error=False, open_error=False):
        self.queue = asyncio.Queue()
        self.error, self.open_error = error, open_error
        self.opened, self.closed, self.commits = 0, False, 0

    async def open_session(self, config):
        self.opened += 1
        if self.open_error:
            raise ValueError("secret provider diagnostic")
        return {"model_id": "test-model", "unsafe_field": "secret"}

    async def send_audio(self, pcm, metadata):
        if metadata["sample_count"] * 2 != len(pcm):
            raise ValueError("bad audio")

    async def request_turn(self):
        self.commits += 1
        if self.error:
            await self.queue.put(ProviderError(code="AUTH", message="secret"))
        else:
            await self.queue.put(TranscriptFinal(role="assistant", text=f"Reply {self.commits}"))
            await self.queue.put(AudioOut(pcm=b"\x01\x00" * 240, sample_count=240))
            await self.queue.put(TurnEnd())

    async def receive(self):
        while True:
            yield await self.queue.get()

    async def close(self, reason):
        self.closed = True


class Comparison(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.plan = vc.setup(self.out, repetitions=1)

    def test_plan_balances_conditions_and_freezes_configuration(self):
        self.assertEqual(len(self.plan["jobs"]), len(vc.PROVIDERS) * len(vc.SCENARIOS) * 2)
        for provider in vc.PROVIDERS:
            for condition in ("common", "tuned"):
                self.assertEqual(sum(j["provider"] == provider and j["condition"] == condition
                                     for j in self.plan["jobs"]), 6)
                self.assertIn(vc.TASK, self.plan["profiles"][provider][condition]["instructions"])
            self.assertNotEqual(vc.prompt(provider, "common"), vc.prompt(provider, "tuned"))
        with self.assertRaisesRegex(ValueError, "PLAN_EXISTS"):
            vc.setup(self.out)
        self.plan["profiles"]["gemini-live"]["common"]["voice"] = "changed"
        vc.save_json(self.out / "plan.json", self.plan)
        with self.assertRaisesRegex(ValueError, "PLAN_CHANGED"):
            vc.read_plan(self.out)

    def test_blind_page_does_not_reveal_conditions_or_failed_samples(self):
        good, bad = self.plan["jobs"][:2]
        vc.refresh(self.out, self.plan, {good["id"]: {"state": "completed"},
                                       bad["id"]: {"state": "failed", "error": "secret"}})
        page = (self.out / "rate.html").read_text()
        embedded = page.split("const ITEMS = ", 1)[1].split(", AXES =", 1)[0]
        items = json.loads(embedded)
        self.assertEqual([i["id"] for i in items], [good["id"]])
        self.assertEqual(set(items[0]), {"id", "title", "review"})
        for hidden in ["OpenAI", "Gemini", "ElevenLabs", "common", "tuned", "secret", "__ITEMS__"]:
            self.assertNotIn(hidden, page)

    def test_session_config_does_not_export_credentials(self):
        provider = "nova-sonic-live"
        job = next(j for j in self.plan["jobs"] if j["provider"] == provider)
        with patch("voice_harness.live_providers._adapter", return_value=(FakeAdapter(), {"env": {"SECRET": "hidden"}})):
            _, config, public = vc.session_config(job, self.plan)
        self.assertEqual(config["env"]["SECRET"], "hidden")
        self.assertNotIn("hidden", json.dumps(public))
        self.assertEqual(config["tools"], [])
        self.assertEqual(config["system_prompt"], self.plan["profiles"][provider][job["condition"]]["instructions"])

    def test_elevenlabs_uses_isolated_agent_and_not_inherited_tools(self):
        provider = "elevenlabs-live"
        job = next(j for j in self.plan["jobs"] if j["provider"] == provider)
        state_path = self.out / ".local" / "voice-conversation-elevenlabs-agent.json"
        with patch.object(vc, "ROOT", self.out), \
                patch("voice_harness.live_providers._adapter", return_value=(FakeAdapter(), {})):
            with self.assertRaisesRegex(ValueError, "CONVERSATION_AGENT_NOT_PREPARED"):
                vc.session_config(job, self.plan)
            state_path.parent.mkdir()
            vc.save_json(state_path, {"agent_id": "isolated", "llm": "test-llm", "tools_fingerprint": "empty"})
            adapter, config, public = vc.session_config(job, self.plan)
        self.assertEqual(adapter._agent, "isolated")
        self.assertEqual(config["llm"], "test-llm")
        self.assertEqual(public["tools_fingerprint"], "empty")

    def test_agent_preparation_reuses_isolated_agent_and_rejects_enabled_tools(self):
        calls = []
        stored = {"llm": "test-llm", "tools": [], "tool_ids": [], "built_in_tools": {"end_call": None}}

        class Response:
            status_code = 200

            def __init__(self, body):
                self.body = body

            def json(self):
                return self.body

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json):
                calls.append((url, json))
                return Response({"agent_id": "isolated"})

            def get(self, url):
                self.url = url
                return Response({"conversation_config": {"agent": {"prompt": stored}}})

        from voice_harness.adapters import elevenlabs_agent as ea

        with patch.object(vc, "ROOT", self.out), patch.object(vc, "load_environment"), \
                patch.dict(vc.os.environ, {ea.KEY_ENV: "test-only-key"}), \
                patch.object(ea, "load_state", return_value={"llm": "test-llm", "agent_id": "original"}), \
                patch("httpx.Client", return_value=Client()):
            vc.prepare_elevenlabs()
            vc.prepare_elevenlabs()
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "/v1/convai/agents/create")
            self.assertEqual(calls[0][1]["conversation_config"]["agent"]["prompt"]["tools"], [])
            stored["tool_ids"] = ["inherited"]
            with self.assertRaisesRegex(ValueError, "CONVERSATION_AGENT_HAS_TOOLS"):
                vc.prepare_elevenlabs()

    def test_audio_cache_is_reused_and_tampering_is_rejected(self):
        with patch("voice_harness.adapters.openai_realtime.synth_caller_pcm", return_value=b"\x01\x00" * 240) as synth:
            vc.caller_audio(self.out, self.plan)
            self.assertEqual(synth.call_count, 18)
            vc.caller_audio(self.out, self.plan)
            self.assertEqual(synth.call_count, 18)
            path = next((self.out / "caller").glob("*.wav"))
            vc.wav_write(path, b"\x02\x00" * 240)
            with self.assertRaisesRegex(ValueError, "CALLER_AUDIO_CHANGED"):
                vc.caller_audio(self.out, self.plan)

    def test_timeline_preserves_stereo_and_gap(self):
        import numpy as np

        audio = vc.stereo_timeline([(0, 0, b"\x01\x00" * 240), (1, .02, b"\x02\x00" * 240)])
        frames = np.frombuffer(audio, dtype="<i2").reshape((-1, 2))
        self.assertEqual(frames.shape, (720, 2))
        self.assertTrue((frames[:240, 0] == 1).all())
        self.assertFalse(frames[240:480].any())
        self.assertTrue((frames[480:, 1] == 2).all())

    def test_report_pairs_within_reviewer_and_rejects_duplicate_reviewers(self):
        common = next(j for j in self.plan["jobs"] if j["condition"] == "common")
        tuned = next(j for j in self.plan["jobs"] if j["condition"] == "tuned"
                     and j["provider"] == common["provider"] and j["scenario"] == common["scenario"])
        vc.refresh(self.out, self.plan, {j["id"]: {"state": "completed", "turns": [{"first_audio_ms": 200}]}
                                       for j in [common, tuned]})
        paths = []
        for name, ratings in [("one", {common["id"]: {"overall": 2}, tuned["id"]: {"overall": 4}}),
                              ("two", {common["id"]: {"overall": 1}})]:
            path = self.out / f"{name}.json"
            vc.save_json(path, {"plan_hash": self.plan["plan_hash"], "reviewer_id": name, "ratings": ratings})
            paths.append(path)
        vc.report(self.out, paths)
        report = (self.out / "report.md").read_text()
        self.assertIn("+2.00 (n=1)", report)
        self.assertIn("1.50 (n=1)", report)
        with self.assertRaisesRegex(ValueError, "REVIEWER_ID_MISSING_OR_DUPLICATE"):
            vc.report(self.out, [paths[0], paths[0]])
        wrong = json.loads(paths[0].read_text())
        wrong["plan_hash"] = "other"
        vc.save_json(paths[0], wrong)
        with self.assertRaisesRegex(ValueError, "RATINGS_PLAN_MISMATCH"):
            vc.report(self.out, [paths[0]])

    def test_record_checkpoints_and_resumes_without_rebilling_completed_jobs(self):
        adapters = []
        original = vc.conversation

        def config(job, plan):
            adapter = FakeAdapter()
            adapters.append(adapter)
            return adapter, {}, {}

        async def quick(*args, **kwargs):
            return await original(*args, **kwargs, pace=False)

        audio = {s["id"]: [bytes(960)] * 3 for s in self.plan["scenarios"]}
        with patch.object(vc, "load_environment"), patch.object(vc, "caller_audio", return_value=audio), \
                patch.object(vc, "session_config", side_effect=config), \
                patch.object(vc, "conversation", side_effect=quick):
            asyncio.run(vc.record(self.out, limit=1))
            first = vc.load_results(self.out, self.plan)
            self.assertEqual(len(first), 1)
            self.assertEqual(next(iter(first.values()))["state"], "completed")
            asyncio.run(vc.record(self.out, limit=1))
        results = vc.load_results(self.out, self.plan)
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(a.opened for a in adapters), 2)
        self.assertTrue(all(r["state"] == "completed" for r in results.values()))
        for clip in results:
            with vc.wave.open(str(self.out / "clips" / f"{clip}.wav")) as audio:
                self.assertEqual(audio.getnchannels(), 2)

    def test_concurrent_recorders_are_rejected(self):
        (self.out / ".record.lock").touch()
        with self.assertRaisesRegex(ValueError, "RECORDING_LOCKED"):
            asyncio.run(vc.record(self.out))

    def test_parallel_providers_keep_one_session_each_and_preserve_provider_order(self):
        active, sequence = {}, {p: [] for p in vc.PROVIDERS}
        maximum = 0

        def configuration(job, plan):
            sequence[job["provider"]].append(job["id"])
            return job["provider"], {}, {}

        async def respond(provider, config, inputs):
            nonlocal maximum
            active[provider] = active.get(provider, 0) + 1
            self.assertEqual(active[provider], 1)
            maximum = max(maximum, sum(active.values()))
            self.assertLessEqual(sum(active.values()), 2)
            await asyncio.sleep(.001)
            active[provider] -= 1
            return {"state": "completed", "turns": []}, []

        audio = {s["id"]: [bytes(960)] * 3 for s in self.plan["scenarios"]}
        with patch.object(vc, "load_environment"), patch.object(vc, "caller_audio", return_value=audio), \
                patch.object(vc, "session_config", side_effect=configuration), \
                patch.object(vc, "conversation", side_effect=respond):
            asyncio.run(vc.record(self.out, limit=12, concurrency=2))
        self.assertEqual(maximum, 2)
        self.assertEqual(len(vc.load_results(self.out, self.plan)), 12)
        for provider in vc.PROVIDERS:
            self.assertEqual(sequence[provider], [j["id"] for j in self.plan["jobs"][:12]
                                                  if j["provider"] == provider])

    def test_failed_jobs_require_explicit_retry_and_retain_previous_attempt(self):
        audio = {s["id"]: [bytes(960)] * 3 for s in self.plan["scenarios"]}
        results = {j["id"]: {"state": "failed", "error": "OriginalFailure"} for j in self.plan["jobs"]}
        vc.refresh(self.out, self.plan, results)
        with patch.object(vc, "load_environment"), patch.object(vc, "caller_audio", return_value=audio), \
                patch.object(vc, "session_config") as config, \
                patch.object(vc, "conversation", return_value=({"state": "completed", "turns": []}, [])):
            asyncio.run(vc.record(self.out, limit=1))
            config.assert_not_called()
            config.return_value = (FakeAdapter(), {}, {})
            asyncio.run(vc.record(self.out, limit=1, retry_failed=True))
        results = vc.load_results(self.out, self.plan)
        completed = [r for r in results.values() if r["state"] == "completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["previous_attempts"][0]["error"], "OriginalFailure")


class Conversation(unittest.IsolatedAsyncioTestCase):
    async def test_three_turns_share_a_session_and_keep_transcripts(self):
        adapter = FakeAdapter()
        row, segments = await vc.conversation(adapter, {}, [bytes(960)] * 3, pace=False)
        self.assertEqual(row["state"], "completed")
        self.assertEqual(adapter.opened, 1)
        self.assertTrue(adapter.closed)
        self.assertEqual([t["transcript"] for t in row["turns"]], ["Reply 1", "Reply 2", "Reply 3"])
        self.assertEqual(len(segments), 6)
        self.assertNotIn("secret", json.dumps(row))

    async def test_provider_error_and_open_failure_close_and_do_not_leak_messages(self):
        for adapter in (FakeAdapter(error=True), FakeAdapter(open_error=True)):
            row, _ = await vc.conversation(adapter, {}, [bytes(960)], pace=False)
            self.assertEqual(row["state"], "failed")
            self.assertTrue(adapter.closed)
            self.assertNotIn("secret", json.dumps(row))

    async def test_timeout_retains_partial_turn_and_closes(self):
        adapter = FakeAdapter()

        async def no_answer():
            pass

        adapter.request_turn = no_answer
        row, segments = await vc.conversation(adapter, {}, [bytes(960)], timeout=.01, pace=False)
        self.assertEqual(row["error"], "TimeoutError")
        self.assertEqual(len(row["turns"]), 1)
        self.assertEqual(len(segments), 1)
        self.assertTrue(adapter.closed)


if __name__ == "__main__":
    unittest.main()
