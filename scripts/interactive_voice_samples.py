"""Record one bounded, adaptive balance conversation per provider and export a local listening report."""

import argparse
import base64
import html
import json
import os
import secrets
import wave
from concurrent.futures import as_completed
from pathlib import Path

PROVIDER_NAMES = {"openai-realtime-live": "OpenAI Realtime", "xai-voice-live": "xAI Grok Voice",
                  "gemini-live": "Google Gemini Live", "nova-sonic-live": "Amazon Nova Sonic",
                  "elevenlabs-live": "ElevenLabs Agents"}


def load_env(path):
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ[key] = value.strip().strip("\"'")


def wav_export(path, clips):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        for pcm in clips:
            stream.writeframes(pcm)
            stream.writeframes(bytes(24000))


def render(rows, output):
    cards = []
    for row in rows:
        player = "<p>No conversation audio was captured.</p>"
        if row.get("audio_file"):
            audio = base64.b64encode((output / row["audio_file"]).read_bytes()).decode()
            player = f'<audio controls preload="none" src="data:audio/wav;base64,{audio}"></audio>'
        lines = "".join(f'<p><strong>{html.escape(t["speaker"])}:</strong> {html.escape(t["text"])}</p>'
                        for t in row.get("conversation", []))
        status = {"objective_response_delivered": "Balance delivered",
                  "target_audio_not_released": "Stopped: final agent reply held by assessment"}.get(
                      row.get("stop_reason"), row.get("stop_reason") or row["state"])
        issues = ", ".join(row.get("assessment_issues", []))
        cards.append(f'<section><h2>{html.escape(PROVIDER_NAMES.get(row["provider"], row["provider"]))}</h2>'
                     f'<p>{html.escape(row.get("model_id") or "Model not opened")} · '
                     f'Agent voice: {html.escape(row.get("agent_voice") or "provider default")}</p>'
                     f'<p><strong>{html.escape(status)}</strong> · '
                     f'{html.escape(row.get("turn_control") or "not opened")} · '
                     f'Evidence verified: {row.get("bundle_verified", False)}</p>'
                     + (f'<p>Assessment notes: {html.escape(issues)}</p>' if issues else '') + player +
                     f'<details><summary>Conversation transcript</summary>{lines}</details></section>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Voice provider listening samples</title><style>'
            'body{font:17px/1.6 system-ui;background:#f3f5f7;color:#152236;max-width:1000px;margin:40px auto;padding:0 24px}'
            'section{background:white;padding:24px;margin:22px 0;border:1px solid #d4dde6;border-radius:12px}'
            'h1,h2{line-height:1.25}audio{width:100%;margin:12px 0}summary{cursor:pointer}strong{font-weight:650}'
            '</style><h1>Four voice providers · listening samples</h1>'
            '<p>Customer: Cedar (male), anxious/cooperative persona. Same synthetic balance enquiry and fixture seed 42. '
            'Each customer adapts to the agent, so wording varies. This is one sample per provider, not a ranking.</p>'
            '<p>Recorded turns are joined with short gaps; generation and assessment waits are excluded. '
            'Only released agent audio is included. Customer transcripts are provider-reported; '
            'agent transcripts are independently assessed. Nova uses patient automatic speech detection; '
            'the other providers use explicit turn boundaries.</p>' + "".join(cards) + '</html>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--providers", default="openai-realtime-live,xai-voice-live,gemini-live,nova-sonic-live")
    args = parser.parse_args()
    load_env(args.env_file)
    os.environ["VOICE_HARNESS_LIVE_ASR"] = "openai"
    os.environ.pop("HARNESS_AUTO_JUDGES", None)
    from fastapi.testclient import TestClient

    from voice_harness import live_providers
    from voice_harness.api import create_app
    from voice_harness.evidence import verify_bundle
    from voice_harness.signing import KeyStore
    from voice_harness.storage import Database

    args.output.mkdir(parents=True, exist_ok=True)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    database = Database(str(args.data_dir.resolve() / "samples.sqlite"))
    app = create_app(database, args.data_dir.resolve(), recover=False)
    token = (args.data_dir / "operator-token").read_text().strip()
    headers = {"Authorization": "Bearer " + token}
    rows, pending = [], {}

    def save():
        (args.output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
        (args.output / "listen.html").write_text(render(rows, args.output))

    with TestClient(app) as client:
        for provider in args.providers.split(","):
            missing = live_providers.requirements(provider, caller={"mode": "live_ai"})
            if missing:
                rows.append({"provider": provider, "state": "unavailable", "stop_reason": ",".join(missing)})
                print(json.dumps(rows[-1]), flush=True)
                continue
            response = client.post("/api/v1/runs", headers={**headers, "Idempotency-Key": secrets.token_hex(16)}, json={
                "scenario_id": "happy-balance-clean-001", "provider_config_id": provider, "fixture_seed": 42,
                "retain_audio": True, "caller": {"mode": "live_ai", "voice": "cedar", "persona": "anxious",
                                               "max_turns": 6, "max_duration_seconds": 120}})
            if response.status_code != 202:
                rows.append({"provider": provider, "state": "launch_failed", "stop_reason": str(response.status_code)})
                continue
            run_id = response.json()["run_ids"][0]
            pending[app.state.jobs[run_id]] = (provider, run_id)
            print(json.dumps({"provider": provider, "run_id": run_id, "stage": "started"}), flush=True)
        save()
        for future in as_completed(pending, timeout=180):
            provider, run_id = pending[future]
            worker_error = None
            try:
                future.result()
            except Exception as exc:  # preserve other providers' samples if one worker fails
                worker_error = type(exc).__name__
            run = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
            bundle = client.get(f"/api/v1/runs/{run_id}/evidence", headers=headers).json()
            (args.data_dir / (run_id + "-bundle.json")).write_text(json.dumps(bundle))
            state = database.get(run_id)
            events = bundle["events"]
            binding = next((e["content"]["provider"] for e in events if e["type"] == "run.live_binding"), {})
            ended = next((e["content"] for e in reversed(events) if e["type"] == "caller.session_ended"), {})
            row = {"provider": provider, "run_id": run_id, "state": run["state"], "worker_error": worker_error,
                   "model_id": binding.get("returned_model_id") or binding.get("requested_model_id"),
                   "customer_voice": "cedar", "agent_voice": binding.get("requested_voice"),
                   "turn_control": binding.get("caller_turn_control"),
                   "stop_reason": ended.get("reason") or run["termination_reason"],
                   "assurance": run["assurance"], "bundle_verified": verify_bundle(
                       bundle, keyring=KeyStore(args.data_dir).public_keys()), "conversation": []}
            row["assessment_issues"] = sorted({reason for e in events
                                               if e["type"] == "assessment.independent_asr"
                                               and e["content"].get("disposition") != "safe"
                                               for reason in e["content"].get("reason_codes", [])})
            clips = []
            for event in events:
                if event["type"] not in {"caller.utterance", "caller.agent_audio"}:
                    continue
                c = event["content"]
                customer = event["type"] == "caller.utterance"
                text = c["text"] if customer else state["units"][c["unit_id"]]["transcript"]
                row["conversation"].append({"speaker": "Customer" if customer else "Agent", "text": text})
                artifact = next((a for a in bundle["artifacts"] if a["sha256"] == c["pcm_sha256"] and a.get("data_hex")), None)
                if artifact:
                    clips.append(bytes.fromhex(artifact["data_hex"]))
            if clips:
                row["audio_file"] = provider + ".wav"
                wav_export(args.output / row["audio_file"], clips)
            rows.append(row)
            save()
            print(json.dumps({k: row[k] for k in ["provider", "state", "stop_reason", "bundle_verified"]}), flush=True)


if __name__ == "__main__":
    main()
