"""ElevenLabs agent provisioning for the harness.

An ElevenLabs conversation runs against a stored agent: tools and the audio formats live on the agent, not in the
session config. This module creates (or syncs) one harness agent whose client tools mirror the live tool list, and
records its id and a fingerprint of those tools in ``.local/elevenlabs-agent.json``. The prompt, first message and
voice are sent per session as overrides, so one agent serves every scenario and prompt profile.

Usage (key from ``ELEVENLABS_API_KEY``; never printed):
    PYTHONPATH=src python -m voice_harness.adapters.elevenlabs_agent setup [--llm gemini-2.5-flash]
    PYTHONPATH=src python -m voice_harness.adapters.elevenlabs_agent status
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

KEY_ENV = "ELEVENLABS_API_KEY"
AGENT_ENV = "ELEVENLABS_AGENT_ID"
LLM_ENV = "ELEVENLABS_AGENT_LLM"
API = "https://api.elevenlabs.io"
DEFAULT_LLM = "gemini-2.5-flash"
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # ElevenLabs premade voice "George"; overridable per session
AGENT_NAME = "voice-harness-bakeoff"
AUDIO_FORMAT = "pcm_24000"  # in and out, so the harness's 24 kHz PCM needs no resampling
STATE_PATH = Path(__file__).resolve().parents[3] / ".local" / "elevenlabs-agent.json"
_SCALARS = {"string", "number", "integer", "boolean"}


def state_path(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    return Path(environ.get("ELEVENLABS_AGENT_STATE") or STATE_PATH)


def load_state(environ=None) -> dict[str, Any]:
    try:
        return json.loads(state_path(environ).read_text())
    except (OSError, ValueError):
        return {}


def agent_id(environ=None) -> str | None:
    environ = os.environ if environ is None else environ
    return environ.get(AGENT_ENV) or load_state(environ).get("agent_id")


def _property(schema: dict[str, Any], name: str) -> dict[str, Any]:
    """One tool parameter in the subset ElevenLabs accepts: a scalar type and a description."""
    kind = schema.get("type")
    if kind is None:  # pydantic Optional[X] -> anyOf [X, null]
        kind = next((o.get("type") for o in schema.get("anyOf", []) if o.get("type") not in (None, "null")), "string")
    out: dict[str, Any] = {"type": kind if kind in _SCALARS else "string",
                           "description": schema.get("description") or schema.get("title") or name}
    if isinstance(schema.get("enum"), list):
        out["enum"] = schema["enum"]
    return out


def client_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for tool in tools:
        parameters = tool.get("parameters") or {}
        properties = {n: _property(s, n) for n, s in (parameters.get("properties") or {}).items()}
        out.append({
            "type": "client", "name": tool["name"], "description": tool.get("description") or tool["name"],
            "expects_response": True, "response_timeout_secs": 30,
            "parameters": {"type": "object", "properties": properties,
                           "required": [r for r in parameters.get("required", []) if r in properties]},
        })
    return out


def fingerprint(tools: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(client_tools(tools), sort_keys=True).encode()).hexdigest()[:16]


def agent_body(tools: list[dict[str, Any]], llm: str, instructions: str = "", voice: str = DEFAULT_VOICE) -> dict[str, Any]:
    return {
        "name": AGENT_NAME,
        "conversation_config": {
            "agent": {"first_message": "", "language": "en",
                      "prompt": {"prompt": instructions or "You are a bank phone assistant.", "llm": llm,
                                 "temperature": 0, "tools": client_tools(tools)}},
            "tts": {"voice_id": voice, "agent_output_audio_format": AUDIO_FORMAT},
            "asr": {"user_input_audio_format": AUDIO_FORMAT},
            "turn": {"turn_eagerness": "normal", "turn_timeout": -1},  # -1: never prompt a silent caller
        },
        "platform_settings": {"overrides": {"conversation_config_override": {
            "agent": {"prompt": {"prompt": True, "llm": True}, "first_message": True, "language": True},
            "tts": {"voice_id": True}}}},
    }


def setup(tools: list[dict[str, Any]], *, llm: str | None = None, environ=None, client=None) -> dict[str, Any]:
    """Create the harness agent, or update it when it exists, so its tools match ``tools``."""
    import httpx

    environ = os.environ if environ is None else environ
    key = environ.get(KEY_ENV)
    if not key:
        raise ValueError("NO_ELEVENLABS_API_KEY")
    state = load_state(environ)
    llm = llm or environ.get(LLM_ENV) or state.get("llm") or DEFAULT_LLM
    body = agent_body(tools, llm)
    http = client or httpx.Client(base_url=API, headers={"xi-api-key": key}, timeout=30)
    existing = environ.get(AGENT_ENV) or state.get("agent_id")
    if existing:
        response = http.patch(f"/v1/convai/agents/{existing}", json=body)
    else:
        response = http.post("/v1/convai/agents/create", json=body)
    if response.status_code >= 400:  # the body names the invalid field; it never echoes the key
        raise ValueError(f"ELEVENLABS_HTTP_{response.status_code}: {response.text[:600]}")
    new_id = existing or response.json()["agent_id"]
    state = {"agent_id": new_id, "llm": llm, "tools_fingerprint": fingerprint(tools), "audio_format": AUDIO_FORMAT}
    path = state_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")
    return state


def main(argv=None) -> int:
    from ..live_providers import TOOLS

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="create or sync the harness agent")
    s.add_argument("--llm", default=None)
    sub.add_parser("status", help="show the stored agent and whether its tools are current")
    args = parser.parse_args(argv)
    if args.cmd == "setup":
        try:
            state = setup(TOOLS, llm=args.llm)
        except ValueError as exc:
            print(f"setup failed: {exc}")
            return 1
        print(json.dumps(state, indent=2))
        return 0
    state = load_state()
    print(json.dumps({**state, "agent_id_from_env": bool(os.environ.get(AGENT_ENV)),
                      "tools_current": state.get("tools_fingerprint") == fingerprint(TOOLS)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
