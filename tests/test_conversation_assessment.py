"""Evidence and aggregation checks for the authored 144-conversation review."""
import csv
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import build_conversation_assessment as assessment


class AssessmentChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.out = self.folder / 'assessment'
        self.out.mkdir()
        for name in ['plan.json', 'results.json', 'assessment/assessments.psv']:
            shutil.copy2(assessment.FOLDER / name, self.folder / name)

    def build(self):
        with patch.object(assessment, 'FOLDER', self.folder), patch.object(assessment, 'OUT', self.out):
            with redirect_stdout(io.StringIO()):
                assessment.build()

    def edit(self, mutate):
        path = self.out / 'assessments.psv'
        with path.open() as f:
            rows = list(csv.DictReader(f, delimiter='|'))
        mutate(rows)
        with path.open('w') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys(), delimiter='|', lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)

    def test_report_preserves_evidence_and_balanced_comparisons(self):
        self.build()
        data = json.loads((self.out / 'scores.json').read_text())
        self.assertEqual(len(data['reviews']), 144)
        self.assertEqual(len(data['summary']), 72)
        self.assertTrue(all(r['n'] == 2 for r in data['summary']))
        self.assertTrue(all(r['n'] == 12 for r in data['overview']))
        self.assertTrue(all(not r['audio_assessed'] for r in data['reviews']))
        for shortlist in data['shortlists']:
            for provider in shortlist['providers']:
                row = next(x for x in data['summary'] if (x['scenario'], x['condition'], x['provider']) == (shortlist['scenario'], shortlist['condition'], provider))
                self.assertEqual(row['high_risk'], 0)
        report = (self.out / 'index.html').read_text()
        for row in data['reviews']:
            self.assertIn(f'id="sample-{row["id"]}"', report)
        with (self.out / 'scores.csv').open() as f:
            self.assertEqual(len(list(csv.DictReader(f))), 144)

    def test_fabricated_quote_is_rejected(self):
        self.edit(lambda rows: rows[0].update(quote='This sentence is not in the recording evidence.'))
        with self.assertRaisesRegex(ValueError, 'Unsupported evidence'):
            self.build()

    def test_missing_sample_is_rejected(self):
        self.edit(lambda rows: rows.pop())
        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.build()

    def test_identity_cannot_change_identical_transcript_scores(self):
        def change(rows):
            row = next(r for r in rows if r['id'] == 'f2006e9723a8eab7')
            row['scores_CPHAEC'] = '111111'
        self.edit(change)
        with self.assertRaisesRegex(ValueError, 'Identical transcripts scored differently'):
            self.build()


if __name__ == '__main__':
    unittest.main()
