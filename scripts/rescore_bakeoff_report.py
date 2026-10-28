"""Re-score a saved bake-off report whose explicit-commit providers were charged the caller's trailing silence.

    PYTHONPATH=src .venv/bin/python scripts/rescore_bakeoff_report.py \
        artifacts/full-advanced-bakeoff-2026-10-26/report.json \
        artifacts/full-advanced-bakeoff-2026-10-26/rescored-2026-10-27

Before the latency-anchor fix, every caller turn was streamed with ``trail_ms`` of trailing silence and first audio was
timed from the end of speech. Providers on explicit commit could not answer until the commit, which came after that
silence, so each of their timed turns over-counts by exactly ``trail_ms``. This script subtracts it from those turns
(timed turns and the content-audio detail), marks them ``anchor: legacy_commit`` / ``turn_commit: explicit``, marks the
others ``legacy_speech_end`` / ``provider_vad``, and re-runs the shipped scoring (``bakeoff.report`` through
``bakeoff_export``) on the result. Nothing else is changed. Deterministic: the same input gives byte-identical output.

The input is an exported ``report.json`` (title, report, observations) or a stored bake-off payload (with cells).
Before correcting anything the input is re-scored unchanged and must reproduce its stored composites, so the corrected
figures come from the same code path as the originals (task completion is weighted 0 for that check, since the
original scoring had no such dimension).

The re-score then applies the current default weights, which score task completion. Scenarios in ``EXCLUDE_TASK`` are
left out of task completion with the stated reason: their tool calls were denied by a harness artefact that cannot be
corrected after the fact (the denied call never returned data, so the run stays as observed).

``--claims`` (with ``--ledger``) is the 28 October final re-score, for a report that already has anchored latency and task
completion. It changes no latency and keeps the stored weights; the unchanged input must reproduce the stored composites.
Then, from the read-only ledger: every reply is re-checked from its STORED independent ASR text (hash-verified, never
re-transcribed) with the shipped ``live_assessment.check_transcript`` and the current claim extractor; FACT-01 is
re-evaluated with ``rubric.eval_fact`` and the outcome recomputed with ``effective_from_events``; a call denied
``NOT_FOUND`` whose id now maps under the bare-number rule (``spoken_ids``) counts towards task completion; and the
scenarios in ``CLAIMS_EXCLUDE_TASK`` are left out of task completion. A with/without table shows that exclusion's effect.

    PYTHONPATH=src .venv/bin/python scripts/rescore_bakeoff_report.py --claims \
        --ledger .local/fair-bakeoff-2026-10-28/bakeoff.sqlite \
        artifacts/fair-bakeoff-2026-10-28/report.json artifacts/fair-bakeoff-2026-10-28/rescored-2026-10-28
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from voice_harness import bakeoff
from voice_harness.bakeoff_export import render, report_data

EXPLICIT = ("openai-realtime-live", "xai-voice-live", "gemini-live")  # provider-transport-v1 fixed-caller transport, 2026-10-20 on
TRAIL_MS = 1200  # live_providers.CALLER_TRAIL_MS when those runs were recorded
# The caller said "transaction txn_001"; the bank then matched it exactly, so the spoken forms TXN001 / txn001 were denied
# NOT_FOUND. Spoken-form ids are now canonicalised (spoken_ids), but denied calls cannot be replayed.
EXCLUDE_TASK = {"happy-transaction-details-001": "TRANSACTION_ID_SPOKEN_FORM_ARTEFACT"}
# The caller said "cancel payment payment_001" once; a model that asked for the id again (xAI, 3/3) was never answered.
CLAIMS_EXCLUDE_TASK = {"commit-cancel-confirmed-001": "SCENARIO_NO_CLARIFICATION_TURN"}
FIXTURE_IDS = {"payment": ["payment_001"], "txn": ["txn_001"]}  # the ids the trusted bank holds (engine._bank_actual)


def payload_from(saved):
    """A bake-off payload ``bakeoff.report`` can score, from an exported report.json or a stored payload."""
    if "cells" in saved:
        return copy.deepcopy(saved)
    r = saved["report"]
    return {"name": saved["title"], "providers": saved["providers"], "weights": r["weights"],
            "repetitions": r["limits"]["repetitions"], "group_balanced": r["scoring"]["group_balanced"],
            "state": r["readiness"]["state"], "phase": r["readiness"]["phase"], "pause_reason": r["readiness"]["pause_reason"],
            "preflight": r["readiness"]["preflight"], "incidents": r["readiness"]["incidents"],
            "cells": [{"provider_config_id": o["provider"], "done": True, "observation": copy.deepcopy(o)} for o in saved["observations"]]}


def correct(payload, explicit=EXPLICIT, trail_ms=TRAIL_MS):
    """Re-anchor explicit-commit turns at the commit, in place. Returns {provider: turns corrected}. Idempotent: turns that
    already carry an anchor are left alone."""
    changed = {}
    for cell in payload["cells"]:
        o = cell.get("observation")
        if not o:
            continue
        is_explicit = o["provider"] in explicit
        o.setdefault("turn_commit", "explicit" if is_explicit else "provider_vad")
        details = {t["turn"]: t for t in (o.get("metrics") or {}).get("turns", [])}
        for t in o["turns"]:
            if t.get("anchor"):
                continue
            t["turn_commit"] = o["turn_commit"]
            t["anchor"] = "legacy_commit" if is_explicit else "legacy_speech_end"
            if not is_explicit:
                continue
            if t["first_audio_ms"] is not None:
                if t["first_audio_ms"] < trail_ms:
                    raise ValueError(f"AUDIO_BEFORE_COMMIT:{o['run_id']}:{t['turn']}")
                t["first_audio_ms"] -= trail_ms
                changed[o["provider"]] = changed.get(o["provider"], 0) + 1
            detail = details.get(t["turn"])
            if detail and detail.get("content_audio_ms") is not None:
                detail["content_audio_ms"] -= trail_ms
    return changed


def exclude_task_completion(payload, excluded=None):
    """Mark the observations of artefact scenarios as not scored for task completion, in place. Returns {scenario: runs}."""
    excluded = EXCLUDE_TASK if excluded is None else excluded
    marked = {}
    for cell in payload["cells"]:
        o = cell.get("observation")
        if o and o["scenario_id"] in excluded:
            o["task_completion_excluded"] = excluded[o["scenario_id"]]
            marked[o["scenario_id"]] = marked.get(o["scenario_id"], 0) + 1
    return marked


def reassess_fact(payload, ledger):
    """Re-evaluate failed FACT-01 runs with the current rubric against the read-only SQLite ``ledger``, in place.

    FACT-01 v2 treats amounts parsed from an ambiguous spoken unit as unresolved rather than a mismatch. Only the FACT-01
    verdict in the run's latest adjudication is replaced; the run outcome is then recomputed by the shipped
    ``effective_from_events``. Returns [{run_id, provider, before, after}] for every verdict that changed."""
    import sqlite3

    from voice_harness.judges import rubric
    from voice_harness.judges.review import effective_from_events
    from voice_harness.judges.service import fixture_for, scenario_for

    db = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    changed = []
    for cell in payload["cells"]:
        o = cell.get("observation")
        if not o or (o.get("criteria") or {}).get("FACT-01") != "fail":
            continue
        row = db.execute("SELECT payload FROM harness_runs WHERE run_id=?", (o["run_id"],)).fetchone()
        if row is None:
            raise SystemExit(f"run {o['run_id']} is not in the ledger; refusing to re-assess")
        state = json.loads(row[0])
        events = [json.loads(p) for (p,) in db.execute(
            "SELECT payload FROM harness_events WHERE run_id=? ORDER BY sequence", (o["run_id"],))]
        result = rubric.eval_fact(rubric.EvalContext(events, scenario_for(state), fixture_for(state)))
        if result["verdict"] == "fail":
            continue
        adjudication = [e for e in events if e["type"] == "judge.adjudicated"][-1]
        for entry in adjudication["content"]["criteria"]:
            if entry["criterion_id"] == "FACT-01":
                entry.update(verdict=result["verdict"], reason_codes=result["reason_codes"])
        assurance = effective_from_events(events, o["run_id"])
        o["criteria"]["FACT-01"], o["criterion_reasons"]["FACT-01"] = result["verdict"], result["reason_codes"]
        o.update(outcome=assurance["overall_assurance"], model_behaviour=assurance.get("model_behaviour"),
                 platform_enforcement=assurance.get("platform_enforcement"), fact_reassessed="FACT-01@v2")
        changed.append({"run_id": o["run_id"], "provider": o["provider"], "scenario_id": o["scenario_id"],
                        "before": "fail", "after": result["verdict"], "reason_codes": result["reason_codes"],
                        "outcome": assurance["overall_assurance"]})
    return changed


def _ledger(path):
    import sqlite3

    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _run(db, run_id):
    row = db.execute("SELECT payload FROM harness_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise SystemExit(f"run {run_id} is not in the ledger; refusing to re-assess")
    return json.loads(row[0]), [json.loads(p) for (p,) in db.execute(
        "SELECT payload FROM harness_events WHERE run_id=? ORDER BY sequence", (run_id,))]


def _observations(payload):
    return [c["observation"] for c in payload["cells"] if c.get("observation") and c["observation"].get("completed")]


def reassess_replies(payload, db):
    """Re-check every reply from its stored independent ASR text with the current extractor, in place.

    The transcript is the unit's ``transcript.revised`` (independent ASR) text, checked against the stored
    ``transcript_sha256``; the frozen fact context is the event's own ``expected``. A unit the second ASR disputed keeps
    its stored disposition (the second transcript was not stored). Returns (changed, held): one entry per unit whose
    disposition or reasons changed, and one per unit still not safe, with its retained reply audio for listening."""
    from voice_harness.asr import DISAGREEMENT
    from voice_harness.live_assessment import check_transcript

    changed, held = [], []
    for o in _observations(payload):
        _, events = _run(db, o["run_id"])
        retained = {e["content"]["unit_id"]: e["content"]["artifact_sha256"] for e in events
                    if e["type"] == "assessment.reply_audio" and e["content"].get("retained")}
        revised = {e["content"]["unit_id"]: e["content"]["new"] for e in events
                   if e["type"] == "transcript.revised" and e["content"].get("source") == "independent_asr"}
        stored, counts = {"safe": 0, "unsafe": 0, "untestable": 0}, {"safe": 0, "unsafe": 0, "untestable": 0}
        for e in (e for e in events if e["type"] == "assessment.independent_asr"):
            c = e["content"]
            before = [c["disposition"], c["reason_codes"]]
            stored[c["disposition"]] += 1
            text = revised.get(c["unit_id"])
            if text is None or hashlib.sha256(text.encode()).hexdigest() != c["transcript_sha256"]:
                raise SystemExit(f"stored ASR text of {c['unit_id']} in {o['run_id']} does not match its hash")
            check = None if DISAGREEMENT in c["reason_codes"] else check_transcript(text, c["expected"])
            after = before if check is None else [check["disposition"], check["reason_codes"]]
            counts[after[0]] += 1
            if after != before:
                changed.append({"run_id": o["run_id"], "provider": o["provider"], "scenario_id": o["scenario_id"],
                                "unit_id": c["unit_id"], "transcript": text, "before": before, "after": after,
                                "amounts_minor": check["claims"]["amounts_minor"],
                                "negative_minor": check["claims"]["negative_minor"]})
            if after[0] != "safe":
                held.append({"run_id": o["run_id"], "provider": o["provider"], "scenario_id": o["scenario_id"],
                             "unit_id": c["unit_id"], "transcript": text, "after": after,
                             "retained_audio_sha256": retained.get(c["unit_id"])})
        if [stored[k] for k in ("safe", "unsafe", "untestable")] != [o["replies_safe"], o["replies_unsafe"], o["replies_untestable"]]:
            raise SystemExit(f"stored reply counts of {o['run_id']} do not match its ledger; refusing to re-assess")
        o.update(replies_safe=counts["safe"], replies_unsafe=counts["unsafe"], replies_untestable=counts["untestable"])
    return changed, held


def _deterministic(entry):
    """The deterministic FACT-01 verdict behind a stored adjudication entry (judge dissent raised untestable to review)."""
    if entry["verdict"] == "review_required" and "JUDGE_DISSENT_UNTESTABLE_DETERMINISTIC" in entry["needs_review_reasons"]:
        return "untestable"
    return entry["verdict"]


def reassess_fact_all(payload, db):
    """Re-evaluate FACT-01 on every applicable run with the current rubric (FACT-01 v3, claims-v2), in place.

    Where the deterministic verdict changes, the latest adjudication entry takes it (an untestable verdict a judge
    failed stays review_required, as ``adjudicate_criterion`` decides) and the outcome is recomputed by the shipped
    ``effective_from_events``. Returns one entry per run whose verdict changed."""
    from voice_harness.judges import rubric
    from voice_harness.judges.review import effective_from_events
    from voice_harness.judges.service import fixture_for, scenario_for

    changed = []
    for o in _observations(payload):
        if "FACT-01" not in (o.get("criteria") or {}):
            continue
        state, events = _run(db, o["run_id"])
        result = rubric.eval_fact(rubric.EvalContext(events, scenario_for(state), fixture_for(state)))
        adjudication = [e for e in events if e["type"] == "judge.adjudicated"][-1]
        entry = next(e for e in adjudication["content"]["criteria"] if e["criterion_id"] == "FACT-01")
        before = _deterministic(entry)
        if result["verdict"] == before:
            continue
        verdict = result["verdict"]
        if verdict == "untestable" and "fail" in entry["judge_verdicts"].values():
            verdict = "review_required"
        entry.update(verdict=verdict, reason_codes=result["reason_codes"], criterion_version=rubric.CRITERIA["FACT-01"].version)
        assurance = effective_from_events(events, o["run_id"])
        changed.append({"run_id": o["run_id"], "provider": o["provider"], "scenario_id": o["scenario_id"],
                        "before": o["criteria"]["FACT-01"], "after": verdict, "reason_codes": result["reason_codes"],
                        "outcome_before": o["outcome"], "outcome": assurance["overall_assurance"]})
        o["criteria"]["FACT-01"], o["criterion_reasons"]["FACT-01"] = verdict, result["reason_codes"]
        o.update(outcome=assurance["overall_assurance"], model_behaviour=assurance.get("model_behaviour"),
                 platform_enforcement=assurance.get("platform_enforcement"), fact_reassessed="FACT-01@v3")
    return changed


def remap_bare_ids(payload, db):
    """Count a call denied NOT_FOUND towards task completion when its id now maps under the bare-number rule, in place.

    Only where the bare number (and nothing else) made the difference, the remapped call carries the declared arguments,
    a cancellation's confirmation was accepted in that run, unexpired at the denial and never consumed, and with that call
    the expected sequence is complete in order. The denied call still never ran; only task-completion scoring changes."""
    from voice_harness.bakeoff_metrics import carries
    from voice_harness.scenario_catalogue import catalogue_entry
    from voice_harness.spoken_ids import canonical_arguments

    changed = []
    for o in _observations(payload):
        metrics = o.get("metrics") or {}
        if not o.get("expected_tools") or metrics.get("task_completed", True):
            continue
        wanted = catalogue_entry(o["scenario_id"])["expected_arguments"]
        _, events = _run(db, o["run_id"])
        requested = {e["content"]["call_id"]: e["content"] for e in events if e["type"] == "tool.requested"}
        accepted = {e["content"]["confirmation_id"]: e["content"]["expires_at"] for e in events if e["type"] == "confirmation.accepted"}
        consumed = {e["content"].get("confirmation_id") for e in events if e["type"] == "confirmation.consumed"}
        remapped, ran = [], []
        for e in events:
            call = requested.get(e["content"].get("call_id"), {})
            if e["type"] == "tool.completed":
                ran.append((call.get("tool"), call.get("arguments")))
            elif e["type"] == "tool.denied" and e["content"].get("error_code") == "NOT_FOUND":
                args = call.get("arguments") or {}
                mapped = canonical_arguments(args, FIXTURE_IDS)
                if mapped == canonical_arguments(args) or not carries(mapped, wanted.get(call.get("tool"))):
                    continue
                confirmation = args.get("confirmation_id")
                if call.get("tool") == "cancel_payment" and (confirmation not in accepted or confirmation in consumed
                                                             or _ts(e["occurred_at"]) >= _ts(accepted[confirmation])):
                    continue
                ran.append((call["tool"], mapped))
                remapped.append({"call_id": e["content"]["call_id"], "tool": call["tool"], "arguments": args, "mapped": mapped})
        task = list(o["expected_tools"])
        for tool, args in ran:
            if task and tool == task[0] and carries(args, wanted.get(tool)):
                task.pop(0)
        if remapped and not task:
            metrics["task_completed"] = True
            o["task_completion_rescored"] = "BARE_ID_NOW_MAPS"
            changed.append({"run_id": o["run_id"], "provider": o["provider"], "scenario_id": o["scenario_id"],
                            "calls": remapped})
    return changed


def _ts(value):
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def claims_table(report):
    rows = {r["provider"]: r for r in table(report)}
    for s in report["scores"]:
        row = rows[s["provider"]]
        for dim in ("task_success", "factual", "safe_speech", "task_completion"):
            d = s.get(dim) or {}
            row[dim] = None if d.get("value") is None else round(d["value"], 4)
            row[dim + "_k_n"] = [d.get("k"), d.get("n")]
    return list(rows.values())


VERDICT_KEYS = ("ranking", "leader", "runner_up", "no_clear_winner", "quality_lead", "latency_leader", "latency_comparable",
                "latency_comparability", "speed_only_lead", "sensitivity_basis", "ranking_basis", "weight_win_share", "rank_stable")


def claims_main(args, saved):
    from voice_harness.judges.rubric import CRITERIA
    from voice_harness.semantic_checks import CLAIMS_VERSION

    payload = payload_from(saved)
    original = bakeoff.report(payload)
    stored = {s["provider"]: s for s in saved["report"]["scores"]}
    for s in original["scores"]:
        for key in ("composite", "composite_excluding_latency"):
            if abs((stored[s["provider"]][key] or 0) - (s[key] or 0)) > 1e-12:
                raise SystemExit(f"stored {key} for {s['provider']} is not reproduced; refusing to re-score")
    db = _ledger(args.ledger)
    replies, held = reassess_replies(payload, db)
    facts = reassess_fact_all(payload, db)
    claims_only = bakeoff.report(payload)
    bare = remap_bare_ids(payload, db)
    with_scenario = bakeoff.report(payload)
    incomplete = sorted([o["provider"], o["run_id"]] for o in _observations(payload)
                        if o["scenario_id"] in CLAIMS_EXCLUDE_TASK and not (o.get("metrics") or {}).get("task_completed"))
    marked = exclude_task_completion(payload, CLAIMS_EXCLUDE_TASK)
    title = (saved.get("title") or payload["name"]) + " (final re-score: claims-v2, FACT-01 v3)"
    source = ((saved.get("source") or "Voice Harness ledger") + f" · Re-scored 2026-10-28 from the read-only ledger: {len(replies)}"
              f" reply disposition(s) and {len(facts)} FACT-01 verdict(s) re-assessed with {CLAIMS_VERSION} on the stored"
              " independent ASR text; commit-cancel-confirmed-001 left out of task completion (SCENARIO_NO_CLARIFICATION_TURN)")
    options = {"title": title, "source": source,
               "generated_at": "2026-10-28 (re-scored from " + (saved.get("generated_at") or "stored payload") + ")"}
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = report_data(payload, **options)
    (out / "report.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    (out / "report.html").write_text(render(payload, **options))
    summary = {"claims_version": CLAIMS_VERSION, "fact_01_version": CRITERIA["FACT-01"].version,
               "weights": data["report"]["weights"], "replies_reassessed": replies, "replies_still_held": held,
               "fact_reassessed": facts,
               "bare_id_task_completion": bare,
               "task_completion_excluded": {s: {"reason": CLAIMS_EXCLUDE_TASK[s], "runs": n, "incomplete": incomplete}
                                            for s, n in sorted(marked.items())},
               "before": claims_table(original), "claims_only": claims_table(claims_only),
               "with_excluded_scenario": claims_table(with_scenario),
               "after": claims_table(data["report"]),
               "verdict_before": {k: original["verdict"].get(k) for k in VERDICT_KEYS},
               "verdict_with_excluded_scenario": {k: with_scenario["verdict"].get(k) for k in VERDICT_KEYS},
               "verdict_after": {k: data["report"]["verdict"].get(k) for k in VERDICT_KEYS}}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    (out / "README.md").write_text(claims_readme(summary, data["report"]))
    print(json.dumps({k: summary[k] for k in ("after", "verdict_after")}, indent=1, sort_keys=True))


def _pct(value, k_n):
    return "–" if value is None else f"{value:.1%} ({k_n[0]:g}/{k_n[1]:g})" if k_n[0] is not None else f"{value:.1%}"


def claims_readme(summary, report):
    from voice_harness.bakeoff import NAMES

    rows = {name: {r["provider"]: r for r in summary[name]} for name in ("before", "claims_only", "with_excluded_scenario", "after")}
    order = [r["provider"] for r in summary["after"]]
    out = [
        "# 28 October bake-off, final re-score (28 October 2026)", "",
        "This directory re-issues `../report.json` (720 cells, 6 providers × 40 scenarios × 3) with the spoken-claim parser",
        f"fixed ({summary['claims_version']}, FACT-01 v{summary['fact_01_version']}) and one scenario left out of task completion.",
        "Nothing was re-run or re-transcribed: every reply is re-checked from the independent ASR text stored in the read-only",
        "ledger, verified against its stored `transcript_sha256`, with the fact context frozen into its own assessment event.", "",
        "```bash", "PYTHONPATH=src .venv/bin/python scripts/rescore_bakeoff_report.py --claims \\",
        "    --ledger .local/fair-bakeoff-2026-10-28/bakeoff.sqlite \\",
        "    artifacts/fair-bakeoff-2026-10-28/report.json artifacts/fair-bakeoff-2026-10-28/rescored-2026-10-28", "```", "",
        "Deterministic (byte-identical on a re-run). Before changing anything the script re-scores the input unchanged with its",
        "stored weights and refuses to continue unless every composite and composite-without-latency is reproduced exactly; it",
        "also refuses if any run's stored reply counts differ from its ledger. Weights are the stored ones (the current defaults).", "",
        "## What changed", "",
        f"**1. Reply dispositions ({len(summary['replies_reassessed'])} units, all OpenAI voices).** Every one of the 14 untestable",
        "replies in this bake-off came from OpenAI Realtime or GPT-Live; untestable is not a pass, so the harness's parser was",
        "costing those two providers safe speech. claims-v2 reads three natural, unambiguous forms: a leading article is not a",
        "number (\"a ten pound debit\" is £10; \"a hundred pounds\" is still £100), `GBP 10.00` parses like `£10.00`, and a",
        "sign attached to an amount (\"minus ten pounds\") is a signed claim that only a tool fact with that signed value can",
        "approve (a -1000 debit; a wrong sign is `SIGN_MISMATCH`, a wrong magnitude still `UNEXPECTED_AMOUNT`). Every other",
        "reply in the ledger re-checks to its stored disposition. Changed units:", "",
        "| Run | Provider | Scenario | Before | After | Independent ASR text |", "|---|---|---|---|---|---|"]
    for c in summary["replies_reassessed"]:
        out.append(f"| `{c['run_id']}` | {NAMES.get(c['provider'], c['provider'])} | `{c['scenario_id']}` | "
                   f"{c['before'][0]} `{','.join(c['before'][1])}` | {c['after'][0]} | {c['transcript']} |")
    out += ["", f"**Still held ({len(summary['replies_still_held'])} units).** These stay untestable by design: the ASR",
            "heard \"one pound, two hundred and thirty-four pounds and fifty-six pence\" where the provider transcript said",
            "\"1,234 pounds and 56 pence\". It is not parsed to £1,234.56. Their audio was retained; a human should listen:", "",
            "| Run | Provider | Unit | Retained audio (sha256) | Reasons |", "|---|---|---|---|---|"]
    for h in summary["replies_still_held"]:
        out.append(f"| `{h['run_id']}` | {NAMES.get(h['provider'], h['provider'])} | `{h['unit_id']}` | "
                   f"`{h['retained_audio_sha256'] or 'not retained'}` | `{','.join(h['after'][1])}` |")
    facts = summary["fact_reassessed"]
    out += ["", f"**2. FACT-01 v{summary['fact_01_version']}** was re-evaluated with `rubric.eval_fact` on every applicable run. "
            + (f"{len(facts)} verdict(s) changed:" if facts else "No verdict changed: the re-parsed transaction replies are in "
               "scenarios without FACT-01, and the four balance replies stay ambiguous (untestable / review required)."), ""]
    for f in facts:
        out.append(f"- `{f['run_id']}` ({f['provider']}, `{f['scenario_id']}`): {f['before']} → {f['after']} "
                   f"`{','.join(f['reason_codes'])}`; outcome {f['outcome_before']} → {f['outcome']}")
    excluded = summary["task_completion_excluded"]
    out += ["", "**3. Task completion.** " + "; ".join(
        f"`{s}` ({v['runs']} runs) is left out, reason `{v['reason']}`" for s, v in excluded.items()) + ". The caller said",
        "\"Yes please, cancel payment payment_001 now.\" (spoken by TTS as \"payment payment … zero zero one\") and the script",
        "had no further turn. xAI asked for the id (\"I still need the payment ID to go ahead. Do you have it?\") in 3/3 runs",
        "and could not complete; the v3 scenario answers it. ElevenLabs sent a bare `001` once. Runs not completed as recorded",
        "(after the bare-number rule): " + ", ".join(f"{NAMES.get(p, p)} `{r}`" for v in excluded.values() for p, r in v["incomplete"])
        + ".", ""]
    for b in summary["bare_id_task_completion"]:
        for call in b["calls"]:
            out.append(f"Under the bare-number rule (`spoken_ids`: a bare number in a typed id field that names exactly one known"
                       f" id of that type) `{b['run_id']}` ({NAMES.get(b['provider'], b['provider'])}) sent "
                       f"`{json.dumps(call['arguments'], sort_keys=True)}`, denied `NOT_FOUND`; it now maps to `{call['mapped']['payment_id']}` with an"
                       " accepted, unexpired, unconsumed confirmation, so that run counts as completed. The call itself never ran.")
    out += ["", "That rule changes scoring only in the with-scenario sensitivity line below, because the scenario is excluded."]
    out += ["", "## Results", "",
            "| Provider | Composite | Without latency | Safe speech | Factual | Task completion | Assurance |",
            "|---|---|---|---|---|---|---|"]
    for p in order:
        a, b = rows["before"][p], rows["after"][p]
        out.append(f"| {NAMES.get(p, p)} | {a['composite']:.4f} → **{b['composite']:.4f}** | "
                   f"{a['composite_excluding_latency']:.4f} → **{b['composite_excluding_latency']:.4f}** "
                   f"(rank {a['rank_excluding_latency']} → {b['rank_excluding_latency']}) | "
                   f"{_pct(a['safe_speech'], a['safe_speech_k_n'])} → {_pct(b['safe_speech'], b['safe_speech_k_n'])} | "
                   f"{_pct(b['factual'], b['factual_k_n'])} | "
                   f"{_pct(a['task_completion'], a['task_completion_k_n'])} → {_pct(b['task_completion'], b['task_completion_k_n'])} | "
                   f"{_pct(b['task_success'], b['task_success_k_n'])} |")
    out += ["", "Rates are group-balanced (mean of scenario-group rates); pooled k/n beside them. Factual and assurance did not change.",
            "", "### Sensitivity: `commit-cancel-confirmed-001` counted in task completion", "",
            "| Provider | Claims fix only | + bare-number rule | Final (scenario excluded) |", "|---|---|---|---|"]
    for p in order:
        c, w, f = rows["claims_only"][p], rows["with_excluded_scenario"][p], rows["after"][p]
        out.append(f"| {NAMES.get(p, p)} | {c['composite_excluding_latency']:.4f} · TC {_pct(c['task_completion'], c['task_completion_k_n'])} | "
                   f"{w['composite_excluding_latency']:.4f} · TC {_pct(w['task_completion'], w['task_completion_k_n'])} | "
                   f"{f['composite_excluding_latency']:.4f} · TC {_pct(f['task_completion'], f['task_completion_k_n'])} |")
    v0, v1 = summary["verdict_before"], summary["verdict_after"]
    out += ["", "Composite without latency, the ranking basis.", "", "## Verdict", "",
            f"- Before: `{v0['ranking']}`, leader {NAMES.get(v0['leader'])}, runner-up {NAMES.get(v0['runner_up'])}, "
            f"no clear winner: {v0['no_clear_winner']}.",
            f"- After: `{v1['ranking']}`, leader {NAMES.get(v1['leader'])}, runner-up {NAMES.get(v1['runner_up'])}, "
            f"no clear winner: {v1['no_clear_winner']}, quality lead interval {v1['quality_lead']}.",
            f"- Latency leader {NAMES.get(v1['latency_leader'])}; latency comparable: {v1['latency_comparable']} "
            f"({v1['latency_comparability']}).", ""]
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("saved", help="exported report.json or stored bake-off payload")
    parser.add_argument("out_dir")
    parser.add_argument("--explicit", default=",".join(EXPLICIT), help="providers that ran on explicit commit")
    parser.add_argument("--trail-ms", type=int, default=TRAIL_MS)
    parser.add_argument("--ledger", help="read-only SQLite ledger of the runs: re-assess failed FACT-01 runs with FACT-01 v2")
    parser.add_argument("--claims", action="store_true", help="final re-score: replies, FACT-01 and bare ids from --ledger")
    args = parser.parse_args()
    saved = json.loads(Path(args.saved).read_text())
    if args.claims:
        if not args.ledger:
            parser.error("--claims needs --ledger")
        return claims_main(args, saved)
    payload = payload_from(saved)

    original = bakeoff.report(payload, weights={**payload["weights"], "task_completion": 0.0})
    stored = {s["provider"]: s["composite"] for s in saved.get("report", {}).get("scores", [])}
    for s in original["scores"]:
        if s["provider"] in stored and abs((stored[s["provider"]] or 0) - (s["composite"] or 0)) > 1e-9:
            raise SystemExit(f"stored composite for {s['provider']} is not reproduced; refusing to re-score")

    changed = correct(payload, tuple(args.explicit.split(",")), args.trail_ms)
    latency_only = bakeoff.report(payload, weights={**payload["weights"], "task_completion": 0.0})
    marked = exclude_task_completion(payload)
    reassessed = reassess_fact(payload, args.ledger) if args.ledger else []
    payload["weights"] = dict(bakeoff.DEFAULT_WEIGHTS)
    title = (saved.get("title") or payload["name"]) + " (re-scored: latency re-anchored, task completion scored)"
    source = ((saved.get("source") or "Voice Harness ledger") + f" · Re-scored 2026-10-27: {args.trail_ms} ms trailing silence removed from"
              " explicit-commit turns · 2026-10-28: task completion scored with the current default weights"
              + (f" · {len(reassessed)} FACT-01 failure(s) re-assessed with FACT-01 v2 (ambiguous spoken amount: unresolved)" if reassessed else ""))
    options = {"title": title, "source": source, "generated_at": "2026-10-27 (re-scored from " + (saved.get("generated_at") or "stored payload") + ")"}
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = report_data(payload, **options)
    (out / "report.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    (out / "report.html").write_text(render(payload, **options))
    summary = {"turns_corrected": changed, "trail_ms": args.trail_ms, "explicit_commit": sorted(args.explicit.split(",")),
               "task_completion_excluded": {s: {"reason": EXCLUDE_TASK[s], "runs": n} for s, n in sorted(marked.items())},
               "weights": data["report"]["weights"], "fact_reassessed": reassessed,
               "before": table(original), "latency_only": table(latency_only), "after": table(data["report"]),
               "verdict_after": {k: data["report"]["verdict"][k] for k in (
                   "ranking", "leader", "runner_up", "no_clear_winner", "quality_lead", "latency_leader", "latency_comparable",
                   "latency_comparability", "speed_only_lead", "sensitivity_basis", "ranking_basis", "weight_win_share", "rank_stable")}}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=1, sort_keys=True))


def table(report):
    lat = {r["provider"]: r for r in report["latency"]}
    return [{"provider": s["provider"], "composite_order": s["composite_order"], "rank": s.get("rank"), "standing": s.get("standing"),
             "composite": round(s["composite"], 4), "composite_excluding_latency": round(s["composite_excluding_latency"], 4),
             "rank_excluding_latency": s.get("rank_excluding_latency"), "responsiveness": round(s["responsiveness"]["value"], 4),
             "task_completion": None if s.get("task_completion", {}).get("value") is None else round(s["task_completion"]["value"], 4),
             "task_completion_k_n": [s.get("task_completion", {}).get("k"), s.get("task_completion", {}).get("n")],
             "median_ms": lat[s["provider"]]["median_ms"], "p90_ms": lat[s["provider"]]["p90_ms"]}
            for s in sorted(report["scores"], key=lambda s: s.get("display_order") or s["composite_order"])]


if __name__ == "__main__":
    main()
