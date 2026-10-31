"""Build the latency benchmark report from the four retained latency sessions (no live calls).

    PYTHONPATH=src .venv/bin/python scripts/build_latency_report.py

Reads the session payloads under ``.local/latency-session-*-2026-10-31`` (local, not tracked), writes the per-turn data to
``artifacts/latency-benchmark-2026-10-31/results.json`` and a self-contained page to ``reports/latency-benchmark-2026-10-31.html``.
Caller-felt latency L is the end of the caller's voice to the first reply audio; E and R split it where the provider
reports its own end-of-speech signal. Bootstrap intervals use a fixed seed.
"""
import html
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = {"1": "latency-session-1-2026-10-31", "1b": "latency-session-1b-plain-2026-10-31",
            "2": "latency-session-2-2026-10-31", "3": "latency-session-3-2026-10-31"}
# Session 4 (31 October, after the ledger began recording Gemini's voiceActivity type) is used only to fill the end-of-speech split
# for a provider the four pooled sessions could not split. It is not pooled into the totals above.
SUPPLEMENT = {"4": "latency-session-4-gemini-split-2026-10-31"}
SPLIT_ONLY = {"gemini-live"}
NAMES = {"gemini-live": "Google Gemini Live", "elevenlabs-live": "ElevenLabs Agents", "nova-sonic-live": "Amazon Nova Sonic",
         "openai-live": "OpenAI GPT-Live 1", "openai-realtime-live": "OpenAI Realtime", "xai-voice-live": "xAI Grok Voice"}
SETTING = {"openai-realtime-live": "server VAD, 700 ms silence", "xai-voice-live": "server VAD, 700 ms silence",
           "gemini-live": "automatic activity detection, 600 ms silence", "nova-sonic-live": "provider end-of-speech detection (default)",
           "elevenlabs-live": "provider turn detection (fixed)", "openai-live": "provider turn detection (fixed)"}


def pct(values, p):
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    low, high = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (k - low)


def interval(values, rng, rounds=2000):
    boots = sorted(pct([rng.choice(values) for _ in values], 0.5) for _ in range(rounds))
    return round(boots[int(0.025 * rounds)]), round(boots[int(0.975 * rounds)])


def collect(sessions=None, only=None):
    turns, cells = [], {}
    for session, folder in (sessions or SESSIONS).items():
        payload = json.loads((ROOT / ".local" / folder / "out" / "payload.json").read_text())
        for cell in payload["cells"]:
            observation = cell.get("observation")
            if not observation:
                continue
            if only and cell["provider_config_id"] not in only:
                continue
            cells[cell["provider_config_id"]] = cells.get(cell["provider_config_id"], 0) + 1
            metrics = {t["turn"]: t for t in (observation.get("metrics") or {}).get("turns", [])}
            for turn in observation["turns"]:
                if turn.get("first_audio_ms") is None or turn.get("overlapped"):
                    continue
                turns.append(dict(session=session, provider=cell["provider_config_id"], scenario=cell["scenario_id"],
                                  repetition=cell["repetition"], turn=turn["turn"],
                                  kind="tool" if (metrics.get(turn["turn"]) or {}).get("tool_results") else "plain",
                                  first_audio_ms=turn["first_audio_ms"], endpointing_ms=turn.get("endpointing_ms"),
                                  response_ms=turn.get("response_ms")))
    return turns, cells


def summarise(turns, split_turns=()):
    rng = random.Random(7)
    rows = []
    for kind in ("plain", "tool"):
        for provider in NAMES:
            mine = [t for t in turns if t["provider"] == provider and t["kind"] == kind]
            if not mine:
                continue
            values = [t["first_audio_ms"] for t in mine]
            ends = [t["endpointing_ms"] for t in mine if t["endpointing_ms"] is not None]
            resp = [t["response_ms"] for t in mine if t["response_ms"] is not None]
            lo, hi = interval(values, rng)
            from_extra = 0
            if not ends:
                extra = [t for t in split_turns if t["provider"] == provider and t["kind"] == kind]
                ends = [t["endpointing_ms"] for t in extra if t["endpointing_ms"] is not None]
                resp = [t["response_ms"] for t in extra if t["response_ms"] is not None]
                from_extra = len(ends)
            rows.append(dict(kind=kind, provider=provider, split_n=from_extra, n=len(values), median=round(pct(values, .5)), lo=lo, hi=hi,
                             p90=round(pct(values, .9)), p95=round(pct(values, .95)),
                             endpointing=round(pct(ends, .5)) if ends else None, response=round(pct(resp, .5)) if resp else None))
    return sorted(rows, key=lambda r: (r["kind"], r["median"]))


def note(r):
    return f'<small>† session 4 only, {r["split_n"]} turns</small>' if r.get("split_n") else ""


def table(rows, kind):
    body = "".join(
        f'<tr><td>{html.escape(NAMES[r["provider"]])}<small>{html.escape(SETTING[r["provider"]])}</small></td><td>{r["n"]}</td>'
        f'<td><b>{r["median"] / 1000:.2f} s</b><small>{r["lo"] / 1000:.2f} – {r["hi"] / 1000:.2f}</small></td>'
        f'<td>{r["p90"] / 1000:.2f} s</td><td>{r["p95"] / 1000:.2f} s</td>'
        f'<td>{"%.2f s" % (r["endpointing"] / 1000) if r["endpointing"] is not None else "not available"}{note(r)}</td>'
        f'<td>{"%.2f s" % (r["response"] / 1000) if r["response"] is not None else "not available"}{note(r)}</td></tr>'
        for r in rows if r["kind"] == kind)
    return ('<table><thead><tr><th>Service</th><th>Turns</th><th>Median<small>95% interval</small></th><th>p90</th><th>p95</th>'
            f'<th>Wait for end of speech<small>median</small></th><th>Reply start after that<small>median</small></th></tr></thead><tbody>{body}</tbody></table>')


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Latency benchmark · 31 October 2026</title>
<style>
:root{{--bg:#f5f6f8;--card:#fff;--fg:#151a22;--muted:#5b6575;--line:#d9dde4;--warn:#8a5a00;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#11151b;--card:#1a2029;--fg:#e7ebf1;--muted:#9aa5b5;--line:#2c3441;--warn:#f0b94d;color-scheme:dark}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:24px 16px 56px}}h1{{margin:.2em 0}}h2{{margin-top:2em}}
section,.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 20px;margin:16px 0;overflow-x:auto}}
table{{border-collapse:collapse;width:100%;min-width:760px}}th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
small{{display:block;color:var(--muted);font-weight:400}}.note{{color:var(--warn)}}a{{color:inherit}}p.lead{{color:var(--muted);max-width:70ch}}
</style></head><body><main>
<p><a href="../index.html">← Reports</a></p>
<h1>Latency benchmark</h1>
<p class="lead">How long a caller waits for the reply, with every service on its own end-of-speech detection. Four sessions on 31 October 2026,
{turns} timed turns, six providers. Latency only: this is a separate measurement from the quality bake-offs and changes none of their results.</p>
<section><h2 style="margin-top:0">What was measured</h2>
<p><b>Caller-felt latency</b> is the time from the end of the caller's voice to the first reply audio. Where a service reports its own
end-of-speech signal it splits into the <b>wait for end of speech</b> (the caller has stopped, the service is still deciding) and the
<b>reply start</b> after that signal. ElevenLabs and GPT-Live expose no usable signal, so only the total is shown for them. Gemini's end-of-speech marker was not recorded in the four pooled sessions; a fifth, shorter session (marked †) recorded it, so Gemini's split comes from that session alone and is not pooled into the other figures.</p>
<p>Earlier bake-offs told three services the turn had ended after an emulated 500 ms, so their latency could not be ranked
(<code>mixed_turn_control</code>). Here nothing is emulated.</p></section>
<h2>Plain answers</h2><div class="card">{plain}</div>
<h2>Answers that call a tool</h2><div class="card">{tool}</div>
<h2>Reading it</h2>
<section><ul>
<li><b>Plain answers:</b> {plain_text}</li>
<li><b>Tool answers:</b> {tool_text}</li>
<li>Where two intervals overlap the services are not shown to differ. Plain-answer samples are small ({plain_min} to {plain_max} turns per service).</li>
<li>The wait for end of speech is a large share of the total for the three services that report it, about 0.9 s for Nova Sonic and OpenAI
Realtime and about 1.15 s for xAI. It is set by the service's own silence rule, not only by its engine speed.</li></ul></section>
<h2>Method and limits</h2>
<section><ul>
<li><b>Sessions:</b> four, at 11:19Z, 11:57Z, 14:20Z and 17:20Z. The first held tool scenarios only and 1b held single-turn plain scenarios only; sessions 2 and 3 held both.
Tool and plain turns are reported separately.</li>
<li><b>Prompt:</b> standardised-v3 for every provider. Scripted fixed caller voice, trailing synthesiser silence trimmed so the end of speech is the end of the voice.</li>
<li><b>Turn detection:</b> OpenAI Realtime and xAI at 700 ms silence, Gemini at 600 ms, picked from a short calibration pass in which 300 ms and 500 ms cut a deliberate 600 ms hesitation off for OpenAI Realtime and xAI (three trials per setting, rough). Nova, ElevenLabs and GPT-Live ran as shipped.</li>
<li class="note"><b>One location and network</b>, with providers running at the same time from one machine. Providers hosted nearby are favoured.</li>
<li class="note"><b>Not comparable with earlier bake-off latency:</b> those figures anchored on a marker about 220 ms after the voice stopped. This benchmark anchors on the end of the voice.</li>
<li>Turns that overlapped the caller are excluded. Judges and independent speech recognition were off; latency needs neither.</li>
<li>Intervals are bootstrap intervals of the median (fixed seed). Turns within a session are not independent, so intervals are optimistic.</li>
<li>Data: <a href="../artifacts/latency-benchmark-2026-10-31/results.json">per-turn results</a>. Rebuild with <code>scripts/build_latency_report.py</code>.</li></ul></section>
</main></body></html>
"""


def sentence(rows, kind):
    mine = sorted((r for r in rows if r["kind"] == kind), key=lambda r: r["median"])
    first = mine[0]
    tied = [r for r in mine[1:] if r["lo"] <= first["hi"]]
    slow = mine[-1]
    text = f'{NAMES[first["provider"]]} had the lowest median ({first["median"] / 1000:.2f} s)'
    text += (", with " + ", ".join(NAMES[r["provider"]] for r in tied) + " not separable by interval") if tied else ", clear of the others by interval"
    return text + f'. {NAMES[slow["provider"]]} was slowest ({slow["median"] / 1000:.2f} s).'


def main():
    turns, cells = collect()
    split_turns, _ = collect(SUPPLEMENT, SPLIT_ONLY)
    rows = summarise(turns, split_turns)
    out = ROOT / "artifacts/latency-benchmark-2026-10-31"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(dict(
        generated="2026-10-31", sessions=SESSIONS, supplementary_sessions=SUPPLEMENT, prompt_profile="standardised-v3", settings=SETTING, cells=cells,
        summary=rows, turns=turns), indent=1) + "\n")
    plain = [r["n"] for r in rows if r["kind"] == "plain"]
    page = PAGE.format(turns=len(turns), plain=table(rows, "plain"), tool=table(rows, "tool"), plain_text=sentence(rows, "plain"),
                       tool_text=sentence(rows, "tool"), plain_min=min(plain), plain_max=max(plain))
    (ROOT / "reports/latency-benchmark-2026-10-31.html").write_text(page)
    print(f"{len(turns)} turns, cells {cells}")


if __name__ == "__main__":
    sys.exit(main())
