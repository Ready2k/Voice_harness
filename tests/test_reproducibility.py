"""Phase 5 reproducibility: the same scenario and seed give the same evidence ledger.

Raw ledger hashes differ between runs because every event carries a random id, the run and session ids, a
monotonic timestamp and a wall-clock time, and the hash chain covers all of them. ``normalized_ledger`` removes
exactly those fields (ids become first-seen ordinals so the causation graph is still compared) and hashes what is
left: types, order, content, decisions, reason codes, fixture and policy identifiers.
"""

import hashlib
import json
import re
import unittest

from test_conformance import Base

from voice_harness.canonical import canonical_json
from voice_harness.contracts import RunConfig
from voice_harness.scenarios import run_scenario

ID = re.compile(r"\b[a-z]+_[0-9a-f]{16,32}\b")
VOLATILE = {"event_hash", "prev_hash", "monotonic_ns", "occurred_at", "event_id"}
TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:\+00:00|Z)")
# Measured or generated per execution, not decided by the scenario: wall-clock timings and OpenTelemetry ids, random approval-token ids,
# and ISO timestamps embedded in tool results.
VOLATILE_CONTENT = {"latency_ms", "span_id", "trace_id", "parent_span_id", "token_id"}


def strip(value):
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in VOLATILE_CONTENT}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def normalized_ledger(events):
    """Digest of a run's events with random ids replaced by first-seen ordinals and clock fields removed."""
    seen = {}

    def ordinal(match):
        return seen.setdefault(match.group(0), f"{match.group(0).split('_')[0]}#{len(seen)}")

    rows = []
    for event in events:
        kept = strip({key: value for key, value in event.items() if key not in VOLATILE})
        kept["event_ref"] = event["event_id"]
        text = TIMESTAMP.sub("<timestamp>", json.dumps(kept, sort_keys=True, default=str))  # tool results carry as_of
        rows.append(ID.sub(ordinal, text))
    return hashlib.sha256(canonical_json(rows)).hexdigest(), rows


class ReproducibilityTests(Base):
    def digest(self, **config):
        run = self.engine.create(RunConfig(**config).model_dump())
        run_scenario(self.engine, run, self.evidence)
        return normalized_ledger(self.database.events(run))

    def test_same_scenario_and_seed_give_the_same_ledger_digest(self):
        first, first_rows = self.digest()
        second, second_rows = self.digest()
        self.maxDiff = None
        self.assertEqual(first_rows, second_rows)
        self.assertEqual(first, second)

    def test_every_builtin_scenario_is_reproducible(self):
        from voice_harness.scenario_defs import SCENARIOS

        for scenario_id in SCENARIOS:
            with self.subTest(scenario=scenario_id):
                self.assertEqual(self.digest(scenario_id=scenario_id)[0], self.digest(scenario_id=scenario_id)[0])

    def test_raw_hashes_differ_so_the_normalisation_is_what_makes_runs_comparable(self):
        run_a = self.engine.create(RunConfig().model_dump())
        run_b = self.engine.create(RunConfig().model_dump())
        for run in (run_a, run_b):
            run_scenario(self.engine, run, self.evidence)
        self.assertNotEqual(
            self.database.events(run_a)[-1]["event_hash"], self.database.events(run_b)[-1]["event_hash"]
        )

    def test_a_different_seed_changes_the_digest(self):
        self.assertNotEqual(self.digest()[0], self.digest(fixture_seed=7)[0])

    def test_digest_detects_a_changed_decision(self):
        _, rows = self.digest()
        tampered = [row.replace('"action": "deny"', '"action": "allow"') for row in rows]
        self.assertNotEqual(rows, tampered)


if __name__ == "__main__":
    unittest.main()
