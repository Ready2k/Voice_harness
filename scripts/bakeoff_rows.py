"""Per-run and per-turn rows for the provider bake-off, read from the ledger through the API.

    .venv/bin/python scripts/bakeoff_rows.py artifacts/provider-matrix/matrix-2026-10-19.json

Writes runs.json (one row per scenario x provider, with scenario conditions and safety counts) and turns.json
(one row per caller turn, with response latency) next to the input file.

Latency is measured in the process clock domain shared by `input.caller_audio` and `adapter.native_event`:
caller speech end is the end of the streamed caller audio minus the synthetic caller's trailing silence
(TRAIL_MS, as passed by live_providers.run_live), and the first response audio is the provider's first audio
event after the caller stopped speaking (earlier audio is the tail of the previous reply). Gemini Live sends audio and transcripts as one native type
(serverContent) whose payloads are stored as hashes only, so its first audio is the first serverContent at
least GEMINI_AUDIO_BYTES long; that figure is labelled as an estimate.
"""

import json
import sys
import urllib.request
from pathlib import Path

TRAIL_MS = 1200
GEMINI_AUDIO_BYTES = 2000
AUDIO_TYPES = {"response.output_audio.delta", "audioOutput"}


def api(token, path, base="http://127.0.0.1:8766"):
    request = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token})  # noqa: S310 -- fixed local URL
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310  # nosec B310 -- fixed local URL
        return json.load(response)


def events(token, run_id):
    items, cursor = [], 0
    while True:
        page = api(token, f"/api/v1/runs/{run_id}/events?limit=1000&cursor={cursor}")
        items += page["items"]
        if page.get("next_cursor") is None:
            return items
        cursor = page["next_cursor"]


def is_audio(provider, native):
    if provider == "gemini-live":
        return native["native_type"] == "serverContent" and native["payload_bytes"] >= GEMINI_AUDIO_BYTES
    return native["native_type"] in AUDIO_TYPES


def main(path):
    source = Path(path)
    token = Path(".local/host-data/operator-token").read_text().strip()
    results = json.loads(source.read_text())
    definitions = {s["scenario_id"]: s for s in api(token, "/api/v1/scenarios?limit=1000")["items"]}
    first_attempts = results.get("first_attempts", {})
    runs, turns = [], []
    for key, cell in results["cells"].items():
        scenario = definitions[cell["scenario_id"]]
        definition, presentation = scenario["definition"], scenario["presentation"] or {}
        prohibited = {a for t in definition["turns"] for a in t.get("prohibited_actions", [])}
        ledger = events(token, cell["run_id"]) if cell.get("run_id") else []
        requested = {e["content"]["call_id"]: e["content"].get("tool") for e in ledger if e["type"] == "tool.requested"}
        executed = [requested.get(e["content"]["call_id"]) for e in ledger if e["type"] == "tool.completed"]
        denied = [requested.get(e["content"]["call_id"]) for e in ledger if e["type"] == "tool.denied"]
        expected = list(definition.get("expected_tool_sequence") or [])
        at, matched = 0, 0
        for tool in executed:  # expected tools found in order among executed calls
            if at < len(expected) and tool == expected[at]:
                at, matched = at + 1, matched + 1
        assessed = [e["content"] for e in ledger if e["type"] == "assessment.independent_asr"]
        assurance = cell.get("assurance") or {}
        completed = cell.get("state") == "completed"
        runs.append({
            "key": key, "scenario_id": cell["scenario_id"], "scenario": presentation.get("title", cell["scenario_id"]),
            "group": presentation.get("group", "Other"), "provider": cell["provider"], "run_id": cell.get("run_id"),
            "audio_profile": definition["audio_profile"], "locale": definition.get("locale"),
            "persona": definition.get("persona"), "completed": completed,
            "first_try": completed and key not in first_attempts,
            "outcome": assurance.get("overall_assurance") if completed else "run_failed",
            "model_behaviour": assurance.get("model_behaviour") if completed else None,
            "platform_enforcement": assurance.get("platform_enforcement") if completed else None,
            "prohibited_executed": sum(t in prohibited for t in executed),
            "prohibited_denied": sum(t in prohibited for t in denied),
            "expected_tools": len(expected), "expected_tools_matched": matched,
            "replies_assessed": len(assessed),
            "replies_unsafe": sum(a["disposition"] == "unsafe" for a in assessed),
            "replies_untestable": sum(a["disposition"] == "untestable" for a in assessed),
        })
        natives = [e["content"] for e in ledger if e["type"] == "adapter.native_event"]
        # Ledger events carry monotonic_ns on the same process clock as received_ms (checked to within a few ms).
        tool_ms = [e["monotonic_ns"] / 1e6 for e in ledger if e["type"] == "tool.requested"]
        inputs = [e["content"] for e in ledger if e["type"] == "input.caller_audio"]
        for index, turn in enumerate(inputs):
            speech_end = turn["stream_ended_ms"] - TRAIL_MS
            window_end = inputs[index + 1]["stream_started_ms"] if index + 1 < len(inputs) else float("inf")
            audio = [n["received_ms"] for n in natives
                     if is_audio(cell["provider"], n) and speech_end <= n["received_ms"] < window_end]
            # Provider audio while the caller was speaking: a reply still streaming (or a barge-in), so the next audio
            # cannot be attributed to this turn and the turn is left out of latency.
            overlapped = any(is_audio(cell["provider"], n) and turn["stream_started_ms"] <= n["received_ms"] < speech_end
                             for n in natives)
            turns.append({
                "key": f"{key}|{index}", "run_key": key, "scenario_id": cell["scenario_id"], "provider": cell["provider"],
                "audio_profile": definition["audio_profile"], "turn": index,
                "first_audio_ms": (audio[0] - speech_end) if audio and not overlapped else None,
                "overlapped": overlapped,
                # The model called a tool before it said anything: latency then includes the tool round trip.
                "tool_first": bool(audio) and any(speech_end <= t < audio[0] for t in tool_ms),
                "method": "estimated_from_payload_size" if cell["provider"] == "gemini-live" else "native_audio_event",
            })
    (source.parent / "runs.json").write_text(json.dumps(runs, indent=1))
    (source.parent / "turns.json").write_text(json.dumps(turns, indent=1))
    print(f"{len(runs)} runs, {len(turns)} turns")


if __name__ == "__main__":
    main(sys.argv[1])
