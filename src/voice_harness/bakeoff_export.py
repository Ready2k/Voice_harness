"""Standalone HTML export of a bake-off: one file with the data embedded, interactive offline, printable to PDF."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .bakeoff import NAMES, report

TEMPLATE = Path(__file__).with_name("templates") / "bakeoff_report.html"
SHORT = {"openai-realtime-live": "OpenAI", "nova-sonic-live": "Nova", "xai-voice-live": "xAI", "gemini-live": "Gemini"}


def render(payload, *, title=None, source="Voice Harness ledger", generated_at=None):
    """The report HTML for a bake-off payload whose finished cells carry observations."""
    data = {
        "title": title or payload["name"], "source": source,
        "generated_at": generated_at or time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "providers": payload["providers"], "names": NAMES, "short": SHORT,
        "report": report(payload),
        "observations": [c["observation"] for c in payload["cells"] if c.get("observation")],
    }
    # Embedded as JSON in a script element: "</" is escaped so no value can close the element.
    encoded = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text()
    return html.replace("__TITLE__", _escape(data["title"])).replace("__DATA__", encoded)


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
