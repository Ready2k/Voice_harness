"""Render live comparison results (.local/compare-<provider>.json) as one static HTML page.

Coverage-style presentation: providers side by side per scenario, outcomes as recorded, no ranking or score.
"""

import html
import json
import sys
from pathlib import Path

PROVIDERS = ["openai", "nova"]
LABEL = {"openai": "OpenAI Realtime (gpt-realtime-2.1)", "nova": "Amazon Nova 2.5 Sonic"}


def cell(result):
    if result is None:
        return '<td class="na">not run</td>'
    if result.get("exception"):
        return f'<td class="bad"><b>Run failed</b><br><small>{html.escape(result["exception"])}</small></td>'
    parts = []
    for turn in result.get("turns", []):
        status = turn["strict_speech_approval"]
        css = "ok" if status == "approved" else "bad"
        parts.append(f'<span class="pill {css}">turn {turn["turn"] + 1}: {html.escape(status)}</span>')
    for call in result.get("tool_calls", []):
        extra = f" · {call['error_code']}" if call.get("error_code") else ""
        fault = " · injected fault" if call.get("fault_injected") else ""
        parts.append(f'<div class="tool">{html.escape(call["name"])} → {html.escape(call["status"])}{html.escape(extra)}'
                     f'{fault} · {call["elapsed_ms"]} ms</div>')
    for err in result.get("adapter_errors", []):
        parts.append(f'<div class="tool warn">adapter error {html.escape(err["code"])}</div>')
    if result.get("interruptions"):
        parts.append(f'<div class="tool">interruptions: {result["interruptions"]}</div>')
    parts.append(f'<div class="meta">{result.get("wall_s", "?")} s wall · hash {html.escape(result["scenario_hash"][7:19])}</div>')
    return "<td>" + "".join(parts) + "</td>"


def render(data):
    ids = []
    for provider in PROVIDERS:
        for r in data.get(provider, []):
            if r["scenario_id"] not in ids:
                ids.append(r["scenario_id"])
    rows = []
    for sid in ids:
        cells = "".join(cell(next((r for r in data.get(p, []) if r["scenario_id"] == sid), None)) for p in PROVIDERS)
        rows.append(f"<tr><th scope=row>{html.escape(sid)}</th>{cells}</tr>")
    heads = "".join(f"<th>{html.escape(LABEL[p])}</th>" for p in PROVIDERS)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live provider comparison</title>
<style>
:root {{ --bg:#f6f7f9; --fg:#14181f; --muted:#5a6472; --card:#fff; --line:#d9dde3; --ok:#0b6b3a; --bad:#b3261e; --warn:#8a5a00 }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#12151a; --fg:#e8ebf0; --muted:#9aa4b2; --card:#1b2028; --line:#2c3440; --ok:#5fd394; --bad:#ff8a80; --warn:#f0b94d }} }}
body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,sans-serif }}
main {{ max-width:1100px; margin:0 auto }} h1 {{ font-size:1.4rem; margin:0 0 4px }} .sub {{ color:var(--muted) }}
table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line) }}
th,td {{ text-align:left; vertical-align:top; padding:10px; border-bottom:1px solid var(--line) }}
thead th {{ position:sticky; top:0; background:var(--card) }} tbody th {{ font-weight:600; overflow-wrap:anywhere; width:22% }}
.pill {{ display:inline-block; margin:0 6px 4px 0; padding:1px 8px; border-radius:10px; font-size:.8rem; border:1px solid var(--line) }}
.pill.ok {{ color:var(--ok); border-color:var(--ok) }} .pill.bad {{ color:var(--bad); border-color:var(--bad) }}
.tool {{ font-size:.85rem }} .tool.warn,.warn {{ color:var(--warn) }} .meta,.na {{ color:var(--muted); font-size:.8rem }} .bad {{ color:var(--bad) }}
.note {{ color:var(--muted); font-size:.85rem; margin-top:16px }}
@media (max-width:700px) {{ .scroll {{ overflow-x:auto }} table {{ min-width:700px }} }}
</style></head><body><main>
<h1>Live provider comparison</h1>
<p class="sub">Same synthetic callers, tools and instructions on each provider. Outcomes are as recorded; there is no ranking or score.</p>
<div class="scroll"><table><thead><tr><th>Scenario</th>{heads}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<p class="note">“approved” means the reply passed independent ASR and the whole-response approval gate. One sample per cell, synthetic data only; a single run is not evidence of rates. Tool latency is the gateway round trip, including any injected delay.</p>
</main></body></html>"""


if __name__ == "__main__":
    base = Path(sys.argv[1] if len(sys.argv) > 1 else ".local")
    data = {p: json.loads((base / f"compare-{p}.json").read_text()) for p in PROVIDERS if (base / f"compare-{p}.json").exists()}
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/phase4/comparison.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data))
    print(out)
