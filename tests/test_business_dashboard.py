import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/build_business_dashboard.py'
if SCRIPT.is_file():  # the container image ships neither the script nor the git tree it reads
    spec = importlib.util.spec_from_file_location('business_dashboard', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


@unittest.skipUnless(SCRIPT.is_file(), 'dashboard builder is not in the container image')
class BusinessDashboardTests(unittest.TestCase):
    def test_sources_versions_and_latest(self):
        result = module.build()
        self.assertEqual(result, json.loads((ROOT / 'site/business-dashboard.json').read_text()))
        self.assertEqual(result['studies'][0]['versions'][0]['path'], module.LATEST)
        self.assertEqual(result['studies'][0]['versions'][0]['runs'], 720)  # six providers: 600 standard-prompt cells plus GPT-Live's 120 carried over from 28 Oct
        fair = next(s for s in result['studies'] if s['source'].startswith('bko_851d76'))
        self.assertEqual(len(fair['versions']), 2)
        self.assertIn('rescored', fair['versions'][0]['path'])
        paused = [v for s in result['studies'] for v in s['versions'] if v['state'] == 'paused']
        self.assertEqual(len(paused), 1)
        self.assertEqual(paused[0]['runs'], 0)
        for study in result['studies']:
            for version in study['versions']:
                self.assertTrue((ROOT / version['path']).is_file())
                self.assertTrue((ROOT / version['data']).is_file())
                self.assertEqual(len(version['sha256']), 64)
