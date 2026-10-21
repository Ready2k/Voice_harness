"""Verify chronological alignment, missing intervals and immutable source evidence."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from voice_harness.call_review import (
    make_alignment,
    mix_tracks,
    render_review,
    sha,
    verify_review,
    write_audio,
)
from voice_harness.events import Ledger
from voice_harness.telephony_demo import verify_output


class CallReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = 1_000_000_000
        self.sources = {"remote": np.concatenate([np.full(8000, 1000), np.full(8000, 2000)]).astype("<i2"),
                        "sent": np.full(4000, 4000, dtype="<i2")}
        for role, audio in self.sources.items():
            write_audio(self.root / ("remote.wav" if role == "remote" else "sent.wav"), audio)
        self.ledger = Ledger("review_test", self.root / "events.jsonl")
        for kind, at, content in [
            ("stream.bound", 0, {"generation": 0}),
            ("stream.gap_started", 1, {"generation": 1}),
            ("stream.bound", 3, {"generation": 1}),
            ("speech.remote_transcript", 3.3, {"text": "Question </script><script>bad()</script>"}),
            ("speech.release", 3.5, {"generation": 1, "text": "An approved answer.", "audio_sha256": "missing-cache"}),
            ("playback.mark", 4, {"name": "phrase_1", "cleared": False}),
        ]:
            self.ledger.append("orchestrator", kind, content, monotonic_ns=self.origin + int(at * 1e9))
        self.events = [event.to_dict() for event in self.ledger.events]
        (self.root / "summary.json").write_text(json.dumps({"scenario": "lost-wallet"}))
        (self.root / "remote-transcript.txt").write_text("An independent transcript.")
        self.seal()

    def seal(self):
        names = ["remote.wav", "sent.wav", "events.jsonl", "summary.json", "remote-transcript.txt"]
        if (self.root / "audio-timing.json").exists():
            names.append("audio-timing.json")
        manifest = {"run_id": "review_test", "sha256": {name: sha(self.root / name) for name in names},
                    "ledger_final_hash": self.ledger.events[-1].event_hash}
        (self.root / "manifest.json").write_text(json.dumps(manifest))

    def captured_alignment(self):
        chunks = [
            {"track": "remote", "source_frame": 0, "frames": 8000, "at_ns": self.origin, "generation": 0},
            {"track": "remote", "source_frame": 8000, "frames": 8000, "at_ns": self.origin + 3_000_000_000, "generation": 1},
            {"track": "sent", "source_frame": 0, "frames": 4000, "at_ns": self.origin + 1_000_000_000, "generation": 0},
        ]
        (self.root / "audio-timing.json").write_text(json.dumps({"schema_version": 1, "clock": "local_monotonic_ns", "chunks": chunks}))
        self.seal()
        return make_alignment(self.root, self.sources, self.events)

    def test_captured_timing_preserves_silence_and_places_both_voices(self):
        alignment = self.captured_alignment()
        self.assertEqual(alignment["mode"], "capture_clock")
        mono, stereo, adjusted = mix_tracks(self.sources, alignment)
        self.assertEqual(stereo.shape, (32000, 2))
        np.testing.assert_array_equal(mono[:8000], 500)
        np.testing.assert_array_equal(mono[8000:12000], 2000)
        np.testing.assert_array_equal(mono[12000:24000], 0)
        np.testing.assert_array_equal(mono[24000:], 1000)
        self.assertEqual(adjusted, 0)

    def test_legacy_recording_is_explicitly_estimated_and_restores_stream_gap(self):
        alignment = make_alignment(self.root, self.sources, self.events)
        self.assertEqual(alignment["mode"], "estimated_legacy")
        mono, stereo, _ = mix_tracks(self.sources, alignment)
        np.testing.assert_array_equal(stereo[8000:24000, 0], 0)
        np.testing.assert_array_equal(stereo[28000:32000, 1], 4000)
        self.assertEqual(len(mono), 32000)

    def test_outbound_packet_jitter_does_not_insert_silence_into_speech(self):
        samples = np.arange(480, dtype="<i2") + 1000
        self.sources["sent"] = samples
        write_audio(self.root / "sent.wav", samples)
        chunks = [{"track": "remote", "source_frame": 0, "frames": 16000, "at_ns": self.origin, "generation": 0}]
        chunks += [{"track": "sent", "source_frame": n * 160, "frames": 160,
                    "at_ns": self.origin + 3_500_000_000 + n * 22_000_000, "generation": 1} for n in range(3)]
        (self.root / "audio-timing.json").write_text(json.dumps({"schema_version": 1, "clock": "local_monotonic_ns", "chunks": chunks}))
        self.seal()
        alignment = make_alignment(self.root, self.sources, self.events)
        _, stereo, _ = mix_tracks(self.sources, alignment)
        np.testing.assert_array_equal(stereo[28000:28480, 1], samples)
        self.assertEqual(len([s for s in alignment["segments"] if s["track"] == "sent"]), 1)

    def test_invalid_source_ranges_are_rejected_rather_than_repeated_or_dropped(self):
        alignment = self.captured_alignment()
        alignment["segments"][0]["source_frame"] = 1
        with self.assertRaisesRegex(ValueError, "INVALID_OR_INCOMPLETE"):
            mix_tracks(self.sources, alignment)

    def test_overlapping_chunk_timestamps_do_not_overwrite_source_samples(self):
        alignment = self.captured_alignment()
        alignment["segments"][1]["timeline_frame"] = 7990
        _, stereo, adjusted = mix_tracks(self.sources, alignment)
        self.assertEqual(adjusted, 10)
        np.testing.assert_array_equal(stereo[:8000, 0], 1000)
        np.testing.assert_array_equal(stereo[8000:16000, 0], 2000)

    def test_derived_review_preserves_sources_and_detects_tampering(self):
        self.captured_alignment()
        originals = {p.name: sha(p) for p in self.root.iterdir()}
        result = render_review(self.root)
        self.assertEqual(result["alignment_mode"], "capture_clock")
        for name, digest in originals.items():
            self.assertEqual(sha(self.root / name), digest)
        self.assertTrue(verify_output(self.root))
        self.assertTrue(verify_review(self.root))
        page = (self.root / "index.html").read_text()
        self.assertIn("Capture-clock alignment", page)
        self.assertIn("&lt;/script&gt;&lt;script&gt;bad()", page)
        self.assertNotIn("Question </script>", page)
        combined = page.split('<section id="originals"')[0]
        self.assertIn('id="full-transcript"', combined)
        self.assertIn("An independent transcript.", combined)
        self.assertIn("Live conversation events — may be incomplete", combined)
        self.assertIn("no sentence timestamps", combined)
        (self.root / "conversation.wav").write_bytes(b"tampered")
        self.assertFalse(verify_review(self.root))

    def test_mismatched_legacy_alignment_is_not_silently_reused(self):
        alignment = make_alignment(self.root, self.sources, self.events)
        alignment["source_sha256"]["sent.wav"] = "wrong"
        (self.root / "alignment.json").write_text(json.dumps(alignment))
        with self.assertRaisesRegex(ValueError, "SOURCE_MISMATCH"):
            make_alignment(self.root, self.sources, self.events)

    def test_original_evidence_tamper_prevents_render(self):
        (self.root / "summary.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "SOURCE_EVIDENCE"):
            render_review(self.root)
