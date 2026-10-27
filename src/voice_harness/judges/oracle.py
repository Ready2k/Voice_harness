"""Read-only judge oracle (spec section 7, ground truth).

The judge never receives a database handle, tool credentials or the live engine: only a
JSON snapshot of ledger events, the fixture snapshot, expected facts/actions, the policy
version and criterion definitions. Data leaving the machine is synthetic fixture data.

Provider blinding is structural: events that only describe the provider session are left out,
identity-bearing keys are stripped, provider-issued ids are pseudonymised, and every remaining
string is masked case-insensitively against identity tokens derived from the live-provider
registry and the adapter sources. ``identity_leaks`` scans the finished oracle, so the blinding
claim in the independence record is a measurement, not an assertion.
"""

from __future__ import annotations

import ast
import hashlib
import json
import random
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..canonical import canonical_json
from .rubric import CRITERIA, EVALUATION_VERSION, RUBRIC_ID, RUBRIC_VERSION

DATA_CLASSIFICATION = "synthetic_fixture"
# Events that describe the provider session or its billing, not the conversation: no criterion needs them.
DROP_TYPES = frozenset({"adapter.native_event", "adapter.session_ready", "cost.usage_reported",
                        "cost.expectation_declared", "benchmark.adapter_metrics", "provider.usage",
                        "provider.connected"})
# Keys never sent to a judge: raw provider payloads, trace plumbing and provider/model/voice/connection identity
# (run.live_binding ``adapter``/``provider``, caller/ASR identity, connection provenance).
STRIP_KEYS = frozenset({
    "provider_event", "provider_event_type", "trace_context", "adapter", "provider", "provider_config_id", "family",
    "model", "model_id", "requested_model_id", "returned_model_id", "backend_model_id", "model_version",
    "provider_session_id", "endpoint_host", "region", "sdk_versions", "sdk_version", "voice", "voice_id",
    "requested_voice", "native_type", "backend", "asr_backend", "service", "target_prompt", "caller_voice",
    "harness_transport", "capability_report_id",
})
# Provider-issued ids keep their linkage but lose their vendor-specific format.
ALIAS_KEYS = {"call_id": "call", "tool_call_id": "call", "response_id": "resp", "item_id": "item"}
# Vendor, product and host brands; matched case-insensitively at a word start, so ``NovaSonicAdapter``,
# ``googleapis`` and ``OpenAI`` are all caught. Registry-derived tokens are added to these.
BRANDS = ("openai", "chatgpt", "gpt", "anthropic", "claude", "amazon", "aws", "bedrock", "nova", "sonic", "google",
          "gemini", "vertexai", "deepmind", "xai", "grok", "elevenlabs", "11labs")
# Matched as whole words only (common prefixes of ordinary words).
WORDS = ("x.ai", "eleven labs", "alexa", "titan", "vertex ai")
# Published voice names of the tested providers that are not ordinary words; adapter defaults are added from source.
VOICES = ("alloy", "cedar", "coral", "marin", "shimmer", "matthew", "tiffany", "ara", "puck", "charon", "kore", "fenrir",
          "aoede", "zephyr", "george")
_GENERIC = {"live", "voice", "agent", "agents", "realtime", "latest", "flash", "native", "audio", "model", "preview",
            "speech", "mini", "pro", "turbo", "api", "www", "com", "io", "wss", "https", "v1"}
PROVIDER_MASK, VOICE_MASK = "[provider]", "[voice]"
_ADAPTERS = Path(__file__).resolve().parent.parent / "adapters"


@dataclass(frozen=True)
class Oracle:
    payload: str  # canonical JSON text; immutable by construction
    sha256: str

    def data(self) -> dict:
        return json.loads(self.payload)

    def event_ids(self) -> set[str]:
        return {e["event_id"] for e in self.data()["events"]}


def _adapter_identity() -> tuple[set[str], set[str]]:
    """Adapter class names, endpoint domains and default voices, read from the adapter sources (no import)."""
    names, voices = set(), set()
    for path in sorted(_ADAPTERS.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Adapter"):
                names.add(node.name)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                for host in re.findall(r"(?:wss|https)://([A-Za-z0-9.-]+)", node.value):
                    names.add(".".join(host.split(".")[-2:]))
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and "VOICE" in t.id for t in node.targets):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    voices.add(node.value.value)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                  and len(node.args) == 2 and isinstance(node.args[0], ast.Constant) and node.args[0].value == "voice"
                  and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
                voices.add(node.args[1].value)
    return names, voices


@lru_cache(maxsize=1)
def identity_tokens() -> dict[str, tuple[str, ...]]:
    """Tokens that identify a tested provider: {"brand": prefix-matched, "exact": whole-word, "voice": whole-word}."""
    from ..live_providers import FAMILIES, LIVE_PROVIDERS, TESTED_MODELS

    exact = set(LIVE_PROVIDERS) | set(LIVE_PROVIDERS.values()) | set(FAMILIES.values())
    for tested in TESTED_MODELS.values():
        exact |= {tested["provider"], tested["model_id"], tested["family"], *(tested.get(k) for k in ("host", "vendor"))}
    exact.discard(None)
    brands = set(BRANDS)
    for token in exact:  # distinctive parts of registry ids, so a new provider's names are masked without a list edit
        brands |= {p for p in re.split(r"[-_.\s]+", token.lower())
                   if len(p) >= 3 and not re.fullmatch(r"[\d.]+|v\d+", p) and p not in _GENERIC}
    names, voices = _adapter_identity()
    exact |= names | set(WORDS)
    return {"brand": tuple(sorted(brands)), "exact": tuple(sorted({t.lower() for t in exact})),
            "voice": tuple(sorted({v.lower() for v in (*VOICES, *voices)}))}


def _alternation(tokens):
    return "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))


@lru_cache(maxsize=32)
def _patterns(extra: tuple[str, ...] = ()):
    t = identity_tokens()
    exact = (*t["exact"], *(e.lower() for e in extra if e))
    left = r"(?<![a-z0-9])"
    return (re.compile(rf"{left}(?:{_alternation(exact)})(?![a-z0-9])|{left}(?:{_alternation(t['brand'])})", re.I),
            re.compile(rf"{left}(?:{_alternation(t['voice'])})(?![a-z0-9])", re.I))


def mask_text(text: str, extra: tuple[str, ...] = ()) -> str:
    provider, voice = _patterns(tuple(extra))
    return voice.sub(VOICE_MASK, provider.sub(PROVIDER_MASK, text))


def identity_leaks(text: str, extra: tuple[str, ...] = ()) -> list[str]:
    """Identity tokens present in ``text`` (case-insensitive); empty means none of the known tokens survived."""
    provider, voice = _patterns(tuple(extra))
    return sorted({m.group(0).lower() for p in (provider, voice) for m in p.finditer(text)})


def _alias(value: str, prefix: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:12]}"


def _collect_aliases(value, out: dict):
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ALIAS_KEYS and isinstance(v, str) and v:
                out.setdefault(v, _alias(v, ALIAS_KEYS[k]))
            else:
                _collect_aliases(v, out)
    elif isinstance(value, list):
        for v in value:
            _collect_aliases(v, out)


def _blind(value, aliases: dict, embedded, extra: tuple[str, ...]):
    if isinstance(value, str):
        if value in aliases:
            return aliases[value]
        return mask_text(embedded.sub(lambda m: aliases[m.group(0)], value) if embedded else value, extra)
    if isinstance(value, list):
        return [_blind(v, aliases, embedded, extra) for v in value]
    if isinstance(value, dict):
        return {mask_text(k, extra): _blind(v, aliases, embedded, extra) for k, v in value.items() if k not in STRIP_KEYS}
    return value


def _embedded(aliases: dict):
    """Ids embedded in other strings (idempotency keys) are replaced too; very short ids only on exact match."""
    long_ids = [a for a in aliases if len(a) >= 4]
    return re.compile(_alternation(long_ids)) if long_ids else None


def slim_event(event: dict, aliases: dict | None = None, extra: tuple[str, ...] = (), embedded=None) -> dict:
    aliases = aliases or {}
    content = _blind(event["content"], aliases, embedded or _embedded(aliases), extra)
    envelope = content.get("native_envelope") if isinstance(content, dict) else None
    if isinstance(envelope, dict):  # tool text is evidence data; keep only the structured result
        content = {**content, "native_envelope": {"structuredContent": envelope.get("structuredContent")}}
    return {
        "event_id": event["event_id"], "sequence": event["sequence"], "source": event["source"],
        "type": event["type"], "occurred_at": event.get("occurred_at"), "content": content,
    }


def build_oracle(events: list[dict], *, scenario: dict | None, fixture: dict | None, policy_version: str,
                 criteria: list[str], run_profile: str = "controlled-fixture", order_seed: int = 0,
                 identity: tuple[str, ...] = ()) -> Oracle:
    """``identity`` adds tested-model tokens (e.g. its model id) to the registry-derived mask list."""
    if run_profile != "controlled-fixture":
        raise ValueError("ORACLE_REQUIRES_SYNTHETIC_FIXTURE_DATA")
    expected_actions = [
        {"expected_intent": t["expected_intent"], "allowed_actions": t["allowed_actions"],
         "prohibited_actions": t["prohibited_actions"]}
        for t in (scenario or {}).get("turns", [])
    ]
    ordered = list(criteria)
    random.Random(order_seed).shuffle(ordered)  # nosec B311 - presentation order only
    kept = [e for e in events if e["type"] not in DROP_TYPES]
    aliases: dict[str, str] = {}
    _collect_aliases([e["content"] for e in kept], aliases)
    extra, embedded = tuple(sorted({i for i in identity if i})), _embedded(aliases)
    body = {
        "data_classification": DATA_CLASSIFICATION,
        "rubric": {"id": RUBRIC_ID, "version": RUBRIC_VERSION, "evaluation_version": EVALUATION_VERSION},
        "policy_version": policy_version,
        "fixture": fixture or {},
        "expected": {"actions": expected_actions, "goal": (scenario or {}).get("goal"),
                     "reference_transcript": (scenario or {}).get("reference_transcript")},
        "criteria": [CRITERIA[c].model_dump() for c in ordered],
        "events": [slim_event(e, aliases, extra, embedded) for e in kept],
    }
    text = canonical_json(body).decode()
    return Oracle(text, hashlib.sha256(text.encode()).hexdigest())
