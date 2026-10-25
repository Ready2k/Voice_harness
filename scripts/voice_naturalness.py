"""Matched-script voice naturalness test: every provider reads the same banking lines, a person rates them blind and
an audio LLM rates the same clips; the report compares the two.

    python scripts/voice_naturalness.py render --out .local/naturalness-DATE     # clips, blind rating page, key
    python scripts/voice_naturalness.py judge  --out .local/naturalness-DATE     # audio-LLM scores (OPENAI_API_KEY)
    python scripts/voice_naturalness.py report --out .local/naturalness-DATE --ratings ratings.json

Providers speak through their live adapters with a read-aloud instruction, so the clip is the product's own voice
path. Each keeps its default voice, plus a second ElevenLabs voice. ``key.json`` maps blind ids to providers and is
not needed until ``report``; the rating page shows only blind ids.
"""

import argparse
import asyncio
import base64
import json
import os
import random
import secrets
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LINES = [
    "Thanks for calling. Before I can share any account details, I need to check who I'm speaking with.",
    "Your current account balance is one thousand two hundred and fifty pounds and forty pence.",
    "I'm so sorry for your loss. I can put you through to someone in our bereavement team who can help with this.",
    "I can't move that payment without your confirmation. Would you like me to go ahead, yes or no?",
    "That didn't go through, I'm afraid. Nothing has been taken from your account, and I can try again if you like.",
    "You've been verified, thank you. Your last three payments were forty-two pounds, nineteen pounds ninety-nine, and two hundred.",
]
NUMERIC_LINES = {1, 5}  # amounts are transcribed as digits, so word similarity is lower
PHONE_LINES = 2  # the first lines are also rendered through a narrowband telephone line
VARIANTS = [  # (label, provider_config_id, voice override)
    ("OpenAI Realtime", "openai-realtime-live", None),
    ("Amazon Nova Sonic", "nova-sonic-live", None),
    ("xAI Grok Voice", "xai-voice-live", None),
    ("Google Gemini Live", "gemini-live", None),
    ("ElevenLabs (George)", "elevenlabs-live", None),
    ("ElevenLabs (second voice)", "elevenlabs-live", "kLhAstPcnnPxqzk6gS5i"),
]
READ_ALOUD = ("You are a voice reading text aloud for a bank phone line. Say exactly the text the user sends, word for "
              "word, in a natural, warm, professional voice. Add nothing, leave nothing out, and do not comment.")
JUDGE_MODEL = "gpt-audio-1.5"
RUBRIC = ("You rate recorded speech for a bank phone assistant. Listen to the clip and score it 1-5 (5 best, half points "
          "allowed) on: naturalness (does it sound like a real person speaking, with human rhythm and intonation rather "
          "than synthetic), clarity (every word easy to understand) and warmth (appropriate, reassuring tone for the "
          "line). Judge only how it sounds, not the wording. Reply with JSON only: "
          '{"naturalness": n, "clarity": n, "warmth": n, "reason": "one short sentence"}')


def load_env(path=ROOT / ".env"):
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value.strip().strip("\"'"))


def write_wav(path, pcm, rate=24000):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(pcm)


def same_words(line, said):
    import difflib
    import re

    def words(text):
        return re.findall(r"[a-z0-9']+", re.sub(r"\[[^\]]*\]", " ", text.lower()))
    return difflib.SequenceMatcher(None, words(line), words(said)).ratio() >= 0.9


async def speak(provider, voice, text, timeout=60):
    """One fresh session: the provider's PCM (24 kHz) for ``text``. ElevenLabs' agent LLM sometimes answers the line
    instead of reading it, so its own transcript is checked and the line retried."""
    for attempt in range(4):
        pcm, said = await speak_once(provider, voice, text, timeout)
        if provider != "elevenlabs-live" or same_words(text, said):
            return pcm
    raise RuntimeError(f"NOT_VERBATIM: {said[:80]}")


async def speak_once(provider, voice, text, timeout=60):
    from voice_harness import live_providers as lp

    adapter, config = lp._adapter(provider)
    config["tools"] = []
    config["system_prompt" if provider == "nova-sonic-live" else "instructions"] = READ_ALOUD
    lp.configure_transport(provider, config, "fixed")
    if voice:
        config["voice"] = voice
    await adapter.open_session(config)
    pcm, said = bytearray(), []
    try:
        await adapter.send_text(f'Repeat this aloud, word for word, and nothing else: "{text}"'
                                if provider == "elevenlabs-live" else text)

        async def collect():
            async for event in adapter.receive():
                kind = getattr(event, "type", None)
                if kind == "audio_out":
                    if event.sample_rate != 24000:
                        raise ValueError(f"UNEXPECTED_RATE_{event.sample_rate}")
                    pcm.extend(event.pcm)
                elif kind == "transcript_final" and event.role == "assistant":
                    said.append(event.text)
                elif kind == "turn_end" and pcm:
                    return
                elif kind == "provider_error":
                    raise RuntimeError(getattr(event, "code", "PROVIDER_ERROR"))

        await asyncio.wait_for(collect(), timeout)
    finally:
        await adapter.close("done")
    return bytes(pcm), " ".join(said)


def render(out):
    from voice_harness.live_scenario import degrade

    load_env()
    out.mkdir(parents=True, exist_ok=True)
    (out / "clips").mkdir(exist_ok=True)
    rng = random.Random(secrets.randbits(32))  # noqa: S311  # nosec B311 -- display order, not security
    key, failures = {}, []

    async def run():
        for label, provider, voice in VARIANTS:
            for index, text in enumerate(LINES):
                try:
                    pcm = await speak(provider, voice, text)
                except Exception as exc:  # one provider failing must not lose the others
                    failures.append({"provider": label, "line": index, "error": f"{type(exc).__name__}: {exc}"[:200]})
                    print("FAILED", label, index, exc, flush=True)
                    continue
                conditions = [("clean", pcm)]
                if index < PHONE_LINES:
                    conditions.append(("phone", degrade(pcm, snr_db=30, narrowband=True)))
                for condition, audio in conditions:
                    clip = secrets.token_hex(4)
                    write_wav(out / "clips" / f"{clip}.wav", audio)
                    key[clip] = {"provider": label, "line": index, "condition": condition, "seconds": len(audio) / 48000}
                print("ok", label, index, flush=True)

    asyncio.run(run())
    (out / "key.json").write_text(json.dumps({"clips": key, "failures": failures, "lines": LINES}, indent=1))
    order = sorted(key, key=lambda _: rng.random())
    (out / "rate.html").write_text(rating_page(order, key))
    print(f"{len(key)} clips, {len(failures)} failures -> {out}/rate.html")


def rating_page(order, key):
    items = [{"id": clip, "text": LINES[key[clip]["line"]]} for clip in order]
    return PAGE.replace("__ITEMS__", json.dumps(items))


def judge(out, concurrency=4):
    import httpx

    load_env()
    token = os.environ.get("OPENAI_API_KEY")
    if not token:
        raise SystemExit("NO_OPENAI_API_KEY")
    key = json.loads((out / "key.json").read_text())["clips"]
    results = {}
    path = out / "judge.json"
    if path.exists():
        results = json.loads(path.read_text())

    async def one(client, clip, gate):
        async with gate:
            audio = base64.b64encode((out / "clips" / f"{clip}.wav").read_bytes()).decode()
            body = {"model": JUDGE_MODEL, "modalities": ["text"], "messages": [
                {"role": "system", "content": RUBRIC},
                {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}},
                                             {"type": "text", "text": f"The line being read: {LINES[key[clip]['line']]}"}]}]}
            response = await client.post("https://api.openai.com/v1/chat/completions", json=body,
                                         headers={"Authorization": f"Bearer {token}"})
            if response.status_code >= 400:
                print("judge error", clip, response.status_code, response.text[:200])
                return
            text = response.json()["choices"][0]["message"]["content"].strip().removeprefix("```json").removesuffix("```")
            try:
                results[clip] = json.loads(text)
            except ValueError:
                print("unparseable", clip, text[:100])

    async def run():
        gate = asyncio.Semaphore(concurrency)
        async with httpx.AsyncClient(timeout=120) as client:
            await asyncio.gather(*(one(client, c, gate) for c in key if c not in results))

    asyncio.run(run())
    path.write_text(json.dumps(results, indent=1))
    print(f"{len(results)}/{len(key)} clips judged by {JUDGE_MODEL}")


def verify(out):
    """Transcribe each clean clip and score it against the line it should read (word-level similarity, 0-1)."""
    import difflib
    import re

    import httpx

    load_env()
    key = json.loads((out / "key.json").read_text())["clips"]
    token = os.environ["OPENAI_API_KEY"]
    result = {}

    def words(text):
        return re.findall(r"[a-z0-9']+", text.lower())

    with httpx.Client(timeout=120) as client:
        for clip, info in key.items():
            if info["condition"] != "clean":
                continue
            response = client.post("https://api.openai.com/v1/audio/transcriptions",
                                   headers={"Authorization": f"Bearer {token}"}, data={"model": "gpt-4o-transcribe"},
                                   files={"file": (f"{clip}.wav", (out / "clips" / f"{clip}.wav").read_bytes(), "audio/wav")})
            heard = response.json().get("text", "") if response.status_code < 400 else ""
            result[clip] = {"heard": heard, "match": round(difflib.SequenceMatcher(
                None, words(LINES[info["line"]]), words(heard)).ratio(), 3), "numeric": info["line"] in NUMERIC_LINES}
    (out / "verify.json").write_text(json.dumps(result, indent=1))
    for clip, row in sorted(result.items(), key=lambda kv: kv[1]["match"]):
        if row["match"] < (0.4 if row["numeric"] else 0.85):
            info = key[clip]
            print(f"{row['match']:.2f} {info['provider']} line {info['line']}: {row['heard'][:110]}")
    print(f"{sum(1 for r in result.values() if r['match'] >= (0.4 if r['numeric'] else 0.85))}/{len(result)} clips read the line as written")


def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else None


def fmt(value):
    return "  - " if value is None else f"{value:4.2f}"


def report(out, ratings_path):
    data = json.loads((out / "key.json").read_text())
    key = data["clips"]
    judged = json.loads((out / "judge.json").read_text()) if (out / "judge.json").exists() else {}
    human = json.loads(Path(ratings_path).read_text()) if ratings_path else {}
    human = human.get("ratings", human)
    lines = ["# Voice naturalness (matched script)", "",
             f"Same {len(LINES)} banking lines per provider, default voice unless named; human = blind ratings, "
             f"judge = {JUDGE_MODEL} on the same clips (1-5, higher is better). Judge and OpenAI share a vendor.", "",
             "| Provider | Condition | Clips | Human natural | Human clarity | Human warmth | Judge natural | Judge clarity | Judge warmth |",
             "|---|---|---|---|---|---|---|---|---|"]
    providers = [v[0] for v in VARIANTS]
    for provider in providers:
        for condition in ("clean", "phone"):
            clips = [c for c, k in key.items() if k["provider"] == provider and k["condition"] == condition]
            if not clips:
                continue
            row = [provider, condition, str(len(clips))]
            for source in (human, judged):
                for axis in ("naturalness", "clarity", "warmth"):
                    row.append(fmt(mean([source.get(c, {}).get(axis) for c in clips])))
            lines.append("| " + " | ".join(row) + " |")
    both = [c for c in key if c in human and c in judged]
    lines += ["", f"Clips rated by both a person and the judge: {len(both)}"]
    for axis in ("naturalness", "clarity", "warmth"):
        rho = spearman([human[c][axis] for c in both], [judged[c][axis] for c in both]) if both else None
        lines.append(f"- {axis}: Spearman agreement {'n/a' if rho is None else f'{rho:.2f}'}")
    if data["failures"]:
        lines += ["", "Clips that could not be rendered:"] + [f"- {f['provider']} line {f['line']}: {f['error']}" for f in data["failures"]]
    text = "\n".join(lines) + "\n"
    (out / "report.md").write_text(text)
    print(text)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blind voice rating</title>
<style>
:root{color-scheme:light dark;--bg:#fafafa;--fg:#1a1a1a;--card:#fff;--line:#d8d8d8;--accent:#0b5fff}
@media(prefers-color-scheme:dark){:root{--bg:#141414;--fg:#eee;--card:#1d1d1d;--line:#363636;--accent:#6aa0ff}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,sans-serif;max-width:760px;margin-inline:auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0}
.line{font-style:italic;margin:0 0 8px}audio{width:100%}
.row{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:8px;align-items:center}
select{font:inherit;padding:4px}button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
.top{position:sticky;top:0;background:var(--bg);padding:8px 0;display:flex;justify-content:space-between;gap:8px;align-items:center}
</style></head><body>
<h1>Blind voice rating</h1>
<p>Listen to each clip once or twice and score it 1 (poor) to 5 (excellent). Natural: sounds like a real person. Clear: every word easy to follow. Warm: the tone suits the line. Provider names are hidden.</p>
<div class="top"><span id="count"></span><button id="save">Download ratings</button></div>
<div id="list"></div>
<script>
const ITEMS = __ITEMS__;
const AXES = [["naturalness","Natural"],["clarity","Clear"],["warmth","Warm"]];
let ratings = {};
try { ratings = JSON.parse(localStorage.getItem("voice-ratings") || "{}"); } catch (e) {}
const store = () => { try { localStorage.setItem("voice-ratings", JSON.stringify(ratings)); } catch (e) {} };
const list = document.getElementById("list");
function count(){ const done = ITEMS.filter(i => AXES.every(([a]) => ratings[i.id] && ratings[i.id][a])).length;
  document.getElementById("count").textContent = done + " of " + ITEMS.length + " rated"; }
ITEMS.forEach((item, n) => {
  const card = document.createElement("div"); card.className = "card";
  const p = document.createElement("p"); p.className = "line"; p.textContent = (n + 1) + ". \\u201c" + item.text + "\\u201d";
  const audio = document.createElement("audio"); audio.controls = true; audio.preload = "none"; audio.src = "clips/" + item.id + ".wav";
  const row = document.createElement("div"); row.className = "row";
  AXES.forEach(([axis, label]) => {
    const wrap = document.createElement("label"); wrap.textContent = label + " ";
    const sel = document.createElement("select");
    ["", 1, 2, 3, 4, 5].forEach(v => { const o = document.createElement("option"); o.value = v; o.textContent = v || "-"; sel.appendChild(o); });
    sel.value = (ratings[item.id] && ratings[item.id][axis]) || "";
    sel.onchange = () => { ratings[item.id] = ratings[item.id] || {}; if (sel.value) ratings[item.id][axis] = Number(sel.value); else delete ratings[item.id][axis]; store(); count(); };
    wrap.appendChild(sel); row.appendChild(wrap);
  });
  card.append(p, audio, row); list.appendChild(card);
});
document.getElementById("save").onclick = () => {
  const blob = new Blob([JSON.stringify({ratings}, null, 1)], {type: "application/json"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "ratings.json"; a.click();
};
count();
</script></body></html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["render", "judge", "verify", "report"])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--ratings")
    args = parser.parse_args()
    {"render": lambda: render(args.out), "judge": lambda: judge(args.out), "verify": lambda: verify(args.out),
     "report": lambda: report(args.out, args.ratings)}[args.command]()


if __name__ == "__main__":
    main()
