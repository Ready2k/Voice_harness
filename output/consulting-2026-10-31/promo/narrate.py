"""Generate per-scene ElevenLabs narration (Lily) and build the narrated promo.
Audio is cached in narration/ so rebuilding the video never re-spends credits.
Key is read from ELEVENLABS_API_KEY (the repo's .env); it is never printed."""
from pathlib import Path
import os, json, subprocess, concurrent.futures, httpx

root = Path(__file__).resolve().parent
VOICE = "pFZP5JQG7iQjIQuC4Bku"  # Lily, velvety British
SR, FPS, LEAD, TAIL, MIN_LEN = 48000, 30, 0.6, 1.0, 6.0
script = json.loads((root / "narration.json").read_text())
(root / "narration").mkdir(exist_ok=True)
(root / "clips-narrated").mkdir(exist_ok=True)

def tts(i):
    out = root / "narration" / f"{i:02}.mp3"
    if out.exists():
        return
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}?output_format=mp3_44100_128",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=120,
        json={"text": script[i], "model_id": "eleven_multilingual_v2",
              "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.2}})
    r.raise_for_status()
    out.write_bytes(r.content)

def duration(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
        capture_output=True, text=True, check=True).stdout)

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    list(pool.map(tts, range(len(script))))

lengths = [max(MIN_LEN, LEAD + duration(root / "narration" / f"{i:02}.mp3") + TAIL) for i in range(len(script))]

def clip(i):
    """Slow push-in rendered with sub-pixel cubic warps. ffmpeg's zoompan crops on whole pixels, which
    repeats frames at this slow speed and reads as judder."""
    import cv2
    n = round(lengths[i] * FPS)
    out = root / "clips-narrated" / f"{i:02}.mp4"
    src = cv2.imread(str(root / "frames" / f"{i:02}.png"))
    h, w = src.shape[:2]
    vf = f"fade=t=in:st=0:d=0.25,fade=t=out:st={lengths[i]-0.25:.3f}:d=0.25,format=yuv420p"
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(FPS),
        "-i", "-", "-vf", vf, "-c:v", "libx264", "-preset", "slow", "-crf", "28", "-threads", "2", str(out)], stdin=subprocess.PIPE)
    for k in range(n):
        z = 1 + 0.03 * k / n
        m = [[z, 0, w / 2 * (1 - z)], [0, z, h / 2 * (1 - z)]]
        proc.stdin.write(cv2.warpAffine(src, __import__("numpy").float32(m), (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE).tobytes())
    proc.stdin.close()
    if proc.wait():
        raise RuntimeError("ffmpeg failed for scene %d" % i)

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    list(pool.map(clip, range(len(script))))

# One continuous voice track: each scene's narration placed LEAD seconds after the scene starts.
total = sum(lengths)
inputs, filters, start = [], [], 0.0
for i in range(len(script)):
    inputs += ["-i", str(root / "narration" / f"{i:02}.mp3")]
    ms = int((start + LEAD) * 1000)
    filters.append(f"[{i}:a]aresample={SR},adelay={ms}|{ms}[v{i}]")
    start += lengths[i]
mix = "".join(f"[v{i}]" for i in range(len(script))) + f"amix=inputs={len(script)}:normalize=0:duration=longest,apad=whole_dur={total:.3f},atrim=0:{total:.3f}[voice]"
subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(filters + [mix]), "-map", "[voice]", "-ar", str(SR), str(root / "narration" / "voice.wav")], check=True)

# Quiet music bed (the product-spot synth pulse, looped, low-passed) under the voice, with fades.
pulse = root.parents[1] / "product-spots" / ".build" / "pulse.wav"
listing = root / "clips-narrated.txt"
listing.write_text("\n".join(f"file '{root / 'clips-narrated' / f'{i:02}.mp4'}'" for i in range(len(script))))
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
    "-i", str(root / "narration" / "voice.wav"), "-stream_loop", "-1", "-i", str(pulse),
    "-filter_complex", f"[2:a]lowpass=f=900,volume=0.16,atrim=0:{total:.3f},afade=t=in:d=2,afade=t=out:st={total-3:.3f}:d=3[bed];"
                       f"[1:a][bed]amix=inputs=2:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]",
    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
    "-movflags", "+faststart", str(root / "voice-harness-admin-promo-narrated.mp4")], check=True)
print(f"Built narrated promo: {total:.1f}s ({total/60:.1f} min), scene lengths {[round(x,1) for x in lengths]}")
