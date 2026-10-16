import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from voice_harness.events import Ledger
from voice_harness.playback import PlaybackGate
from voice_harness.verdicts import aggregate


class EvidenceTests(unittest.TestCase):
    def test_restart_and_tamper_detection_is_nonmutating(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            ledger = Ledger("r1", path)
            ledger.append("orchestrator", "run.started", {"seed": 42})
            recovered = Ledger("r1", path)
            recovered.append("orchestrator", "run.completed", {})
            self.assertTrue(Ledger("r1", path).verify())
            recovered.events[0].content["seed"] = 43
            before = recovered.export()
            self.assertFalse(recovered.verify())
            self.assertFalse(recovered.verify())
            self.assertEqual(before, recovered.export())
            with self.assertRaisesRegex(ValueError, "INTEGRITY"):
                Ledger("another_run", path)

    def test_failed_persistence_never_acknowledges_event(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger("r1")
            ledger.path = Path(directory)  # Cannot append to a directory.
            with self.assertRaises(OSError):
                ledger.append("orchestrator", "run.started", {})
            self.assertEqual(ledger.events, [])


class PlaybackTests(unittest.TestCase):
    def test_forged_modified_expired_and_replayed_tokens(self):
        gate = PlaybackGate("client")
        unit = gate.hold("response", "fixture speech")
        token = gate.approve(unit)
        for bad in [
            replace(token, token_id="forged"),
            replace(token, audio_sha256="wrong"),
            replace(token, assessment_epoch=9),
            replace(token, client_session_id="other"),
        ]:
            with self.subTest(token=bad), self.assertRaises(ValueError):
                gate.release(bad)
        expired = gate.approve(unit, ttl_seconds=-1)
        with self.assertRaises(ValueError):
            gate.release(expired)
        self.assertEqual(gate.release(token), "fixture speech")
        with self.assertRaises(ValueError):
            gate.release(token)

    def test_revision_revokes_old_approval(self):
        gate = PlaybackGate("client")
        original = gate.hold("response", "ten pounds")
        old_token = gate.approve(original)
        revised = gate.revise(original.unit_id, "one hundred pounds")
        with self.assertRaises(ValueError):
            gate.release(old_token)
        self.assertEqual(revised.assessment_epoch, 2)
        self.assertEqual(gate.release(gate.approve(revised)), "one hundred pounds")

    def test_partial_evidence_and_overflow_cannot_release(self):
        gate = PlaybackGate("client", max_buffer_bytes=10)
        partial = gate.hold("response", "partial", transcript_final=False)
        with self.assertRaisesRegex(ValueError, "PARTIAL_TRANSCRIPT"):
            gate.approve(partial)
        with self.assertRaisesRegex(ValueError, "BUFFER_OVERFLOW"):
            gate.hold("response2", "extra")


class VerdictTests(unittest.TestCase):
    def test_mandatory_truth_table(self):
        cases = [
            (["fail", "untestable"], "fail"),
            (["pass", "untestable"], "untestable"),
            (["pass", "disagreement"], "review_required"),
            ([], "untestable"),
            (["not_applicable"], "untestable"),
            (["pass", "pass"], "pass"),
        ]
        for inputs, expected in cases:
            with self.subTest(inputs=inputs):
                self.assertEqual(aggregate(inputs), expected)
