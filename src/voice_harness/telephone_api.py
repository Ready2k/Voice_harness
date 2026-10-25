"""Authenticated operator controls around the bounded telephone pilot subprocess."""
import asyncio
import json
import os
import shutil
import signal
import sys
import uuid
from pathlib import Path

import httpx
from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .telephone_providers import provider_options
from .telephone_setup import CallSetup, setup_templates
from .telephony_demo import load_environment, normalise_number


class TelephoneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: str
    caller_id: str
    route_id: str = Field(pattern=r"^[0-9]{5,6}$")
    subtest_id: str | None = Field(default=None, pattern=r"^[0-9]{1,12}$")
    scenario: str = Field(default="lost-wallet", pattern=r"^(balance|lost-wallet)$")
    response_mode: str = Field(default="ai", pattern=r"^(static|ai)$")
    provider_config_id: str = Field(default="openai-realtime-live", pattern=r"^(openai-realtime-live|gemini-live|nova-sonic-live|xai-voice-live|elevenlabs-live)$")
    routing_mode: str = Field(default="dtmf", pattern=r"^(dtmf|voice)$")
    max_seconds: int = Field(default=180, ge=20, le=180)
    turn_quiet_ms: int = Field(default=1500, ge=200, le=2000)
    vad_silence_ms: int = Field(default=900, ge=300, le=1500)
    parallel_speech_checks: bool = False
    call_setup: CallSetup | None = None


class TelephoneCalls:
    def __init__(self, root):
        self.root = Path(root) / "telephone-calls"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.repo = Path(__file__).resolve().parents[2]
        self.process = None
        self.active_id = None
        self.lock = asyncio.Lock()

    def configuration(self):
        env_path = self.repo / ".env"
        env = load_environment(env_path) if env_path.exists() else dict(os.environ)
        missing = [k for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET", "OPENAI_API_KEY") if not env.get(k)]
        tunnel = env.get("VOICE_HARNESS_TUNNEL_BINARY") or shutil.which("cloudflared")
        public = env.get("VOICE_HARNESS_TELEPHONE_PUBLIC_URL")
        if not public and not (tunnel and Path(tunnel).is_file()):
            missing.append("VOICE_HARNESS_TUNNEL_BINARY or VOICE_HARNESS_TELEPHONE_PUBLIC_URL")
        if not (self.repo / "scripts/delegate_phone_demo.py").is_file():
            missing.append("host telephone runner")
        return env, tunnel, public, missing

    async def twilio_numbers(self):
        env, *_ = self.configuration()
        if not all(env.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET")):
            raise HTTPException(409, "TWILIO_CREDENTIALS_REQUIRED")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{env['TWILIO_ACCOUNT_SID']}/IncomingPhoneNumbers.json"
        numbers = []
        async with httpx.AsyncClient(auth=(env["TWILIO_API_KEY_SID"], env["TWILIO_API_KEY_SECRET"]), timeout=15) as client:
            try:
                while url:
                    response = await client.get(url)
                    if response.status_code in (401, 403) and env.get("TWILIO_AUTH_TOKEN"):
                        # The pilot's restricted Calls key may not include number-list permission.
                        response = await client.get(url, auth=(env["TWILIO_ACCOUNT_SID"], env["TWILIO_AUTH_TOKEN"]))
                    if response.status_code >= 400:
                        raise HTTPException(502, "TWILIO_NUMBER_LOOKUP_DENIED" if response.status_code in (401, 403) else "TWILIO_NUMBER_LOOKUP_FAILED")
                    data = response.json()
                    numbers.extend({"phone_number": n["phone_number"], "friendly_name": n.get("friendly_name", n["phone_number"])}
                                   for n in data["incoming_phone_numbers"] if n.get("capabilities", {}).get("voice") is True)
                    next_uri = data.get("next_page_uri")
                    if next_uri and not next_uri.startswith(f"/2010-04-01/Accounts/{env['TWILIO_ACCOUNT_SID']}/IncomingPhoneNumbers.json?"):
                        raise ValueError("INVALID_PAGINATION")
                    url = "https://api.twilio.com" + next_uri if next_uri else None
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise HTTPException(502, "TWILIO_NUMBER_LOOKUP_FAILED") from None
        return {"items": numbers, "caller_id": numbers[0]["phone_number"] if numbers else None,
                "scenario_defaults": {"lost-wallet": {"destination": "+441617683827", "route_id": "770222", "subtest_id": "01"},
                                      "balance": {"destination": "+441617683858", "route_id": "770022", "subtest_id": ""}}}

    def directory(self, call_id):
        if not len(call_id) == 32 or any(c not in "0123456789abcdef" for c in call_id):
            raise HTTPException(404, "CALL_NOT_FOUND")
        path = self.root / call_id
        if not path.is_dir():
            raise HTTPException(404, "CALL_NOT_FOUND")
        return path

    def view(self, call_id):
        path = self.directory(call_id)
        setup = json.loads((path / "setup.json").read_text()) if (path / "setup.json").exists() else None
        if (path / "evidence").is_dir():
            path = path / "evidence"
        active = self.active_id == call_id and self.process and self.process.returncode is None
        summary = json.loads((path / "summary.json").read_text()) if (path / "summary.json").exists() else None
        return {"call_id": call_id, "state": "running" if active else "finished" if summary else "interrupted",
                "summary": summary, "setup": setup, "artifacts": [n for n in ("remote.wav", "sent.wav", "conversation.wav", "remote-transcript.txt", "events.jsonl", "manifest.json", "summary.json") if (path / n).is_file()]}

    async def start(self, request):
        async with self.lock:
            if self.process and self.process.returncode is None:
                raise HTTPException(409, "TELEPHONE_CALL_ALREADY_ACTIVE")
            env, tunnel, public, missing = self.configuration()
            selected = next(p for p in provider_options(env) if p["provider_config_id"] == request.provider_config_id)
            if selected["missing"]:
                raise HTTPException(409, "TELEPHONE_PROVIDER_UNAVAILABLE:" + ",".join(selected["missing"]))
            if missing:
                raise HTTPException(409, "TELEPHONE_CONFIGURATION_REQUIRED")
            destination, caller = normalise_number(request.destination), normalise_number(request.caller_id)
            call_id = uuid.uuid4().hex
            path = self.root / call_id
            setup = request.call_setup or CallSetup.model_validate(setup_templates()[request.scenario])
            if not setup.goal:
                raise HTTPException(422, "CALL_GOAL_REQUIRED")
            path.mkdir(mode=0o700)
            public_setup = request.model_dump(exclude={"call_setup"}) | {"call_setup": setup.public()}
            (path / "setup.json").write_text(json.dumps(public_setup))
            (path / "setup.json").chmod(0o600)
            command = [sys.executable, str(self.repo / "scripts/delegate_phone_demo.py"), "--to", destination,
                       "--caller-id", caller, "--output", str((path / "evidence").resolve()), "--env-file", str(self.repo / ".env") if (self.repo / ".env").exists() else os.devnull]
            for key in ("route_id", "scenario", "routing_mode", "max_seconds", "turn_quiet_ms", "vad_silence_ms", "provider_config_id"):
                command += ["--" + key.replace("_", "-"), str(getattr(request, key))]
            if request.subtest_id:
                command += ["--subtest-id", request.subtest_id]
            command += ["--call-setup-stdin", "--response-mode", request.response_mode]
            if request.parallel_speech_checks:
                command += ["--parallel-speech-checks"]
            command += ["--public-url", public] if public else ["--tunnel-binary", tunnel]
            child_env = {**os.environ, "PYTHONPATH": str(self.repo / "src")}
            with (path / "runner.log").open("wb") as log:
                self.process = await asyncio.create_subprocess_exec(*command, cwd=self.repo, env=child_env, stdin=asyncio.subprocess.PIPE, stdout=log, stderr=log)
            self.active_id = call_id
            self.process.stdin.write(setup.model_dump_json().encode())
            await self.process.stdin.drain()
            self.process.stdin.close()
            return self.view(call_id)

    async def stop(self):
        if self.process and self.process.returncode is None:
            self.process.send_signal(signal.SIGTERM)

    async def close(self):
        await self.stop()
        if self.process:
            try:
                await asyncio.wait_for(self.process.wait(), 45)
            except TimeoutError:
                # Preserve the runner so its own cleanup can finish; Twilio has a hard call limit.
                pass


def register_telephone_routes(app, root, auth):
    calls = TelephoneCalls(root)
    app.state.telephone_calls = calls

    @app.get("/api/v1/telephone", dependencies=[Depends(auth)])
    def catalogue():
        env, _, _, missing = calls.configuration()
        return {"ready": not missing, "missing": missing, "providers": provider_options(env), "setup_templates": setup_templates(),
                "items": [calls.view(p.name) for p in sorted(calls.root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if p.is_dir()]}

    @app.get("/api/v1/telephone/configuration", dependencies=[Depends(auth)])
    async def configuration():
        return await calls.twilio_numbers()

    @app.post("/api/v1/telephone", dependencies=[Depends(auth)])
    async def start(request: TelephoneRequest):
        return await calls.start(request)

    @app.get("/api/v1/telephone/{call_id}", dependencies=[Depends(auth)])
    def status(call_id: str):
        return calls.view(call_id)

    @app.post("/api/v1/telephone/{call_id}/stop", dependencies=[Depends(auth)])
    async def stop(call_id: str):
        calls.directory(call_id)
        if call_id == calls.active_id:
            await calls.stop()
        return calls.view(call_id)

    @app.get("/api/v1/telephone/{call_id}/artifacts/{name}", dependencies=[Depends(auth)])
    def artifact(call_id: str, name: str):
        view = calls.view(call_id)
        if name not in view["artifacts"]:
            raise HTTPException(404, "ARTIFACT_NOT_FOUND")
        path = calls.directory(call_id)
        if (path / "evidence").is_dir():
            path = path / "evidence"
        return FileResponse(path / name, headers={"Cache-Control": "no-store"})
