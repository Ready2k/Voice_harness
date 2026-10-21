"""Offline combined call playback; preserve source evidence and state timing quality."""
from __future__ import annotations

import hashlib
import html
import json
import wave
from bisect import bisect_right
from pathlib import Path

import numpy as np

RATE = 8000
MAX_FRAMES = RATE * 600
SOURCE_FILES = ("remote.wav", "sent.wav", "events.jsonl")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_audio(path):
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, RATE):
            raise ValueError("REVIEW_REQUIRES_MONO_PCM16_8000")
        if audio.getnframes() > MAX_FRAMES:
            raise ValueError("REVIEW_AUDIO_LIMIT")
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").copy()


def write_audio(path, samples):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1 if samples.ndim == 1 else samples.shape[1])
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(samples.astype("<i2").tobytes())


def _frame_offset(ns, origin):
    return max(0, round((ns - origin) * RATE / 1_000_000_000))


def _source_hashes(root):
    return {name: sha(root / name) for name in SOURCE_FILES}


def _validate_segments(alignment, sources):
    if alignment.get("sample_rate") != RATE or alignment.get("mode") not in {"capture_clock", "estimated_legacy"}:
        raise ValueError("INVALID_ALIGNMENT_FORMAT")
    for role in ("remote", "sent"):
        cursor = 0
        for segment in sorted((s for s in alignment["segments"] if s["track"] == role), key=lambda s: s["source_frame"]):
            if (any(type(segment[k]) is not int for k in ("source_frame", "frames", "timeline_frame"))
                    or segment["source_frame"] != cursor or segment["frames"] <= 0 or segment["timeline_frame"] < 0
                    or segment["timeline_frame"] + segment["frames"] > MAX_FRAMES):
                raise ValueError("INVALID_OR_INCOMPLETE_ALIGNMENT")
            cursor += segment["frames"]
        if cursor != len(sources[role]):
            raise ValueError("ALIGNMENT_DOES_NOT_COVER_SOURCE")
    if any(s["track"] not in sources for s in alignment["segments"]):
        raise ValueError("UNKNOWN_ALIGNMENT_TRACK")


def make_alignment(root, sources, events, *, speech_cache=None):
    hashes = _source_hashes(root)
    bindings = [e for e in events if e["type"] == "stream.bound"]
    if not bindings:
        raise ValueError("NO_STREAM_TIMING_AVAILABLE")
    origin = bindings[0]["monotonic_ns"]
    timing = root / "audio-timing.json"
    if timing.exists():
        capture = json.loads(timing.read_text())
        if capture.get("schema_version") != 1 or capture.get("clock") != "local_monotonic_ns":
            raise ValueError("INVALID_CAPTURE_TIMING")
        hashes["audio-timing.json"] = sha(timing)
        rows = capture["chunks"]
        origin = min([origin, *[r["at_ns"] for r in rows]])
        segments = [{"track": r["track"], "source_frame": r["source_frame"], "frames": r["frames"],
                     "timeline_frame": _frame_offset(r["at_ns"], origin), "generation": r["generation"]} for r in rows]
        # Twilio buffers outbound media for sequential playback. Local websocket
        # scheduling jitter is not silence inside a spoken reply. Anchor each
        # release's first chunk, then preserve its source PCM without packet gaps.
        releases = sorted(e["monotonic_ns"] for e in events if e["type"] == "speech.release")
        sent = []
        previous = None
        for row in (r for r in rows if r["track"] == "sent"):
            release = bisect_right(releases, row["at_ns"])
            same_reply = (previous is not None and row["generation"] == previous["generation"]
                          and row["source_frame"] == previous["source_frame"] + previous["frames"]
                          and release == bisect_right(releases, previous["at_ns"]))
            # Older timing fixtures without release events use only nearby packets.
            if not releases and previous is not None:
                same_reply = same_reply and row["at_ns"] - previous["at_ns"] <= 100_000_000
            if same_reply:
                sent[-1]["frames"] += row["frames"]
            else:
                sent.append({"track": "sent", "source_frame": row["source_frame"], "frames": row["frames"],
                             "timeline_frame": _frame_offset(row["at_ns"], origin), "generation": row["generation"]})
            previous = row
        segments = [s for s in segments if s["track"] != "sent"] + sent
        result = {"schema_version": 1, "mode": "capture_clock", "sample_rate": RATE, "origin_ns": origin,
                  "source_sha256": hashes, "segments": segments,
                  "delegate_timing": "first_send_anchor_per_release; continuous_source_pcm_within_reply",
                  "limitations": ["Aligned to the local transport clock; remote acoustic/playback latency is unknown.",
                                  "Delegate replies preserve continuous source audio; local send-packet jitter is not rendered as silence.",
                                  "Missing audio during stream reconnection is silence, not recovered speech."]}
    else:
        existing = root / "alignment.json"
        if existing.exists():
            result = json.loads(existing.read_text())
            if result.get("source_sha256") != hashes or result.get("mode") != "estimated_legacy":
                raise ValueError("EXISTING_ALIGNMENT_SOURCE_MISMATCH")
            _validate_segments(result, sources)
            return result
        segments = []
        cursor = 0
        for index, binding in enumerate(bindings):
            if index == len(bindings) - 1:
                count = len(sources["remote"]) - cursor
            else:
                stop = next((e["monotonic_ns"] for e in events if e["type"] == "stream.gap_started"
                             and binding["monotonic_ns"] < e["monotonic_ns"] < bindings[index + 1]["monotonic_ns"]), None)
                if stop is None:
                    raise ValueError("LEGACY_STREAM_BOUNDARY_UNKNOWN")
                # Legacy stream audio arrived as 20ms frames, but exact frame
                # boundaries were not saved. This remains explicitly estimated.
                count = min(len(sources["remote"]) - cursor,
                            max(0, round((stop - binding["monotonic_ns"]) / 20_000_000) * 160))
            if count:
                segments.append({"track": "remote", "source_frame": cursor, "frames": count,
                                 "timeline_frame": _frame_offset(binding["monotonic_ns"], origin),
                                 "generation": binding["content"]["generation"]})
            cursor += count
        releases = [e for e in events if e["type"] == "speech.release"]
        counts = None
        if speech_cache and Path(speech_cache).exists() and releases:
            cache = {sha(p): p.stat().st_size // 6 for p in Path(speech_cache).glob("*.pcm")
                     if not p.is_symlink() and p.stat().st_size <= 960000}
            values = [cache.get(e["content"]["audio_sha256"]) for e in releases]
            if all(v is not None for v in values) and sum(values) == len(sources["sent"]):
                counts = values
        length_method = "approved_pcm_lengths_verified_against_sent_total" if counts is not None else "estimated_from_playback_marks"
        if releases and counts is None:
            weights = []
            for index, release in enumerate(releases):
                name = "phrase_" + str(index + 1)
                mark = next((e for e in events if e["type"] == "playback.mark" and e["content"].get("name") == name
                             and not e["content"].get("cleared")), None)
                if mark is None:
                    raise ValueError("LEGACY_DELEGATE_BOUNDARY_UNKNOWN")
                weights.append(max(1, mark["monotonic_ns"] - release["monotonic_ns"]))
            counts = [round(len(sources["sent"]) * w / sum(weights)) for w in weights]
            counts[-1] += len(sources["sent"]) - sum(counts)
        if len(sources["sent"]) and not releases:
            raise ValueError("NO_DELEGATE_TIMING_AVAILABLE")
        cursor = 0
        for release, count in zip(releases, counts or []):
            if count:
                segments.append({"track": "sent", "source_frame": cursor, "frames": count,
                                 "timeline_frame": _frame_offset(release["monotonic_ns"], origin),
                                 "generation": release["content"]["generation"]})
            cursor += count
        result = {"schema_version": 1, "mode": "estimated_legacy", "sample_rate": RATE,
                  "origin_ns": origin, "source_sha256": hashes, "segments": segments,
                  "delegate_lengths": length_method,
                  "limitations": ["Older recording: alignment reconstructed from stream and speech-release events, not per-frame timing.",
                                  "Small offsets may remain; event arrival is not acoustic alignment.",
                                  "Missing audio during keypad reconnection cannot be recovered."]}
    _validate_segments(result, sources)
    return result


def mix_tracks(sources, alignment):
    _validate_segments(alignment, sources)
    length = max((s["timeline_frame"] + s["frames"] for s in alignment["segments"]), default=0)
    channels = np.zeros((length, 2), dtype=np.int32)
    adjustments = 0
    for role, channel in (("remote", 0), ("sent", 1)):
        end = 0
        for segment in sorted((s for s in alignment["segments"] if s["track"] == role), key=lambda s: s["source_frame"]):
            at = max(end, segment["timeline_frame"])
            adjustments += at - segment["timeline_frame"]
            if at + segment["frames"] > len(channels):
                if at + segment["frames"] > MAX_FRAMES:
                    raise ValueError("ALIGNED_RECORDING_LIMIT")
                channels = np.pad(channels, ((0, at + segment["frames"] - len(channels)), (0, 0)))
            source = segment["source_frame"]
            channels[at:at + segment["frames"], channel] = sources[role][source:source + segment["frames"]]
            end = at + segment["frames"]
    # Equal half gain prevents clipping even when both sides overlap.
    mono = np.rint(channels.sum(axis=1) / 2).astype("<i2")
    return mono, channels.astype("<i2"), adjustments


def _peaks(samples, count=1000):
    if not len(samples):
        return []
    edges = np.linspace(0, len(samples), min(count, len(samples)) + 1, dtype=int)
    return [round(float(np.max(np.abs(samples[a:b].astype(np.int32)))) / 32768, 4)
            for a, b in zip(edges[:-1], edges[1:])]


def render_review(root, *, speech_cache=None):
    """Write derived playback files without modifying original audio/manifest/events."""
    from .telephony_demo import verify_output

    root = Path(root)
    if not verify_output(root):
        raise ValueError("SOURCE_EVIDENCE_VERIFICATION_FAILED")
    sources = {"remote": read_audio(root / "remote.wav"), "sent": read_audio(root / "sent.wav")}
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    alignment = make_alignment(root, sources, events, speech_cache=speech_cache)
    mono, stereo, adjustments = mix_tracks(sources, alignment)
    alignment["overlap_adjustment_frames"] = adjustments
    alignment["duration_seconds"] = len(mono) / RATE
    (root / "alignment.json").write_text(json.dumps(alignment, indent=2))
    write_audio(root / "conversation.wav", mono)
    write_audio(root / "conversation-stereo.wav", stereo)
    rows = []
    for event in events:
        if event["type"] not in {"speech.remote_transcript", "speech.release"}:
            continue
        seconds = max(0, (event["monotonic_ns"] - alignment["origin_ns"]) / 1e9)
        delegate = event["type"] == "speech.release"
        text = event["content"]["text"]
        rows.append(f'<tr data-at="{seconds:.3f}"><td><button class="seek" data-seek="{seconds:.3f}">{int(seconds)//60}:{int(seconds)%60:02d}</button></td>'
                    f'<td><span class="speaker {"delegate" if delegate else "remote"}">{"Delegate" if delegate else "Service"}</span></td>'
                    f'<td>{html.escape(text)}</td></tr>')
    independent = (root / "remote-transcript.txt").read_text() if (root / "remote-transcript.txt").exists() else "Independent transcript unavailable."
    summary = json.loads((root / "summary.json").read_text())
    title = "Lost-wallet demo" if summary.get("scenario") == "lost-wallet" else "Telephone demo"
    date = events[0].get("occurred_at", "")[:10] if events else ""
    mode = "Estimated alignment" if alignment["mode"] == "estimated_legacy" else "Capture-clock alignment"
    data = {"duration": len(mono) / RATE, "remote": _peaks(stereo[:, 0]), "sent": _peaks(stereo[:, 1])}
    template = (Path(__file__).parent / "templates" / "call_review.html").read_text()
    for key, value in {
        "MODE": mode, "DURATION": f"{len(mono)/RATE:.2f}", "ROWS": "".join(rows),
        "TITLE": html.escape(title), "DATE": html.escape(date),
        "INDEPENDENT": html.escape(independent),
        "NOTES": " ".join(html.escape(x) for x in alignment["limitations"]),
        "SHORT_NOTES": ("Timing for this older call is estimated. Small offsets may remain; missing audio during keypad reconnections is shown as silence."
                        if alignment["mode"] == "estimated_legacy" else
                        "Both voices share the recorded transport clock. Network and remote playback delays can still affect the perceived timing."),
        "DATA": json.dumps(data, separators=(",", ":")).replace("<", "\\u003c"),
    }.items():
        template = template.replace("@@" + key + "@@", value)
    (root / "index.html").write_text(template)
    derived = {"schema_version": 1, "authentication": "unsigned_derived_review",
               "source_manifest_sha256": sha(root / "manifest.json"), "source_sha256": alignment["source_sha256"],
               "alignment_mode": alignment["mode"], "mix": "mono_equal_half_gain; stereo_left_service_right_delegate",
               "sha256": {name: sha(root / name) for name in (
                   "alignment.json", "conversation.wav", "conversation-stereo.wav", "index.html")}}
    (root / "playback-manifest.json").write_text(json.dumps(derived, indent=2))
    return derived


def verify_review(root):
    try:
        from .telephony_demo import verify_output
        root = Path(root)
        if not verify_output(root):
            return False
        manifest = json.loads((root / "playback-manifest.json").read_text())
        expected = {"alignment.json", "conversation.wav", "conversation-stereo.wav", "index.html"}
        if set(manifest["sha256"]) != expected or manifest["source_manifest_sha256"] != sha(root / "manifest.json"):
            return False
        if not set(manifest["source_sha256"]).issubset({*SOURCE_FILES, "audio-timing.json"}):
            return False
        return all(not (root / name).is_symlink() and sha(root / name) == value
                   for group in (manifest["source_sha256"], manifest["sha256"]) for name, value in group.items())
    except (ValueError, OSError, KeyError, TypeError):
        return False
