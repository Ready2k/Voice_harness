import tempfile
import unittest
from pathlib import Path

from voice_harness.generate_contracts import generate


class ContractsInSyncTests(unittest.TestCase):
    def test_checked_in_contracts_match_generator_output(self):
        committed = Path("contracts")
        if not committed.is_dir():
            self.skipTest("contracts/ is not shipped in this image")
        with tempfile.TemporaryDirectory() as directory:
            generate(directory)
            generated = {p.name for p in Path(directory).iterdir()}
            self.assertEqual(generated, {p.name for p in committed.iterdir()})
            stale = [
                name
                for name in sorted(generated)
                if (Path(directory) / name).read_bytes() != (committed / name).read_bytes()
            ]
            self.assertEqual(stale, [], "run: python -m voice_harness.generate_contracts")


if __name__ == "__main__":
    unittest.main()
