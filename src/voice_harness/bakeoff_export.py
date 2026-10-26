"""Bake-off report data and its standalone HTML export (one file, interactive offline, printable to PDF).

The operator page and the export draw the same report with the same renderer: ``apps/web/bakeoff-report.mjs``
and ``bakeoff-report.css`` are served to the page and inlined into the export, so the two cannot disagree.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .bakeoff import NAMES, report

TEMPLATE = Path(__file__).with_name("templates") / "bakeoff_report.html"
WEB = Path(__file__).resolve().parents[2] / "apps" / "web"
SHORT = {"openai-realtime-live": "OpenAI", "nova-sonic-live": "Nova", "xai-voice-live": "xAI", "gemini-live": "Gemini", "elevenlabs-live": "ElevenLabs"}


def report_data(payload, *, weights=None, title=None, source="Voice Harness ledger", generated_at=None):
    """What the renderer draws: the report plus the per-run observations behind it."""
    if payload.get("prompt_profile") == "advanced-v1":
        source += " · Advanced provider-specific prompts (advanced-v1)"
    if payload.get("prompt_profile") == "conversational-v1":
        source += " · Conversational phone manner (conversational-v1)"
    return {
        "title": title or payload["name"], "source": source,
        "generated_at": generated_at or time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "providers": payload["providers"], "names": NAMES, "short": SHORT,
        "report": report(payload, weights),
        "observations": [c["observation"] for c in payload["cells"] if c.get("observation")],
    }


def render(payload, **options):
    """The standalone report HTML for a bake-off payload whose finished cells carry observations."""
    data = report_data(payload, **options)
    renderer, css = (WEB / "bakeoff-report.mjs").read_text(), (WEB / "bakeoff-report.css").read_text()
    for name, text in (("renderer", renderer), ("stylesheet", css)):
        if "</script" in text.lower() or "</style" in text.lower():
            raise ValueError("REPORT_ASSET_NOT_INLINABLE:" + name)
    # Embedded as JSON in a script element: "</" is escaped so no value can close the element.
    encoded = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text()
    for marker, value in (("__TITLE__", _escape(data["title"])), ("__CSS__", css), ("__RENDERER__", renderer), ("__DATA__", encoded)):
        html = html.replace(marker, value, 1)
    return html


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
