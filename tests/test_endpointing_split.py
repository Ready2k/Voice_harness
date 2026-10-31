"""Caller-felt latency splits into the provider's endpointing wait and its response time, where it exposes a signal."""

import unittest

from voice_harness.bakeoff_metrics import endpointing_split


def native(kind, ms):
    return {"native_type": kind, "received_ms": ms}


class EndpointingSplitTests(unittest.TestCase):
    def test_splits_at_the_first_signal_after_speech_end(self):
        natives = [native("input_audio_buffer.speech_stopped", 1700), native("input_audio_buffer.speech_stopped", 5000)]
        self.assertEqual(endpointing_split("xai-voice-live", natives, 1000, 9000, 2300), {"endpointing_ms": 700, "response_ms": 600})

    def test_gemini_splits_at_the_activity_end_marker_not_the_start(self):
        natives = [native("voiceActivity.ACTIVITY_START", 1200), native("voiceActivity.ACTIVITY_END", 1900)]
        self.assertEqual(endpointing_split("gemini-live", natives, 1000, 9000, 2000), {"endpointing_ms": 900, "response_ms": 100})
        self.assertIsNone(endpointing_split("gemini-live", [native("voiceActivity", 1900)], 1000, 9000, 2000))

    def test_signal_outside_the_turn_window_or_after_the_audio_is_ignored(self):
        late = [native("userSpeechEnd", 9500)]
        self.assertIsNone(endpointing_split("nova-sonic-live", late, 1000, 9000, 2300))
        self.assertIsNone(endpointing_split("nova-sonic-live", [native("userSpeechEnd", 2500)], 1000, 9000, 2300))

    def test_providers_without_a_signal_report_nothing(self):
        for provider in ("elevenlabs-live", "openai-live", "gemini-live"):
            self.assertIsNone(endpointing_split(provider, [native("userSpeechEnd", 1500)], 1000, 9000, 2300))

    def test_no_first_audio_gives_no_split(self):
        self.assertIsNone(endpointing_split("nova-sonic-live", [native("userSpeechEnd", 1500)], 1000, 9000, None))


class TrimTests(unittest.TestCase):
    def test_trailing_silence_is_dropped_and_the_voice_kept(self):
        from voice_harness.adapters.openai_realtime import trim_trailing_silence

        voice = (b"\xe8\x03" * 24000)  # 1 s of constant amplitude 1000
        self.assertEqual(trim_trailing_silence(voice + bytes(2 * 24000 // 4)), voice)  # 250 ms of silence removed
        self.assertEqual(trim_trailing_silence(bytes(4800)), b"")


if __name__ == "__main__":
    unittest.main()
