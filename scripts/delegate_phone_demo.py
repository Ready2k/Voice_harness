#!/usr/bin/env python3
"""Run one authorised, bounded demo call; expose only signed Twilio ingress."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
import signal
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from voice_harness.telephony_demo import DelegateDemo, load_environment, verify_output
from voice_harness.asr import AsrUnavailable
from voice_harness.caller_voice import CallerVoiceUnavailable
from voice_harness.telephony_demo import TwilioError


async def run(args):
    env = load_environment(Path(args.env_file))
    for key in ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET", "OPENAI_API_KEY"]:
        if not env.get(key):
            raise ValueError("MISSING_CONFIG:" + key)
    root = Path(args.output or (".local/delegate-calls/" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")))
    demo = DelegateDemo(env, root, destination=args.to, caller_id=args.caller_id,
                        route_id=args.route_id, subtest_id=args.subtest_id, scenario=args.scenario,
                        wallet_card_included=None if args.wallet_card_included is None else args.wallet_card_included == "yes",
                        wallet_card_description=args.wallet_card_description,
                        speech_cache=root.parent.parent / "delegate-speech-cache",
                        task=args.task or ("Say I've lost my wallet and follow the demo questions using only supplied facts."
                                           if args.scenario == "lost-wallet" else
                                           "Check the demo gift card balance and currency and report what the system says."),
                        max_seconds=args.max_seconds)
    server, serving, tunnel, tunnel_log = None, None, None, None
    loop = asyncio.get_running_loop()
    def stop():
        demo.reason = "OPERATOR_STOP"
        demo.done.set()
    loop.add_signal_handler(signal.SIGINT, stop)
    loop.add_signal_handler(signal.SIGTERM, stop)
    summary = None
    try:
        print("Preparing trusted speech, independent ASR and realtime session; no call yet.", flush=True)
        await demo.prepare()
        if demo.done.is_set():
            raise ValueError("STOPPED_DURING_PREFLIGHT")
        server = uvicorn.Server(uvicorn.Config(demo.app(), host="127.0.0.1", port=args.port,
                                               access_log=False, log_level="warning", ws_max_size=65536))
        # The runner owns signal handling and cleanup.
        server.capture_signals = contextlib.nullcontext
        serving = asyncio.create_task(server.serve())
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    raise ValueError("INGRESS_SERVER_FAILED")
                await asyncio.sleep(0.05)
        if args.public_url:
            demo.configure_public_url(args.public_url)
        else:
            if not args.tunnel_binary:
                raise ValueError("SET_PUBLIC_URL_OR_TUNNEL_BINARY")
            tunnel_log = (root / "tunnel.log").open("wb")
            tunnel = await asyncio.create_subprocess_exec(args.tunnel_binary, "tunnel", "--url",
                f"http://127.0.0.1:{args.port}", "--no-autoupdate", "--protocol", "http2",
                stdout=tunnel_log, stderr=tunnel_log)
            async with asyncio.timeout(40):
                while True:
                    if tunnel.returncode is not None:
                        raise ValueError("TUNNEL_FAILED")
                    log = (root / "tunnel.log").read_text()
                    matches = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", log)
                    if matches and "Registered tunnel connection" in log:
                        demo.configure_public_url(matches[0])
                        # Avoid caching NXDOMAIN before the new DNS record exists.
                        await asyncio.sleep(3)
                        break
                    await asyncio.sleep(0.5)
        # A newly allocated tunnel can briefly return NXDOMAIN. Verify both
        # managed and operator-supplied origins before placing a chargeable call.
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            for attempt in range(30):
                if demo.done.is_set():
                    raise ValueError("STOPPED_DURING_INGRESS_PREFLIGHT")
                try:
                    response = await client.get(demo.public_url + "/")
                    demo.record("ingress.probe", {"attempt": attempt + 1, "status": response.status_code})
                    if response.status_code == 404:
                        break
                except httpx.HTTPError as exc:
                    demo.record("ingress.probe", {"attempt": attempt + 1, "error_type": type(exc).__name__})
                await asyncio.sleep(1)
            else:
                raise ValueError("TUNNEL_NOT_REACHABLE")
        if demo.done.is_set():
            raise ValueError("STOPPED_BEFORE_DIAL")
        print("Dialling", demo.destination, "from", demo.caller_id, "limit", args.max_seconds, "seconds.", flush=True)
        await demo.dial()
        print("Call:", demo.call_sid, flush=True)
        await demo.done.wait()
    except Exception as exc:
        # Errors are stable codes; never dump provider bodies or credentials.
        demo.reason = demo.reason or "FAILED:" + (str(exc) if isinstance(exc, (ValueError, AsrUnavailable, CallerVoiceUnavailable, TwilioError)) else type(exc).__name__)
        print("Run stopped:", demo.reason, flush=True)
    finally:
        try:
            summary = await demo.finish()
        finally:
            await demo.adapter.close("runner_cleanup")
            await demo.control.close()
            if server:
                server.should_exit = True
            if serving:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(serving, 5)
            if tunnel and tunnel.returncode is None:
                tunnel.terminate()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(tunnel.wait(), 5)
            if tunnel_log:
                tunnel_log.close()
    print("Evidence:", root.resolve(), flush=True)
    print("Local evidence verification:", verify_output(root), flush=True)
    if summary and (summary["remote_audio_seconds"] or summary["sent_audio_seconds"]):
        from voice_harness.call_review import render_review, verify_review
        try:
            render_review(root)
            print("Combined playback:", root.resolve() / "index.html", "verified:", verify_review(root), flush=True)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            print("Combined playback unavailable:", type(exc).__name__, "; original evidence retained.", flush=True)
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary and summary["model_finding"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--to", required=True)
    parser.add_argument("--caller-id", required=True)
    parser.add_argument("--route-id", required=True)
    parser.add_argument("--subtest-id", help="Optional operator-supplied sub-test keypad value; never guessed")
    parser.add_argument("--scenario", choices=["balance", "lost-wallet"], default="balance")
    parser.add_argument("--wallet-card-included", choices=["yes", "no"], help="Operator-supplied demo fact about the Barclays card")
    parser.add_argument("--wallet-card-description", help="Operator-supplied demo card description; no card numbers or secrets")
    parser.add_argument("--task")
    parser.add_argument("--max-seconds", type=int, default=90)
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--public-url")
    parser.add_argument("--tunnel-binary")
    parser.add_argument("--output")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
