"""Convert a recorded comparison's clips/*.wav to lossless FLAC and repoint its pages.

    python scripts/flac_clips.py artifacts/voice-conversation-v2

Each WAV is encoded with ``flac``, decoded back, and its PCM compared with the original before the WAV is removed.
``clips/flac-manifest.json`` records each clip's original WAV SHA-256 (matching ``audio_sha256`` in results.json), its
PCM SHA-256 and the FLAC SHA-256, so a decoded clip can be checked against the recording. The pages' ``clips/<id>.wav``
references become ``.flac``. Re-running ``voice_conversation.py refresh`` regenerates the pages with ``.wav`` links:
rerun this script afterwards. Idempotent; a folder with no WAVs is left as it is.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path


def pcm_sha256(path):
    with wave.open(str(path)) as w:
        return hashlib.sha256(w.getparams().__repr__().encode() + w.readframes(w.getnframes())).hexdigest()


def convert(folder):
    flac = shutil.which("flac")
    if not flac:
        raise SystemExit("flac not found (brew install flac)")
    clips = folder / "clips"
    manifest_path = clips / "flac-manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for wav in sorted(clips.glob("*.wav")):
        target = wav.with_suffix(".flac")
        subprocess.run([flac, "-s", "-8", "-f", "-o", str(target), str(wav)], check=True)  # noqa: S603  # nosec B603
        decoded = wav.with_suffix(".check.wav")
        subprocess.run([flac, "-s", "-d", "-f", "-o", str(decoded), str(target)], check=True)  # noqa: S603  # nosec B603
        if pcm_sha256(decoded) != pcm_sha256(wav):
            decoded.unlink()
            target.unlink()
            raise SystemExit(f"ROUNDTRIP_MISMATCH: {wav.name}")
        decoded.unlink()
        manifest[wav.stem] = {"wav_sha256": hashlib.sha256(wav.read_bytes()).hexdigest(), "pcm_sha256": pcm_sha256(wav),
                              "flac_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        wav.unlink()
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    changed = []
    for page in folder.glob("*.html"):
        text = page.read_text()
        # only the rating page's clip links move; other files keep their extension
        new = text.replace('"clips/"+item.id+".wav"', '"clips/"+item.id+".flac"')
        if new != text:
            page.write_text(new)
            changed.append(page.name)
    return len(manifest), changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", type=Path)
    count, pages = convert(parser.parse_args().folder)
    print(f"{count} clips in manifest; pages updated: {', '.join(pages) or 'none'}", file=sys.stderr)
