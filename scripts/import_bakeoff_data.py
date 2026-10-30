"""Import the runs of an isolated bake-off data directory into the data directory the GUI serves.

    python scripts/import_bakeoff_data.py --source .local/bakeoff-2026-10-29-standard-5 --target .local/host-data [--apply]

Events are copied verbatim: a run's hash chain pins its scenario id, version and content hash, so nothing is rewritten.
Scenario definitions are carried by content hash (harness_scenario_by_hash); a hash the source registry cannot supply is
rebuilt from the current catalogue and must match exactly or the import stops. Source signing keys are added to the
target key history as verification-only. SQLite only. Without --apply this is a dry run that changes nothing.
Stop the server on the target first: a copy of the target database is taken, then everything is applied in one transaction.
"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Tables whose rows belong to a run (run_id column) are copied for the imported runs only.
GLOBAL_COPIED = ("harness_bakeoffs", "harness_provider_capability_reports")
SKIPPED = ("harness_migrations", "harness_scenario_definitions", "harness_balance_settings", "harness_calibration_policies",
           "harness_calibration_approvals", "harness_calibration_reports", "harness_comparisons", "harness_cost_reconciliations")


def database_file(data):
    for name in ("bakeoff.sqlite", "development.sqlite"):
        if (data / name).exists():
            return data / name
    raise SystemExit("no bakeoff.sqlite or development.sqlite in " + str(data))


def columns(db, schema, table):
    return [r[1] for r in db.execute(f"PRAGMA {schema}.table_info({table})")]


def resolve_scenarios(db):
    """(hash -> (scenario_id, version, definition)) for every scenario hash an imported run pinned."""
    from voice_harness.scenario_catalogue import CATALOGUE
    from voice_harness.scenario_defs import SCENARIOS, definition_hash

    pinned = {}
    for (payload,) in db.execute("SELECT e.payload FROM src.harness_events e WHERE e.sequence=1 AND e.run_id IN (SELECT run_id FROM new_runs)"):
        content = json.loads(payload)["content"]
        pinned[content["scenario_hash"]] = (content["scenario_id"], content["scenario_version"])
    found, unresolved = {}, []
    for content_hash, (scenario_id, version) in pinned.items():
        row = db.execute("SELECT definition FROM src.harness_scenario_definitions WHERE scenario_id=? AND version=?", (scenario_id, version)).fetchone()
        candidates = [json.loads(row[0])] if row else []
        base = CATALOGUE[scenario_id]["definition"] if scenario_id in CATALOGUE else SCENARIOS.get(scenario_id)
        if base is not None:
            candidates.append({**base, "version": version})  # an in-place edit of this version, rebuilt from the catalogue
        match = next((c for c in candidates if definition_hash(c) == content_hash), None)
        if match is None:
            unresolved.append((scenario_id, version, content_hash))
        else:
            found[content_hash] = (scenario_id, version, match)
    if unresolved:
        raise SystemExit("cannot rebuild scenario definitions for: " + json.dumps(unresolved))
    return found


def chain_errors(db, schema):
    """Run ids whose events do not form an unbroken hash chain (tombstoned payloads are checked by retention, not here)."""
    from voice_harness.canonical import canonical_json
    from voice_harness.retention import is_tombstone

    bad, previous, current = [], None, None
    for run_id, sequence, payload in db.execute(f"SELECT run_id, sequence, payload FROM {schema}.harness_events WHERE run_id IN (SELECT run_id FROM new_runs) ORDER BY run_id, sequence"):
        event = json.loads(payload)
        if run_id != current:
            current, previous = run_id, None
        ok = event["prev_hash"] == previous and event["sequence"] == sequence
        if ok and not is_tombstone(event):
            ok = hashlib.sha256(canonical_json({**event, "event_hash": None})).hexdigest() == event["event_hash"]
        if not ok and run_id not in bad:
            bad.append(run_id)
        previous = event["event_hash"]
    return bad


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source, target = args.source.resolve(), args.target.resolve()
    target_db = database_file(target)
    if args.apply:
        backup = target_db.with_name(target_db.name + ".pre-import-" + source.name)
        if backup.exists():
            raise SystemExit("backup already exists, remove it or pick another source: " + str(backup))
        shutil.copy2(target_db, backup)
        print("backup:", backup)

    from voice_harness.storage import Database

    if args.apply:
        Database(str(target_db))  # runs every migration, including harness_scenario_by_hash, before the copy
    db = sqlite3.connect(target_db, timeout=60, isolation_level=None)
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("ATTACH DATABASE ? AS src", (str(database_file(source)),))
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("CREATE TEMP TABLE new_runs AS SELECT run_id FROM src.harness_runs WHERE run_id NOT IN (SELECT run_id FROM main.harness_runs)")
        total = db.execute("SELECT count(*) FROM src.harness_runs").fetchone()[0]
        new = db.execute("SELECT count(*) FROM new_runs").fetchone()[0]
        print(f"source runs: {total}, to import: {new}, already present: {total - new}")
        scenarios = resolve_scenarios(db)
        broken = chain_errors(db, "src")
        if broken:
            raise SystemExit(f"source hash chain broken for {len(broken)} runs, e.g. {broken[:3]}")
        print("source hash chains intact for all runs to import")

        counts = {}
        tables = [r[0] for r in db.execute("SELECT name FROM src.sqlite_master WHERE type='table' AND name LIKE 'harness_%'")]
        for table in sorted(tables, key=lambda t: t != "harness_runs"):  # runs first; foreign keys are off but keep the order natural
            if table in SKIPPED or table not in [r[0] for r in db.execute("SELECT name FROM main.sqlite_master")]:
                continue
            shared = [c for c in columns(db, "src", table) if c in columns(db, "main", table)]
            names = ", ".join(shared)
            if "run_id" in shared:
                where = " WHERE run_id IN (SELECT run_id FROM new_runs)"
            elif table == "harness_human_reviews":
                where = " WHERE finding_id IN (SELECT finding_id FROM src.harness_judge_findings WHERE run_id IN (SELECT run_id FROM new_runs))"
            elif table in GLOBAL_COPIED:
                where = ""
            else:
                print("not copied (no run_id):", table)
                continue
            before = db.total_changes
            db.execute(f"INSERT OR IGNORE INTO main.{table} ({names}) SELECT {names} FROM src.{table}{where}")
            counts[table] = db.total_changes - before
        for content_hash, (scenario_id, version, definition) in scenarios.items():
            if args.apply:
                db.execute("INSERT OR IGNORE INTO main.harness_scenario_by_hash VALUES (?, ?, ?, ?)", (content_hash, scenario_id, version, json.dumps(definition, sort_keys=True)))
            db.execute("INSERT OR IGNORE INTO main.harness_scenario_definitions VALUES (?, ?, ?, ?)", (scenario_id, version, content_hash, json.dumps(definition, sort_keys=True)))
        for table, n in counts.items():
            print(f"  {table}: {n} rows")
        after = chain_errors(db, "main")
        if after:
            raise SystemExit("target hash chain broken after copy: " + str(after[:3]))
        print("target hash chains intact for all imported runs")
        if not args.apply:
            db.execute("ROLLBACK")
            print("dry run: nothing changed. Re-run with --apply (server stopped) to import.")
            return
        # objects and signing keys sit outside the database: copy after its rows are staged, before the commit
        copied = 0
        for run_dir in (source / "objects").iterdir() if (source / "objects").exists() else []:
            if not (target / "objects" / run_dir.name).exists() and db.execute("SELECT 1 FROM new_runs WHERE run_id=?", (run_dir.name,)).fetchone():
                shutil.copytree(run_dir, target / "objects" / run_dir.name)
                copied += 1
        print("object directories copied:", copied)
        from voice_harness.signing import KeyStore

        store = KeyStore(target)
        for line in (source / "signing" / "key-history.jsonl").read_text().splitlines():
            record = json.loads(line)
            if record["event"] == "generated" and store.import_public_key(record["key_id"], record["public_key"], "imported from " + source.name):
                print("imported verification key", record["key_id"])
        db.execute("COMMIT")
        print("committed")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


if __name__ == "__main__":
    main()
