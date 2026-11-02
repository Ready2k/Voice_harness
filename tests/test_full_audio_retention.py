"""Full caller/reply archives survive timeouts and TTL purge; opt-out remains explicit."""
import asyncio
import hashlib
import tempfile
import time
import unittest
from pathlib import Path

from voice_harness.adapters import AudioOut
from voice_harness.asr import ScriptedAsr
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.evidence import Evidence
from voice_harness.live_assessment import IndependentAssessor
from voice_harness.live_scenario import LiveScenarioRunner
from voice_harness.storage import Database

PCM = b'\x01\x00' * 48


class Adapter:
    async def send_audio(self, data, metadata):
        pass

    async def request_turn(self):
        pass

    async def receive(self):
        yield AudioOut(pcm=PCM, sample_count=48)
        await asyncio.sleep(10)


class FullAudioRetention(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Database(str(self.root / 'test.sqlite'))
        self.engine = Engine(self.db)
        self.evidence = Evidence(self.db, self.root / 'objects')

    def runner(self, retain=True):
        run = self.engine.create(RunConfig(retain_audio=retain).model_dump())
        assessor = IndependentAssessor(self.engine, ScriptedAsr())
        runner = LiveScenarioRunner(self.engine, run, Adapter(), assessor, 'happy-balance-clean-001',
                                    caller_audio=lambda text: PCM, evidence=self.evidence, turn_timeout_s=.02)
        return run, runner

    def test_default_is_full_retention_and_input_flag_is_true(self):
        self.assertTrue(RunConfig().retain_audio)
        run, runner = self.runner()
        asyncio.run(runner._speak(0, {'customer': 'Test caller.'}, PCM))
        event = next(e for e in self.db.events(run) if e['type'] == 'input.caller_audio')
        self.assertTrue(event['content']['audio_retained'])
        self.assertEqual((self.root / 'objects' / run / event['content']['pcm_sha256']).read_bytes(), PCM)

    def test_partial_reply_survives_timeout_and_has_no_automatic_expiry(self):
        run, runner = self.runner()
        result, _ = asyncio.run(runner._await_turn(None))
        self.assertEqual(result['strict_speech_approval'], 'turn_timeout')
        sha = hashlib.sha256(PCM).hexdigest()
        self.assertEqual((self.root / 'objects' / run / sha).read_bytes(), PCM)
        self.assertIsNone(self.db.get(run)['artifacts'][sha]['expires_at'])
        self.evidence.clock = lambda: time.time() + 365 * 86400
        self.evidence.purge(run)
        self.assertTrue(self.db.get(run)['artifacts'][sha]['available'])

    def test_opt_out_does_not_archive_frames(self):
        run, runner = self.runner(False)
        asyncio.run(runner._speak(0, {'customer': 'Test'}, PCM))
        runner._note_audio(AudioOut(pcm=PCM, sample_count=48))
        runner._flush_received_audio()
        self.assertEqual(self.db.get(run)['artifacts'], {})

    def test_sampled_write_cannot_shorten_existing_archive(self):
        run, _ = self.runner()
        sha = self.evidence.put(run, PCM, ttl=None)
        self.evidence.put(run, PCM, ttl=1)
        self.assertIsNone(self.db.get(run)['artifacts'][sha]['expires_at'])


if __name__ == '__main__':
    unittest.main()
