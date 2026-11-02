"""Protect source integrity and comparison denominators in regional context exports."""
import base64
import hashlib
import json
import unittest
from collections import Counter

from scripts import build_regional_context as regional


class RegionalContextChecks(unittest.TestCase):
    def test_approval_revisions_are_not_duplicate_recordings(self):
        old = dict(output_event_id='event-1', audio_sha256='hash', assessment_epoch=1, created_at=1, pcm='old')
        new = dict(old, assessment_epoch=2, pcm='new')
        other = dict(new, output_event_id='event-2', created_at=2)
        selected = regional.select_replies({'old': old, 'new': new, 'other': other})
        self.assertEqual(selected, [new, other])

    def test_audio_corruption_and_wrong_format_are_rejected(self):
        pcm = b'\x01\x00' * 16
        unit = dict(pcm=base64.b64encode(pcm).decode(), codec='pcm_s16le', channels=1, rate=24000,
                    byte_count=len(pcm), audio_sha256=hashlib.sha256(pcm).hexdigest())
        self.assertEqual(regional.verified_pcm(unit), pcm)
        with self.assertRaisesRegex(ValueError, 'integrity'):
            regional.verified_pcm(dict(unit, pcm=base64.b64encode(b'\x02\x00' * 16).decode()))
        with self.assertRaisesRegex(ValueError, 'format'):
            regional.verified_pcm(dict(unit, rate=16000))

    def test_assurance_pass_is_not_task_success(self):
        ob = dict(outcome='pass', metrics={'task_completed': False, 'arguments': [
            {'tool': 'get_transaction_details', 'status': 'wrong'}]})
        self.assertEqual(regional.task_note(ob), 'get_transaction_details: wrong argument')

    def test_export_has_one_primary_cell_and_separate_controls(self):
        data = json.loads((regional.OUT / 'data.json').read_text())
        rows = data['records']
        self.assertEqual(len(rows), 140)
        primary = [r for r in rows if r['cohort'] == 'primary']
        controls = [r for r in rows if r['cohort'] == 'control']
        self.assertEqual(len(primary), 120)
        self.assertEqual(len(controls), 20)
        self.assertTrue(all(r['provider'] == 'gemini-live' for r in controls))
        counts = Counter((r['provider'], r['profile'], r['scenario']) for r in primary)
        self.assertEqual(set(counts.values()), {1})
        self.assertEqual(len(counts), 120)
        self.assertTrue(all(not r['caller_audio_available'] for r in rows))
        retried = [r for r in primary if r['attempts'] > 1]
        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0]['attempt_history'][0]['failure'], 'TURN_TIMEOUT')
        self.assertTrue(retried[0]['task_completed'])
        for row in rows:
            for clip in row['clips']:
                if clip['audio_available']:
                    path = regional.OUT / clip['path']
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), clip['flac_sha256'])
        # Only allowlisted records are exported, not raw run/session/database payloads.
        public = json.dumps(data)
        for sensitive_key in ['operator-token', 'reviewer-token', 'isolation_key', 'provider_session_id', '"pcm":']:
            self.assertNotIn(sensitive_key, public)


if __name__ == '__main__':
    unittest.main()
