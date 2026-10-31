"""Caller-felt latency splits into the provider's endpointing wait and its response time, where it exposes a signal."""

import unittest

from voice_harness.bakeoff_metrics import endpointing_split


def native(kind, ms):
    return {"native_type": kind, "received_ms": ms}


class EndpointingSplitTests(unittest.TestCase):
    def test_splits_at_the_first_signal_after_speech_end(self):
        natives = [native("input_audio_buffer.speech_stopped", 1700), native("input_audio_buffer.speech_stopped", 5000)]
        self.assertEqual(endpointing_split("xai-voice-live", natives, 1000, 9000, 2300), {"endpointing_ms": 700, "response_ms": 600})

    def test_signal_outside_the_turn_window_or_after_the_audio_is_ignored(self):
        late = [native("userSpeechEnd", 9500)]
        self.assertIsNone(endpointing_split("nova-sonic-live", late, 1000, 9000, 2300))
        self.assertIsNone(endpointing_split("nova-sonic-live", [native("userSpeechEnd", 2500)], 1000, 9000, 2300))

    def test_providers_without_a_signal_report_nothing(self):
        for provider in ("elevenlabs-live", "openai-live", "gemini-live"):
            self.assertIsNone(endpointing_split(provider, [native("userSpeechEnd", 1500)], 1000, 9000, 2300))

    def test_no_first_audio_gives_no_split(self):
        self.assertIsNone(endpointing_split("nova-sonic-live", [native("userSpeechEnd", 1500)], 1000, 9000, None))


if __name__ == "__main__":
    unittest.main()
