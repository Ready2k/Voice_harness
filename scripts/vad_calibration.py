"""Calibrate each provider's server-side turn detection for the latency benchmark: speed against false endpoints.

    PYTHONPATH=src .venv/bin/python scripts/vad_calibration.py --out .local/vad-calibration --trials 3

For each provider and silence setting it streams, at real time, a sentence with a deliberate mid-sentence pause (a caller who
hesitates) and a control without one. A false endpoint is the provider ending the turn, or starting to answer, before the
sentence is finished, or hearing the sentence as more than one user turn. The control gives the endpointing wait (end of speech to
the provider's end-of-speech signal) and caller-felt latency (end of speech to first audio) at that setting. Live and billed
(pennies); needs the provider keys. Nova and ElevenLabs are not covered: their turn detection is not set this way.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["HARNESS_NATIVE_VAD"] = "1"  # provider-endpointed scripted caller (see live_providers.configure_transport)
PAUSE_MS = 600
TRAIL_MS = 1500
FIRST = "I'd like to check my account balance,"
SECOND = "please."
END_SIGNALS = {"input_audio_buffer.speech_stopped"}
SETTINGS = {
    "openai-realtime-live": [300, 500, 700],
    "xai-voice-live": [300, 500, 700],
    "gemini-live": [300, 500, 700],
}


def detection(provider, silence_ms):
    """The provider's turn-detection config for one silence setting (vendor default threshold, clean synthetic audio)."""
    if provider == "gemini-live":
        return {"activity_detection": {"endOfSpeechSensitivity": "END_SENSITIVITY_HIGH", "silenceDurationMs": silence_ms,
                                       "prefixPaddingMs": 200}}
    td = {"type": "server_vad", "silence_duration_ms": silence_ms, "prefix_padding_ms": 300}
    if provider == "openai-realtime-live":
        td.update(create_response=True, interrupt_response=True)
    return {"turn_detection": td}


def trim(pcm, rate=24000, threshold=300):
    """Drop the synthesiser's own leading and trailing silence (10 ms windows below an RMS threshold), so the pause is exactly
    PAUSE_MS and the clip ends where the caller's voice ends."""
    import array

    samples, window = array.array("h"), rate // 100
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    voiced = [i for i in range(0, len(samples), window)
              if (sum(x * x for x in samples[i : i + window]) / max(len(samples[i : i + window]), 1)) ** 0.5 >= threshold]
    return samples[voiced[0] : voiced[-1] + window].tobytes() if voiced else pcm


def clip(pause):
    from voice_harness.adapters.openai_realtime import synth_caller_pcm
    from voice_harness.live_scenario import RATE

    first = bytes(RATE * 2 * 200 // 1000) + trim(synth_caller_pcm(FIRST, lead_ms=0, trail_ms=0))
    second = trim(synth_caller_pcm(SECOND, lead_ms=0, trail_ms=0))
    gap = bytes(RATE * 2 * PAUSE_MS // 1000) if pause else b""
    return first + gap + second, bytes(RATE * 2 * TRAIL_MS // 1000)


async def trial(provider, silence_ms, pause, index):
    from voice_harness.adapters import AudioOut, TranscriptFinal
    from voice_harness.live_providers import _adapter, configure_transport
    from voice_harness.live_scenario import CHUNK_MS, RATE
    from voice_harness.resample import chunk

    adapter, config = _adapter(provider, "standardised-v3")
    configure_transport(provider, config, "fixed")
    config.update(detection(provider, silence_ms))
    config["session_id"] = f"cal-{provider}-{silence_ms}-{index}"
    marks = {"signals": [], "audio": [], "users": set()}
    adapter.native_sink = lambda _a, kind, _e: marks["signals"].append((time.monotonic() * 1000, str(kind)))
    await adapter.open_session(config)
    speech, trail = clip(pause)

    async def listen():
        async for event in adapter.receive():
            if isinstance(event, AudioOut):
                marks["audio"].append(time.monotonic() * 1000)
            elif isinstance(event, TranscriptFinal) and event.role == "user":
                marks["users"].add(getattr(event, "item_id", None) or len(marks["users"]))  # one entry per user item, not per transcript revision

    reader = asyncio.ensure_future(listen())
    started = time.monotonic() * 1000
    speech_end = started + len(speech) / 2 / RATE * 1000
    sent = 0
    for part in chunk(speech + trail, RATE, CHUNK_MS):
        if (due := started + sent / 2 / RATE * 1000 - time.monotonic() * 1000) > 0:
            await asyncio.sleep(due / 1000)
        await adapter.send_audio(part, {"sample_count": len(part) // 2, "sample_rate": RATE})
        sent += len(part)
    await asyncio.sleep(4)  # let the reply start
    reader.cancel()
    await adapter.close("calibration")
    ends = sorted(t for t, kind in marks["signals"] if kind in END_SIGNALS)
    audio = sorted(marks["audio"])
    early_signal = any(t < speech_end for t in ends)
    early_audio = any(t < speech_end for t in audio)
    return {"provider": provider, "silence_ms": silence_ms, "pause": pause, "trial": index,
            "false_endpoint": bool(early_signal or early_audio or len(marks["users"]) > 1),
            "endpointing_ms": round(next((t for t in ends if t >= speech_end), None) - speech_end) if any(t >= speech_end for t in ends) else None,
            "first_audio_ms": round(next((t for t in audio if t >= speech_end), None) - speech_end) if any(t >= speech_end for t in audio) else None,
            "user_turns_heard": len(marks["users"]), "answered": bool(audio)}


def summarise(rows):
    out = []
    for provider in sorted({r["provider"] for r in rows}):
        for silence in SETTINGS[provider]:
            mine = [r for r in rows if r["provider"] == provider and r["silence_ms"] == silence]
            pause, control = [r for r in mine if r["pause"]], [r for r in mine if not r["pause"]]
            lat = [r["first_audio_ms"] for r in control if r["first_audio_ms"] is not None]
            end = [r["endpointing_ms"] for r in control if r["endpointing_ms"] is not None]
            out.append({"provider": provider, "silence_ms": silence,
                        "false_endpoints_with_pause": f'{sum(r["false_endpoint"] for r in pause)}/{len(pause)}',
                        "false_endpoints_control": f'{sum(r["false_endpoint"] for r in control)}/{len(control)}',
                        "median_endpointing_ms": round(statistics.median(end)) if end else None,
                        "median_first_audio_ms": round(statistics.median(lat)) if lat else None})
    return out


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3, help="pause trials per setting (control trials: one fewer, minimum 2)")
    parser.add_argument("--providers", default=",".join(SETTINGS))
    args = parser.parse_args()
    from voice_conversation import load_environment

    load_environment()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []

    async def one_provider(provider):
        for silence in SETTINGS[provider]:
            for pause, count in ((True, args.trials), (False, max(2, args.trials - 1))):
                for index in range(count):
                    try:
                        rows.append(await trial(provider, silence, pause, index))
                    except Exception as error:  # noqa: BLE001  # recorded, so one failed trial does not end the calibration
                        rows.append({"provider": provider, "silence_ms": silence, "pause": pause, "trial": index,
                                     "error": f"{type(error).__name__}: {str(error)[:120]}"})
                    print(json.dumps(rows[-1]), flush=True)

    await asyncio.gather(*(one_provider(p) for p in args.providers.split(",")))
    good = [r for r in rows if "error" not in r]
    (args.out / "trials.json").write_text(json.dumps(rows, indent=1))
    (args.out / "summary.json").write_text(json.dumps(summarise(good), indent=1))
    for row in summarise(good):
        print(row)


if __name__ == "__main__":
    asyncio.run(main())
