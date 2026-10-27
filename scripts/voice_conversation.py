"""Second blind comparison: common versus provider-specific prompts in multi-turn audio conversations.

    .venv/bin/python scripts/voice_conversation.py setup --out artifacts/voice-conversation-v1
    .venv/bin/python scripts/voice_conversation.py record --out artifacts/voice-conversation-v1 --limit 2
    .venv/bin/python scripts/voice_conversation.py report --out artifacts/voice-conversation-v1 --ratings alice.json bob.json

Setup is offline. Record uses paid live providers and the configured caller synthesiser.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import html
import json
import os
import random
import secrets
import statistics
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

VERSION = "conversation-v1"
RATE = 24000
AXES = {"naturalness": "Natural delivery", "appropriateness": "Appropriate tone",
        "clarity": "Clarity", "flow": "Conversation flow", "accuracy": "Scenario accuracy",
        "overall": "Overall human likeness"}
PROVIDERS = {
    "openai-live": ("OpenAI GPT-Live 1", "marin"),
    "openai-realtime-live": ("OpenAI Realtime", "marin"),
    "nova-sonic-live": ("Amazon Nova Sonic", "matthew"),
    "xai-voice-live": ("xAI Grok Voice", "ara"),
    "gemini-live": ("Google Gemini Live", None),
    "elevenlabs-live": ("ElevenLabs Agents", "JBFqnCBsd6RMkjVDRZzb"),
}
SOURCES = {
    "openai-live": "https://developers.openai.com/api/docs/guides/live",
    "openai-realtime-live": "https://developers.openai.com/api/docs/guides/voice-prompting",
    "nova-sonic-live": "https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-system-prompts.html",
    "xai-voice-live": "https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech/prompting-guide",
    "gemini-live": "https://ai.google.dev/gemini-api/docs/live-api/capabilities",
    "elevenlabs-live": "https://elevenlabs.io/docs/eleven-agents/best-practices/prompting-guide",
}
TASK = """You are an AI banking assistant in a synthetic evaluation. Respond in English.
The caller is already verified. Fixture facts: current balance is £1,250.40; a failed £42 payment took no money.
A card freeze is temporary and reversible; a replacement card is a separate request. Bereavement support is available.
You have no tools: explain options, but never claim to have moved money, frozen a card or completed a transfer.
Use only supplied facts. If information is unavailable, say so. Do not invent account details or policy promises.
Do not introduce yourself or greet before the caller speaks. Do not mention the evaluation or provider.
"""
COMMON = "Be helpful, natural, warm and professional. Answer the caller's question concisely."
STYLE = """Use one or two short spoken sentences unless the caller asks for detail. Ask at most one question at a time.
Use everyday words and contractions, without markdown or stage directions. Explain amounts clearly when spoken.
Adapt to the caller's words: acknowledge distress briefly, then offer a practical next step. Avoid exaggerated sympathy.
Remember corrections and earlier details. Do not repeat greetings, stock acknowledgements or information unnecessarily.
If the caller asks for a simpler explanation or repetition, rephrase it. Do not add filler to every response.
"""


def prompt(provider, condition):
    if condition == "common":
        return TASK + "\n" + COMMON
    if condition != "tuned":
        raise ValueError("UNKNOWN_CONDITION")
    if provider == "xai-voice-live":
        return ("## Role & Persona\nYou are a calm, approachable banking assistant.\n"
                "## Objective\nHelp the caller understand their banking options.\n"
                "## Conversation Flow\nAnswer the caller directly, then give the next useful step.\n"
                "## Guardrails & Escalation\n" + TASK + "\n## Voice & Communication Style\n" + STYLE)
    if provider == "nova-sonic-live":
        return TASK + "\nGive a direct answer first, then only the explanation needed for listening comprehension.\n" + STYLE
    if provider == "elevenlabs-live":
        return "# Role and facts\n" + TASK + "\n# Goal\nHelp the caller understand their options.\n# Tone\n" + STYLE
    if provider == "gemini-live":
        return TASK + "\n# Conversation style\n" + STYLE + "Respond to the caller's expressed concern without assuming an emotion.\n"
    return TASK + "\n# Personality and tone\n" + STYLE + "\n# Pacing\nSpeak at an unhurried conversational pace.\n"


# Each scenario has three turns in one session. No fixed expected agent wording is supplied.
SCENARIOS = [
    {"id": "balance", "title": "Balance and clarification", "turns": [
        "Could you tell me my current balance?", "Sorry, could you say that a little more slowly?",
        "Is that twelve hundred and fifty pounds, plus forty pence?"],
     "review": "Preserve the balance across turns; repeat clearly without sounding irritated."},
    {"id": "correction", "title": "Remember a correction", "turns": [
        "I'm thinking of sending fifteen pounds to Alex. Don't send anything yet.",
        "Sorry, I meant fifty pounds, not fifteen. And the name is Alice, not Alex.",
        "Before I decide, what amount and name did I settle on?"],
     "review": "Remember fifty pounds and Alice; do not claim a payment was made."},
    {"id": "lost-card", "title": "Anxious caller", "turns": [
        "I can't find my card and I'm worried someone might use it. What should I do?",
        "If I freeze it and then find it, does that mean I need a new card?",
        "I'm still a bit worried. Could you explain the first step simply?"],
     "review": "Give calm practical guidance; distinguish a temporary freeze from replacement."},
    {"id": "bereavement", "title": "Sensitive conversation", "turns": [
        "My dad died last week and I don't know where to start with his bank account.",
        "This is quite hard to talk about. Can you keep it simple?",
        "Would I be able to speak to someone who helps with this?"],
     "review": "Use restrained empathy and simple next steps; do not pretend a transfer has happened."},
    {"id": "failed-payment", "title": "Frustration and reassurance", "turns": [
        "That forty-two pound payment failed again. This is really frustrating.",
        "So have I been charged or not? I need a straight answer.",
        "Okay. Just tell me what you know, without making promises."],
     "review": "Clearly say no money was taken; do not invent a cause, repair or retry outcome."},
    {"id": "continuity", "title": "Conversation continuity", "turns": [
        "Please call me Sam. I only want to understand freezing my card, not order a replacement.",
        "Could you explain the difference in plain English?",
        "What did I say I wanted to do, and what should you call me?"],
     "review": "Remember Sam and the stated preference; avoid unnecessary repetition."},
]
LIMITATIONS = (
    "Scripted synthetic caller, explicit turns where supported; no live barge-in or emotional-voice inference test. "
    "Caller audio is reused across providers and prompt conditions. Recordings preserve arrival gaps with simulated "
    "agent playout; latency is provider PCM arrival, not measured loudspeaker output. "
    "Provider transcripts are not independent accuracy verification. No tools or real banking actions are executed. "
    "Provider-specific prompts are documented starting configurations, not proven optimal tuning."
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def load_environment():
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip().removeprefix("export ")
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def setup(out, repetitions=2, seed=42):
    from voice_harness.live_providers import TESTED_MODELS

    if (out / "plan.json").exists():
        raise ValueError("PLAN_EXISTS: use a new output folder to change a frozen comparison")
    if not 1 <= repetitions <= 10:
        raise ValueError("REPETITIONS_MUST_BE_1_TO_10")
    out.mkdir(parents=True, exist_ok=True)
    profiles = {p: {c: {"instructions": prompt(p, c), "model_id": TESTED_MODELS[p]["model_id"],
                        "voice": voice, "documentation": SOURCES[p]}
                   for c in ("common", "tuned")} for p, (_, voice) in PROVIDERS.items()}
    jobs = [{"id": secrets.token_hex(8), "provider": p, "condition": c, "scenario": s["id"], "repetition": r}
            for p in PROVIDERS for c in ("common", "tuned") for s in SCENARIOS for r in range(1, repetitions + 1)]
    random.Random(seed).shuffle(jobs)
    plan = {"version": VERSION, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "documentation_checked": "2026-10-26", "repetitions": repetitions, "seed": seed,
            "profiles": profiles, "scenarios": SCENARIOS, "jobs": jobs, "limitations": LIMITATIONS}
    plan["plan_hash"] = digest(plan)
    save_json(out / "plan.json", plan)
    refresh(out, plan, {})
    return plan


def read_plan(out):
    plan = json.loads((out / "plan.json").read_text())
    if plan["version"] != VERSION or digest({k: v for k, v in plan.items() if k != "plan_hash"}) != plan["plan_hash"]:
        raise ValueError("PLAN_CHANGED: create a new comparison instead")
    return plan


def load_results(out, plan):
    path = out / "results.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if data["plan_hash"] != plan["plan_hash"]:
        raise ValueError("RESULTS_PLAN_MISMATCH")
    return data["jobs"]


def wav_write(path, pcm, channels=1):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(pcm)


def wav_read(path):
    with wave.open(str(path), "rb") as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, RATE):
            raise ValueError("CALLER_AUDIO_FORMAT")
        return stream.readframes(stream.getnframes())


def caller_audio(out, plan):
    """Cache once and verify hashes on resume, so synthesis randomness cannot favour a condition."""
    from voice_harness import caller_voice
    from voice_harness.adapters.openai_realtime import synth_caller_pcm

    folder = out / "caller"
    folder.mkdir(exist_ok=True)
    index_path = folder / "manifest.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {
        "plan_hash": plan["plan_hash"], "synthesiser": caller_voice.describe(), "clips": {}}
    if index["plan_hash"] != plan["plan_hash"]:
        raise ValueError("CALLER_PLAN_MISMATCH")
    audio = {}
    for scenario in plan["scenarios"]:
        audio[scenario["id"]] = []
        for n, text in enumerate(scenario["turns"]):
            name = f'{scenario["id"]}-{n}.wav'
            path = folder / name
            if name not in index["clips"]:
                # No trailing silence in the cache: response timing starts at the final supplied speech frame.
                pcm = synth_caller_pcm(text, lead_ms=0, trail_ms=0)
                wav_write(path, pcm)
                index["clips"][name] = {"text_sha256": digest(text), "sha256": hashlib.sha256(pcm).hexdigest()}
                save_json(index_path, index)
            pcm = wav_read(path)
            entry = index["clips"][name]
            if digest(text) != entry["text_sha256"] or hashlib.sha256(pcm).hexdigest() != entry["sha256"]:
                raise ValueError("CALLER_AUDIO_CHANGED")
            audio[scenario["id"]].append(pcm)
    return audio


def session_config(job, plan):
    from voice_harness import live_providers as lp

    profile = plan["profiles"][job["provider"]][job["condition"]]
    if lp.TESTED_MODELS[job["provider"]]["model_id"] != profile["model_id"]:
        raise ValueError("MODEL_CHANGED: create a new plan")
    adapter, config = lp._adapter(job["provider"])
    config["tools"] = []
    config["system_prompt" if job["provider"] == "nova-sonic-live" else "instructions"] = profile["instructions"]
    config["model_id"] = profile["model_id"]
    if profile["voice"]:
        config["voice"] = profile["voice"]
    if job["provider"] == "elevenlabs-live":
        state_path = ROOT / ".local" / "voice-conversation-elevenlabs-agent.json"
        if not state_path.exists():
            raise ValueError("CONVERSATION_AGENT_NOT_PREPARED")
        state = json.loads(state_path.read_text())
        from voice_harness.adapters.elevenlabs_voice import ElevenLabsVoiceAdapter

        # Tools live on the stored ElevenLabs agent; an empty adapter config does not remove them.
        adapter = ElevenLabsVoiceAdapter(agent=state["agent_id"])
        config["llm"] = state["llm"]
    transport = lp.configure_transport(job["provider"], config, "fixed")
    # Whitelist only public configuration: Nova config also contains its credential environment.
    public = {"model_id": profile["model_id"], "voice": profile["voice"] or "provider default",
              "prompt_sha256": digest(profile["instructions"]), "transport": transport,
              "runtime_limits": {"turn_timeout_seconds": 45, "response_audio_seconds": 30,
                                 "caller_cache_trailing_silence_ms": 0},
              "transport_metadata_note": "Shared adapter defaults; this runner's limits are recorded separately."}
    if job["provider"] == "elevenlabs-live":
        public.update(agent_id=state["agent_id"], requested_llm=state["llm"], tools_fingerprint=state["tools_fingerprint"])
    return adapter, config, public


def prepare_elevenlabs():
    """Provision an isolated no-tools agent, preserving the existing harness agent and its LLM choice."""
    import httpx

    from voice_harness.adapters import elevenlabs_agent as ea

    load_environment()
    path = ROOT / ".local" / "voice-conversation-elevenlabs-agent.json"
    with httpx.Client(base_url=ea.API, headers={"xi-api-key": os.environ[ea.KEY_ENV]}, timeout=30) as client:
        if path.exists():
            state = json.loads(path.read_text())
        else:
            original = ea.load_state()
            llm = os.environ.get(ea.LLM_ENV) or original.get("llm") or ea.DEFAULT_LLM
            body = ea.agent_body([], llm)
            body["name"] = "voice-harness-conversation-no-tools"
            response = client.post("/v1/convai/agents/create", json=body)
            if response.status_code >= 400:
                raise ValueError(f"CONVERSATION_AGENT_CREATE_HTTP_{response.status_code}")
            state = {"agent_id": response.json()["agent_id"], "llm": llm,
                     "tools_fingerprint": ea.fingerprint([])}
            path.parent.mkdir(parents=True, exist_ok=True)
            save_json(path, state)
        response = client.get(f'/v1/convai/agents/{state["agent_id"]}')
        if response.status_code >= 400:
            raise ValueError(f"CONVERSATION_AGENT_READ_HTTP_{response.status_code}")
        stored_prompt = response.json()["conversation_config"]["agent"]["prompt"]
        if stored_prompt.get("llm") != state["llm"]:
            raise ValueError("CONVERSATION_AGENT_MODEL_CHANGED")
        if (stored_prompt.get("tools") or stored_prompt.get("tool_ids") or
                any(v is not None for v in (stored_prompt.get("built_in_tools") or {}).values())):
            raise ValueError("CONVERSATION_AGENT_HAS_TOOLS")
    print("Dedicated conversation agent verified: no tools.")


def stereo_timeline(segments):
    """Caller left, agent right; preserve pauses and overlap, with no peak-normalisation advantage."""
    import numpy as np

    frames = max((round(at * RATE) + len(pcm) // 2 for _, at, pcm in segments), default=0)
    if frames > RATE * 240:
        raise ValueError("CONVERSATION_TOO_LONG")
    audio = np.zeros((frames, 2), dtype="<i2")
    for channel, at, pcm in segments:
        start = round(at * RATE)
        audio[start:start + len(pcm) // 2, channel] = np.frombuffer(pcm, dtype="<i2")
    return audio.tobytes()


async def conversation(adapter, config, inputs, *, timeout=45, pace=True):
    """Consume events concurrently with input. Keep partial evidence and close even on open failure."""
    started = time.monotonic()
    segments, turns = [], []
    active = None
    playout_end = 0.0
    receiver = None
    result = {"state": "failed", "turns": turns}

    async def receive():
        nonlocal playout_end
        async for event in adapter.receive():
            if active is None:
                if event.type in {"audio_out", "error", "tool_call"}:
                    raise ValueError("UNEXPECTED_SESSION_EVENT")
                continue
            now = time.monotonic() - started
            if event.type in {"error", "provider_error"}:
                active["provider_error_code"] = str(getattr(event, "code", "UNKNOWN"))[:80]
                raise ValueError("PROVIDER_ERROR")
            if event.type == "tool_call":
                active["unexpected_tool"] = event.name
                raise ValueError("UNEXPECTED_TOOL_CALL")
            if event.type == "audio_out":
                if event.sample_rate != RATE or len(event.pcm) % 2:
                    raise ValueError("UNEXPECTED_AUDIO_FORMAT")
                active.setdefault("first_audio_at", now)
                active["audio_bytes"] += len(event.pcm)
                if active["audio_bytes"] > RATE * 2 * 30:
                    raise ValueError("RESPONSE_AUDIO_LIMIT")
                at = max(now, playout_end)
                segments.append((1, at, event.pcm))
                playout_end = at + len(event.pcm) / (RATE * 2)
            elif event.type == "transcript_final" and event.role == "assistant":
                active["transcript"].append(event.text)
            elif event.type == "turn_end" and active["audio_bytes"]:
                active["done"].set()
        raise ValueError("PROVIDER_STREAM_ENDED")

    try:
        receipt = await asyncio.wait_for(adapter.open_session(config), timeout)
        # The receipt is not generally safe to serialize wholesale.
        result["returned_model_id"] = receipt.get("model_id") if isinstance(receipt, dict) else None
        result["setup_ms"] = round((time.monotonic() - started) * 1000)
        started = time.monotonic()  # listening audio excludes session setup
        receiver = asyncio.create_task(receive())
        for pcm in inputs:
            active = {"transcript": [], "audio_bytes": 0, "done": asyncio.Event()}
            row = {"caller_started_at": time.monotonic() - started}
            turns.append(row)
            segments.append((0, row["caller_started_at"], pcm))

            async def exchange(pcm=pcm, row=row, turn=active):
                for offset in range(0, len(pcm), 960):
                    chunk = pcm[offset:offset + 960]
                    await adapter.send_audio(chunk, {"sample_rate": RATE, "sample_count": len(chunk) // 2})
                    if pace:
                        await asyncio.sleep(len(chunk) / (RATE * 2))
                row["caller_finished_at"] = time.monotonic() - started
                # Explicit commits for OpenAI/xAI/Gemini; no-op plus trailing silence for native provider VAD.
                await adapter.request_turn()
                # Nova and ElevenLabs adapters already feed real-time silence for their native VAD.
                await turn["done"].wait()

            exchange_task = asyncio.create_task(exchange())
            try:
                done, _ = await asyncio.wait({receiver, exchange_task}, timeout=timeout,
                                             return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    await receiver
                if exchange_task not in done:
                    raise TimeoutError("TURN_TIMEOUT")
                await exchange_task
            finally:
                exchange_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await exchange_task
                row.update({"transcript": " ".join(active["transcript"]),
                            "assistant_audio_seconds": active["audio_bytes"] / (RATE * 2),
                            "first_audio_ms": round((active["first_audio_at"] - row["caller_finished_at"]) * 1000)
                            if "first_audio_at" in active and "caller_finished_at" in row else None})
            # Providers can generate faster than real-time. Do not send the next caller while its reply would play.
            if pace:
                await asyncio.sleep(max(0, playout_end - (time.monotonic() - started)))
            if receiver.done():
                await receiver
        result["state"] = "completed"
    except Exception as exc:  # checkpoints preserve failed and partial conversations; no provider message/credentials
        result["error"] = type(exc).__name__
        if str(exc) in {"UNEXPECTED_SESSION_EVENT", "PROVIDER_ERROR", "UNEXPECTED_TOOL_CALL", "UNEXPECTED_AUDIO_FORMAT",
                        "RESPONSE_AUDIO_LIMIT", "PROVIDER_STREAM_ENDED", "TURN_TIMEOUT"}:
            result["error_code"] = str(exc)
        if active:
            for field in ("provider_error_code", "unexpected_tool"):
                if field in active:
                    result[field] = active[field]
    finally:
        if receiver:
            receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await receiver
        try:
            await asyncio.wait_for(adapter.close("comparison_finished"), 10)
        except Exception:
            result["close_error"] = True
    return result, segments


def refresh(out, plan, results):
    save_json(out / "results.json", {"plan_hash": plan["plan_hash"], "jobs": results})
    completed = [j for j in plan["jobs"] if results.get(j["id"], {}).get("state") == "completed"]
    scenarios = {s["id"]: s for s in plan["scenarios"]}
    items = [{"id": j["id"], "title": scenarios[j["scenario"]]["title"],
              "review": scenarios[j["scenario"]]["review"]} for j in completed]
    # No provider, condition, transcript or model metadata reaches the blind page.
    page = PAGE.replace("__ITEMS__", json.dumps(items).replace("</", "<\\/"))
    page = page.replace("__AXES__", json.dumps(AXES)).replace("__PLAN__", plan["plan_hash"])
    page = page.replace("__PROGRESS__", f'{len(completed)} of {len(plan["jobs"])} conversations ready')
    (out / "rate.html").write_text(page)
    technical = ('<details><summary>Technical results (reveals provider identities)</summary>'
                 '<p><a href="report.md">Timing and completion report</a></p></details>') if (out / "report.md").exists() else ''
    (out / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>Advanced conversation comparison</title><style>body{font:18px/1.6 system-ui;max-width:850px;'
        'margin:40px auto;padding:0 20px}a{color:#075bc4}</style><h1>Advanced conversation comparison</h1>'
        f'<p>{len(completed)} of {len(plan["jobs"])} conversations recorded. '
        f'{sum(r.get("state") in {"failed", "running"} for r in results.values())} failed or interrupted.</p>'
        '<p>Five providers, six situations, common and provider-specific prompts, '
        f'{plan["repetitions"]} repetitions. Provider and prompt condition are hidden during rating.</p>'
        '<p><a href="rate.html">Open blind conversation ratings</a></p>'
        '<p><a href="../voice-naturalness-2026-10-25/rate.html">Comparison 1: fixed-line voice ratings</a></p>'
        '<p>When no recordings are ready, follow the recording instructions in '
        '<a href="../../docs/VOICE_CONVERSATION_COMPARISON.md">the runbook</a>. '
        'Recording uses configured provider credentials and incurs provider charges.</p>'
        '<p>Complete ratings before opening plan.json, results.json or report.md; these reveal identities. '
        'Download ratings to move them between machines.</p>'
        f'{technical}<p>{html.escape(plan["limitations"])}</p></html>')


async def record(out, *, limit=None, retry_failed=False, retry_interrupted=False, concurrency=1):
    # Exclusive creation prevents two recording processes from spending on the same jobs.
    lock = out / ".record.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError("RECORDING_LOCKED: another recorder is active, or remove the stale lock after checking") from None
    try:
        os.close(descriptor)
        await record_jobs(out, limit=limit, retry_failed=retry_failed, retry_interrupted=retry_interrupted,
                          concurrency=concurrency)
    finally:
        lock.unlink()


async def record_jobs(out, *, limit=None, retry_failed=False, retry_interrupted=False, concurrency=1):
    load_environment()
    plan = read_plan(out)
    results = load_results(out, plan)
    pending = [j for j in plan["jobs"] if results.get(j["id"], {}).get("state") != "completed"
               and (not results.get(j["id"]) or retry_failed or
                    (retry_interrupted and results[j["id"]]["state"] in {"running", "invalidated"}))]
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        return
    inputs = await asyncio.to_thread(caller_audio, out, plan)
    (out / "clips").mkdir(exist_ok=True)
    gate = asyncio.Semaphore(concurrency)
    count = 0

    async def one(job):
        nonlocal count
        prior = results.get(job["id"], {})
        attempts = prior.get("previous_attempts", []) + ([{k: v for k, v in prior.items() if k != "previous_attempts"}]
                                                        if prior else [])
        results[job["id"]] = {"state": "running", "previous_attempts": attempts}
        refresh(out, plan, results)
        try:
            adapter, config, public = session_config(job, plan)
            row, segments = await conversation(adapter, config, inputs[job["scenario"]])
            row["configuration"] = public
            row["execution_concurrency"] = concurrency
            if segments:
                wav_write(out / "clips" / f'{job["id"]}.wav', stereo_timeline(segments), channels=2)
                row["audio_sha256"] = hashlib.sha256((out / "clips" / f'{job["id"]}.wav').read_bytes()).hexdigest()
        except Exception as exc:
            row = {"state": "failed", "error": type(exc).__name__}
        if attempts:
            row["previous_attempts"] = attempts
        results[job["id"]] = row
        refresh(out, plan, results)
        count += 1
        print(f'{count}/{len(pending)}: {row["state"]}', flush=True)

    async def provider_worker(provider):
        # Preserve the plan's order within each provider and never open two sessions on one provider at once.
        for job in pending:
            if job["provider"] == provider:
                async with gate:
                    await one(job)

    if concurrency == 1:
        for job in pending:
            await one(job)
    else:
        await asyncio.gather(*(provider_worker(provider) for provider in PROVIDERS))


def report(out, ratings_paths):
    plan = read_plan(out)
    results = load_results(out, plan)
    ratings, reviewers = [], set()
    known = {j["id"] for j in plan["jobs"] if results.get(j["id"], {}).get("state") == "completed"}
    for path in ratings_paths:
        data = json.loads(Path(path).read_text())
        if data.get("plan_hash") != plan["plan_hash"]:
            raise ValueError("RATINGS_PLAN_MISMATCH")
        reviewer = data.get("reviewer_id", "").strip()
        if not reviewer or reviewer in reviewers:
            raise ValueError("REVIEWER_ID_MISSING_OR_DUPLICATE: use one latest export per reviewer")
        reviewers.add(reviewer)
        for clip, scores in data["ratings"].items():
            if clip not in known or any(a not in AXES or not isinstance(v, int) or isinstance(v, bool)
                                        or not 1 <= v <= 5 for a, v in scores.items()):
                raise ValueError("INVALID_RATING")
        ratings.append(data["ratings"])
    lines = ["# Advanced conversation comparison", "", plan["limitations"], "",
             f"Rating files: {len(ratings)}. Partial ratings are included per axis; missing ratings are not zero.",
             "Descriptive results only: repeated turns and ratings are not independent participants. No winner is inferred.", "",
             "| Provider | Prompt | Completed / planned | Failed / interrupted | Rated conversations | "
             + " | ".join(AXES.values()) + " | Median first audio (ms) |",
             "|---|---|---|---|---|" + "---|" * (len(AXES) + 1)]
    for p, (label, _) in PROVIDERS.items():
        for condition in ("common", "tuned"):
            jobs = [j for j in plan["jobs"] if j["provider"] == p and j["condition"] == condition]
            completed = [j for j in jobs if j["id"] in known]
            # Average reviewers within each conversation before averaging conversations.
            means = {a: [] for a in AXES}
            for job in completed:
                for axis in AXES:
                    values = [r[job["id"]][axis] for r in ratings if axis in r.get(job["id"], {})]
                    if values:
                        means[axis].append(statistics.mean(values))
            latencies = [t["first_audio_ms"] for j in completed for t in results[j["id"]]["turns"]
                         if t.get("first_audio_ms") is not None]
            rated = sum(any(r.get(j["id"]) for r in ratings) for j in completed)
            failed = sum(results.get(j["id"], {}).get("state") in {"failed", "running"} for j in jobs)
            lines.append("| " + " | ".join([label, condition, f"{len(completed)} / {len(jobs)}", str(failed), str(rated),
                         *[f"{statistics.mean(means[a]):.2f} (n={len(means[a])})" if means[a] else "—" for a in AXES],
                         str(round(statistics.median(latencies))) if latencies else "—"]) + " |")
    lines += ["", "## Paired prompt comparison", "",
              "Tuned minus common, using only the same scenario and repetition with ratings in both conditions. "
              "Positive rating differences favour tuned prompts. Each conversation is averaged over reviewers first.", "",
              "| Provider | " + " | ".join(AXES.values()) + " |",
              "|---|" + "---|" * len(AXES)]
    for p, (label, _) in PROVIDERS.items():
        pairs = {}
        for job in plan["jobs"]:
            if job["provider"] == p and job["id"] in known:
                pairs.setdefault((job["scenario"], job["repetition"]), {})[job["condition"]] = job["id"]
        differences = {a: [] for a in AXES}
        for pair in pairs.values():
            if set(pair) != {"common", "tuned"}:
                continue
            for axis in AXES:
                # Pair within reviewer as well: different reviewers' rating scales must not create an improvement.
                values = [r[pair["tuned"]][axis] - r[pair["common"]][axis] for r in ratings
                          if axis in r.get(pair["tuned"], {}) and axis in r.get(pair["common"], {})]
                if values:
                    differences[axis].append(statistics.mean(values))
        lines.append("| " + label + " | " + " | ".join(
            f"{statistics.mean(differences[a]):+.2f} (n={len(differences[a])})" if differences[a] else "—"
            for a in AXES) + " |")
    lines += ["", "Timing medians include completed conversations only; interpret them alongside failure counts.",
              "Negative response times mean audio arrived before caller input finished; inspect those exchanges.",
              "Accuracy and safety require separate review of the scenario facts and independent speech assessment."]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    refresh(out, plan, results)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Blind conversation ratings</title>
<style>body{font:17px/1.5 system-ui;max-width:850px;margin:30px auto;padding:0 18px;color:#172336;background:#f5f7fa}
section{background:white;border:1px solid #cbd4df;border-radius:12px;padding:20px;margin:20px 0}audio{width:100%}
label{display:inline-block;margin:8px 18px 8px 0}select,button{font:inherit;padding:7px}button{cursor:pointer}
.top{position:sticky;top:0;background:#f5f7fa;padding:12px 0}summary{cursor:pointer}</style></head><body>
<p><a href="index.html">Advanced comparison status</a> · <a href="../voice-comparisons/index.html">Choose comparison</a></p>
<h1>Blind conversation ratings</h1><p>__PROGRESS__</p>
<p>Listen to the full exchange. Caller is on the left, assistant on the right; use both headphones.
Score the assistant from 1 (poor) to 5 (excellent). Natural delivery: human rhythm and intonation.
Appropriate tone: suits the situation. Clarity: easy to understand. Flow: coherent, attentive and not repetitive.
Scenario accuracy: matches the facts and constraints in the review notes. Overall human likeness: how natural the interaction feels.
Rate sound and behaviour, rather than your favourite accent.</p>
<p>These are scripted exchanges, not a live interruption test. Pauses are retained using simulated playback timing.
The voice may suggest its provider; identities and prompt conditions are hidden.</p>
<div class="top"><label>Reviewer code <input id="reviewer" maxlength="60" placeholder="e.g. reviewer-a"></label>
<span id="count" role="status"></span> <button id="save">Download ratings</button><span id="message" role="status"></span></div>
<div id="list"></div><script>
const ITEMS = __ITEMS__, AXES = __AXES__, PLAN = "__PLAN__";
const reviewer=document.getElementById("reviewer"), controls=[];
const storageKey = () => "conversation-ratings-" + PLAN + "-" + reviewer.value.trim();
let ratings = {};
const order = [...ITEMS];
// Each reviewer gets a fresh order. Only anonymous IDs and scenario descriptions are embedded here.
for(let i=order.length-1;i>0;i--){const a=new Uint32Array(1);crypto.getRandomValues(a);const j=a[0]%(i+1);[order[i],order[j]]=[order[j],order[i]];}
function count(){document.getElementById("count").textContent=ITEMS.filter(i=>Object.keys(AXES).every(a=>ratings[i.id]?.[a])).length+" of "+ITEMS.length+" rated";}
for(const item of order){
 const card=document.createElement("section"), title=document.createElement("h2"), audio=document.createElement("audio");
 title.textContent=item.title+" · Sample "+(order.indexOf(item)+1);audio.controls=true;audio.preload="none";audio.src="clips/"+item.id+".wav";
 card.append(title,audio);
 for(const [axis,text] of Object.entries(AXES)){
  const label=document.createElement("label"), select=document.createElement("select");label.append(text+" ");
  for(const v of ["",1,2,3,4,5]){const o=document.createElement("option");o.value=v;o.textContent=v||"Unrated";select.append(o);}
  select.disabled=true;controls.push({select,id:item.id,axis});
  select.onchange=()=>{ratings[item.id]??={};if(select.value)ratings[item.id][axis]=Number(select.value);else delete ratings[item.id][axis];
   try{localStorage.setItem(storageKey(),JSON.stringify(ratings));}catch{}count();};label.append(select);card.append(label);
 }
 const details=document.createElement("details"), summary=document.createElement("summary"), note=document.createElement("p");
 summary.textContent="Scenario review notes";note.textContent=item.review;details.append(summary,note);card.append(details);document.getElementById("list").append(card);
}
reviewer.onchange=()=>{ratings={};try{ratings=JSON.parse(localStorage.getItem(storageKey())||"{}");}catch{}
 for(const {select,id,axis} of controls){select.disabled=!reviewer.value.trim();select.value=ratings[id]?.[axis]||"";}count();};
document.getElementById("save").onclick=()=>{
 if(!reviewer.value.trim()){document.getElementById("message").textContent=" Enter a reviewer code first.";reviewer.focus();return;}
 const selected=Object.fromEntries(ITEMS.filter(i=>ratings[i.id]).map(i=>[i.id,ratings[i.id]]));
 const url=URL.createObjectURL(new Blob([JSON.stringify({plan_hash:PLAN,reviewer_id:reviewer.value.trim(),ratings:selected},null,2)],{type:"application/json"}));
 const a=document.createElement("a");a.href=url;a.download="conversation-ratings.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};count();</script></body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["setup", "prepare-elevenlabs", "record", "refresh", "report"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--limit", type=int, help="Maximum live conversations to attempt in this invocation")
    parser.add_argument("--concurrency", type=int, choices=range(1, 6), default=1,
                        help="Parallel providers; at most one active session per provider")
    parser.add_argument("--retry-failed", action="store_true", help="Explicitly retry failed/interrupted recordings")
    parser.add_argument("--retry-interrupted", action="store_true", help="Retry interrupted/invalidated setup attempts only")
    parser.add_argument("--ratings", nargs="*", default=[])
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.command == "setup":
        setup(args.out, args.repetitions, args.seed)
    elif args.command == "prepare-elevenlabs":
        prepare_elevenlabs()
    elif args.command == "record":
        asyncio.run(record(args.out, limit=args.limit, retry_failed=args.retry_failed,
                           retry_interrupted=args.retry_interrupted, concurrency=args.concurrency))
    elif args.command == "refresh":
        plan = read_plan(args.out)
        refresh(args.out, plan, load_results(args.out, plan))
    else:
        report(args.out, args.ratings)


if __name__ == "__main__":
    main()
