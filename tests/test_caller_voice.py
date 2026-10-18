"""Caller-voice synthesiser selection, fail-closed gating and offline OpenAI TTS path (no real HTTP)."""
import unittest
from unittest import mock

from voice_harness import caller_voice as cv
from voice_harness import live_providers
from voice_harness.adapters import openai_realtime as m

LIVE = {"VOICE_HARNESS_LIVE_ASR": "openai", "OPENAI_API_KEY": "sk-test-secret"}


class FakeClient:
    def __init__(self, status=200, content=b"\x01\x00" * 100):
        self.status, self.content, self.calls = status, content, []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        return mock.Mock(status_code=self.status, content=self.content)


class CallerVoiceTests(unittest.TestCase):
    def test_default_macos_only_when_present(self):
        with mock.patch.object(cv, "_has_macos", return_value=True):
            self.assertEqual(cv.backend({}), ("macos", None))
        with mock.patch.object(cv, "_has_macos", return_value=False):
            self.assertEqual(cv.backend({}), (None, "NO_CALLER_VOICE_SYNTH"))

    def test_tts_is_opt_in_and_fails_closed(self):
        with mock.patch.object(cv, "_has_macos", return_value=False):
            self.assertEqual(cv.backend(LIVE)[1], "NO_CALLER_VOICE_SYNTH")
        self.assertEqual(cv.backend({cv.ENV: "openai_tts"}), (None, "CALLER_VOICE_TTS_NOT_CONFIGURED"))
        self.assertEqual(cv.backend({cv.ENV: "openai_tts", "OPENAI_API_KEY": "k"})[1], "CALLER_VOICE_TTS_NOT_CONFIGURED")
        self.assertEqual(cv.backend({cv.ENV: "openai_tts", **LIVE}), ("openai_tts", None))
        self.assertEqual(cv.backend({cv.ENV: "bogus"}), (None, "CALLER_VOICE_UNKNOWN_BACKEND"))

    def test_availability_reason_and_success(self):
        env = {**LIVE, cv.ENV: "openai_tts"}
        with mock.patch.object(cv, "_has_macos", return_value=False):
            self.assertEqual(live_providers.availability("openai-realtime-live", env), (True, None))
            no_tts = {**LIVE, cv.ENV: "openai_tts", "OPENAI_API_KEY": ""}
            self.assertEqual(live_providers.availability("openai-realtime-live", no_tts)[1], "NO_ASR_BACKEND_CONFIGURED")
            self.assertEqual(live_providers.availability("openai-realtime-live", LIVE)[1], "NO_CALLER_VOICE_SYNTH")

    def test_tts_request_and_pcm(self):
        client = FakeClient(content=b"\x01\x00" * 10 + b"\x09")
        env = {**LIVE, cv.ENV: "openai_tts", "VOICE_HARNESS_CALLER_TTS_VOICE": "verse"}
        pcm = cv.openai_tts_pcm("hello", env, client)
        self.assertEqual(len(pcm), 20)  # odd trailing byte dropped
        url, kw = client.calls[0]
        self.assertEqual(url, cv.OPENAI_TTS_URL)
        self.assertEqual(kw["json"]["response_format"], "pcm")
        self.assertEqual(kw["json"]["voice"], "verse")

    def test_tts_errors_fail_closed(self):
        env = {**LIVE, cv.ENV: "openai_tts"}
        with self.assertRaises(cv.CallerVoiceUnavailable) as ctx:
            cv.openai_tts_pcm("x", env, FakeClient(status=429))
        self.assertIn("429", str(ctx.exception))
        with self.assertRaises(cv.CallerVoiceUnavailable):
            cv.openai_tts_pcm("x", env, FakeClient(content=b""))
        with self.assertRaises(cv.CallerVoiceUnavailable):
            cv.openai_tts_pcm("x", {}, FakeClient())

    def test_synth_caller_pcm_dispatch_pads_and_describe_has_no_secret(self):
        env = {**LIVE, cv.ENV: "openai_tts"}
        with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(
                cv, "openai_tts_pcm", return_value=bytes(480)):
            pcm = m.synth_caller_pcm("hi", lead_ms=100, trail_ms=200)
            self.assertEqual(len(pcm), 4800 + 480 + 9600)
            described = cv.describe()
        self.assertEqual(described["backend"], "openai_tts")
        self.assertNotIn("sk-test-secret", str(described))

    def test_synth_unavailable_raises(self):
        with mock.patch.dict("os.environ", {cv.ENV: "openai_tts"}, clear=True), self.assertRaises(RuntimeError):
            m.synth_caller_pcm("hi")


if __name__ == "__main__":
    unittest.main()
