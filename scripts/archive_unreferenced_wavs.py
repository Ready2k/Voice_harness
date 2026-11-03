"""Losslessly archive WAVs not referenced by current HTML or its JSON dependencies.

Dry run by default; pass --apply to convert. Ignored runtime/build folders are excluded.
Dynamic WAV players conservatively protect adjacent audio folders. A manifest preserves
original hashes; every conversion is decoded and compared before removing its WAV.
"""

import argparse
import hashlib
import io
import json
import re
import subprocess
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "artifacts/wav-archive-manifest.json"


def files():
    output = subprocess.check_output(  # nosec B603 B607 - fixed local Git query
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT  # noqa: S607
    )
    return sorted({ROOT / name for name in output.decode().split("\0") if name})


def pcm(data):
    with wave.open(io.BytesIO(data)) as audio:
        return ((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                 audio.getnframes()), audio.readframes(audio.getnframes()))


def candidates(paths):
    wavs = [p for p in paths if p.suffix.lower() == ".wav" and p.is_file()]
    protected = set()
    dependencies = set()
    for page in paths:
        if page.suffix != ".html" or not page.is_file():
            continue
        text = page.read_text(errors="replace")
        for wav in wavs:
            if wav.name in text or str(wav.relative_to(ROOT)) in text:
                protected.add(wav)
            if (".wav" in text and page.parent in (wav.parent, wav.parent.parent)
                    and any(folder in text for folder in ("clips/", "caller/", "setup-attempts/"))):
                protected.add(wav)
        for ref in re.findall(r'''["']([^"'\s<>]+\.json)(?:[?#][^"']*)?["']''', text):
            for base in (page.parent, ROOT):
                target = (base / ref.lstrip("/")).resolve()
                if target.is_relative_to(ROOT) and target.is_file():
                    dependencies.add(target)
    # Follow JSON manifests used by pages, including paths built from IDs in JS.
    seen = set()
    while dependencies - seen:
        path = next(iter(dependencies - seen))
        seen.add(path)
        text = path.read_text(errors="replace")
        for wav in wavs:
            if wav.name in text or str(wav.relative_to(ROOT)) in text:
                protected.add(wav)
        for ref in re.findall(r'"([^"\s]+\.json)"', text):
            for base in (path.parent, ROOT):
                target = (base / ref.lstrip("/")).resolve()
                if target.is_relative_to(ROOT) and target.is_file():
                    dependencies.add(target)
    return [p for p in wavs if p not in protected], protected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    paths = files()
    selected, protected = candidates(paths)
    print(f"Protected {len(protected)} WAVs; eligible {len(selected)} WAVs "
          f"({sum(p.stat().st_size for p in selected) / 1048576:.2f} MiB)", flush=True)
    if not args.apply:
        for path in selected:
            print(path.relative_to(ROOT))
        return
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    converted = {}
    for wav in selected:
        target = wav.with_suffix(".flac")
        if target.exists():
            raise SystemExit(f"Refusing to overwrite {target}")
        original = wav.read_bytes()
        try:
            subprocess.run(["flac", "-s", "-8", "-o", str(target), str(wav)], check=True)  # noqa: S603, S607  # nosec B603 B607
            decoded = subprocess.check_output(["flac", "-s", "-d", "-c", str(target)])  # noqa: S603, S607  # nosec B603 B607
            if pcm(decoded) != pcm(original):
                raise ValueError(f"PCM round-trip mismatch: {wav}")
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        entry = {"flac": str(target.relative_to(ROOT)),
                 "wav_sha256": hashlib.sha256(original).hexdigest(),
                 "pcm_sha256": hashlib.sha256(pcm(original)[1]).hexdigest(),
                 "flac_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                 "wav_bytes": len(original), "flac_bytes": target.stat().st_size}
        manifest[str(wav.relative_to(ROOT))] = entry
        # Persist provenance before removing the original.
        MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        wav.unlink()
        converted[wav] = entry
    # Update literal JSON file references. Caller manifest keys are logical cache IDs;
    # the loader supports archived FLAC without changing those IDs or PCM hashes.
    for path in paths:
        if path.suffix != ".json" or not path.is_file() or path == MANIFEST:
            continue
        old = path.read_text()
        new = old
        for wav in converted:
            for ref in (str(wav), str(wav.relative_to(ROOT)), str(wav.relative_to(path.parent))
                        if wav.is_relative_to(path.parent) else None):
                if ref:
                    new = re.sub(re.escape(json.dumps(ref)) + r"(?!\s*:)",
                                 lambda match, ref=ref: json.dumps(ref[:-4] + ".flac"), new)
        if new != old:
            # File checksums must follow changed file formats; original hashes remain in manifest.
            for entry in converted.values():
                new = new.replace(entry["wav_sha256"], entry["flac_sha256"])
            path.write_text(new)
    before = sum(e["wav_bytes"] for e in converted.values())
    after = sum(e["flac_bytes"] for e in converted.values())
    print(f"Converted and verified {len(converted)} files: {before / 1048576:.2f} -> "
          f"{after / 1048576:.2f} MiB; saved {(before-after) / 1048576:.2f} MiB")


if __name__ == "__main__":
    main()
