"""Records provider-reported usage from live adapters, ASR and judges as ``cost.usage_reported`` events.

Rules (spec section 11, AC-12, AC-19):

* only what the provider reported is recorded; nothing is estimated and nothing is zero by default;
* OpenAI ``response.done`` usage is per response (incremental, scope = response id); Nova ``usageEvent``
  totals are cumulative per provider session, so each is a new *revision* of the same session report
  (``revision`` = provider ``totalTokens``, monotonic) which supersedes, never adds to, the earlier one;
* a cancelled or failed generation records the usage the provider returned with its generation state and
  a ``BILLED_STATUS_UNKNOWN`` assumption (whether the provider bills it is not established here);
* a turn that ended without usage records an unknown line with a reason, never a zero;
* delivery is idempotent: the recorder skips a report whose (meter, scope, revision) key is already in the
  ledger, so duplicate delivery and reconnect replays append nothing.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal

from . import cost

CUMULATIVE_PROVIDERS = {"aws_nova_sonic"}
SERVICES = {"openai": "realtime", "aws_nova_sonic": "bedrock"}
BILLED_UNKNOWN = "BILLED_STATUS_UNKNOWN: provider reported usage for a cancelled or failed generation"
NO_USAGE = "PROVIDER_RETURNED_NO_USAGE"


def _key(report):
    return (report["meter_id"], report["billing_scope_id"], report["revision"],
            "cumulative" if report["report_kind"] == "cumulative" else report["report_id"])


def _today():
    return datetime.now(UTC).date().isoformat()


class UsageRecorder:
    def __init__(self, database, run_id, *, provider, service=None, model=None, region=None, component="voice",
                 usage_mode=None, meter_map=None, clock=None):
        self.database, self.run_id = database, run_id
        self.provider, self.service = provider, service or SERVICES.get(provider, provider)
        self.model, self.region, self.component = model, region, component
        self.cumulative = (usage_mode == "cumulative_session") if usage_mode else provider in CUMULATIVE_PROVIDERS
        self.meter_map = dict(meter_map or {})
        self._clock = clock or _today
        self._seen = None
        self._turn = 0
        self._turn_usage = self._interrupted = False
        self._cumulative_scopes = set()
        self._session = None

    @classmethod
    def for_adapter(cls, database, run_id, adapter, **kw):
        caps = adapter.capabilities()
        return cls(database, run_id, provider=caps["provider"], model=caps.get("model_id"),
                   region=getattr(adapter, "region", None), **kw)

    # -- ledger helpers
    def _known(self):
        if self._seen is None:
            self._seen = {_key(e["content"]) for e in self.database.events(self.run_id)
                          if e["type"] == "cost.usage_reported"}
        return self._seen

    def _append(self, reports):
        ids = []
        for report in reports:
            key = _key(report)
            if key in self._known():
                continue  # duplicate delivery / replay after reconnect: idempotent
            ids.append(cost.record_usage(self.database, self.run_id, report))
            self._known().add(key)
        return ids

    def declare(self, *components):
        return cost.ensure_expected_components(self.database, self.run_id, list(components) or [self.component])

    # -- adapter events
    def observe(self, event):
        """Feed one normalised adapter event; returns the ids of cost events appended."""
        kind = event.type
        if event.session_id:
            self._session = event.session_id
        if kind == "turn_start":
            self._turn_usage = self._interrupted = False
        elif kind == "interrupted":
            self._interrupted = True
        elif kind == "usage":
            return self._usage(event)
        elif kind == "turn_end":
            return self._turn_end(event)
        return []

    def _turn_scope(self, event):
        return event.turn_id or f"{event.session_id or self._session or 'session'}:turn{self._turn}"

    def _common(self, **kw):
        return dict(service=self.service, model=self.model, region=self.region, component=self.component,
                    meter_map=self.meter_map, usage_date=self._clock(), **kw)

    def _usage(self, event):
        raw = dict(event.raw or {})
        if raw.get("cumulative"):
            scope = raw.get("provider_session_id") or event.session_id or self._session or "session"
            total = raw.get("totalTokens")
            if total is None:
                total = (event.input_tokens or 0) + (event.output_tokens or 0)
            self._cumulative_scopes.add(scope)
            return self._append(cost.reports_from_adapter_usage(
                self.provider, event, scope_id=scope, revision=max(1, int(total)), **self._common()))
        state = {"cancelled": "cancelled", "failed": "discarded"}.get(raw.get("response_status"))
        if state is None and self._interrupted and raw.get("response_status") is None:
            state = "cancelled"
        state = state or "completed"
        self._turn_usage = True
        return self._append(cost.reports_from_adapter_usage(
            self.provider, event, scope_id=self._turn_scope(event), generation_state=state,
            assumptions=[BILLED_UNKNOWN] if state != "completed" else (), **self._common()))

    def _turn_end(self, event):
        ids = []
        if not self.cumulative and not self._turn_usage:
            state = "cancelled" if self._interrupted else "completed"
            ids = self._unknown(self._turn_scope(event), False, state, NO_USAGE)
        self._turn += 1
        self._turn_usage = self._interrupted = False
        return ids

    def _unknown(self, scope, cumulative, state, reason):
        reports = cost.reports_from_adapter_usage(
            self.provider, {"input_tokens": None, "output_tokens": None, "raw": {"cumulative": cumulative}},
            scope_id=scope, generation_state=state, assumptions=[BILLED_UNKNOWN] if state != "completed" else (),
            **self._common())
        return self._append([{**r, "unknown_reason": reason} for r in reports])

    def close(self):
        """Session end: a cumulative-usage provider that never reported usage records unknown, not zero."""
        if self.cumulative and not self._cumulative_scopes:
            return self._unknown(self._session or "session", True, "completed", NO_USAGE)
        return []

    # -- independent ASR
    def record_asr(self, result, scope_id, *, failed_reason=None):
        """ASR usage as returned (seconds or tokens), else an unknown line with a reason; never zero."""
        usage = getattr(result, "usage", None)
        provider = result.backend if result is not None else "asr"
        model = getattr(result, "model", None)
        date = self._clock()
        if isinstance(usage, dict) and usage.get("type") == "duration" and usage.get("seconds") is not None:
            return self._append([self._asr_seconds(provider, model, scope_id, date,
                                                   format(Decimal(str(usage["seconds"])), "f"), None)])
        if isinstance(usage, dict) and usage.get("input_tokens") is not None:
            return self._append(cost.reports_from_adapter_usage(
                provider, {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                           "raw": {"input_token_details": usage.get("input_token_details")}},
                scope_id=scope_id, usage_date=date, service="transcription", model=model, component="asr",
                meter_map=self.meter_map))
        offline = result is not None and not result.live
        reason = failed_reason or ("OFFLINE_FIXTURE_NO_USAGE" if offline else NO_USAGE)
        return self._append([self._asr_seconds(provider, model, scope_id, date, None, reason)])

    def _asr_seconds(self, provider, model, scope_id, date, quantity, reason):
        return {
            "report_id": f"{scope_id}:asr_seconds:1", "component": "asr", "provider": provider,
            "service": "transcription", "model": model, "region": None,
            "meter_id": self.meter_map.get("asr_seconds", "asr_seconds"), "billing_scope_id": scope_id,
            "scope_type": "request", "report_kind": "incremental", "revision": 1, "corrects_report_id": None,
            "covers_scope_ids": [], "provenance": "provider_reported_usage", "quantity": quantity,
            "unit": "second", "cached_quantity": None, "generation_state": "completed", "usage_date": date,
            "interval_start_ms": None, "interval_end_ms": None, "estimation_method": None, "assumptions": [],
            "unknown_reason": reason, "evidence_event_ids": []}

    # -- judges
    def record_judge(self, config, usage, scope_id, *, failed=False):
        """Judge usage (``input_tokens``/``output_tokens``/``cached_tokens``) as the backend returned it, else unknown."""
        date, reports = self._clock(), []
        for meter, key, cached in (("judge_input_tokens", "input_tokens", (usage or {}).get("cached_tokens")),
                                   ("judge_output_tokens", "output_tokens", None)):
            quantity = None if not usage or usage.get(key) is None else str(int(usage[key]))
            reason = None if quantity is not None else ("JUDGE_CALL_FAILED_USAGE_UNKNOWN" if failed else NO_USAGE)
            reports.append({
                "report_id": f"{scope_id}:{meter}:1", "component": "judge", "provider": config.provider,
                "service": "judge", "model": config.model_id, "region": None,
                "meter_id": self.meter_map.get(meter, meter), "billing_scope_id": scope_id,
                "scope_type": "request", "report_kind": "incremental", "revision": 1, "corrects_report_id": None,
                "covers_scope_ids": [], "provenance": "provider_reported_usage", "quantity": quantity,
                "unit": "token", "cached_quantity": None if cached is None or quantity is None else str(int(cached)),
                "generation_state": "completed", "usage_date": date, "interval_start_ms": None,
                "interval_end_ms": None, "estimation_method": None, "assumptions": [],
                "unknown_reason": reason, "evidence_event_ids": []})
        return self._append(reports)


class CostMeteredJudge:
    """Wraps a ``JudgeBackend`` so every call records usage (or unknown) without touching the judge service."""

    def __init__(self, backend, database, run_id, **kw):
        self._backend = backend
        self.config = backend.config
        self._recorder = UsageRecorder(database, run_id, provider=backend.config.provider, component="judge", **kw)
        self._recorder.declare("judge")
        self._calls = 0

    def __getattr__(self, name):
        return getattr(self._backend, name)

    def complete(self, request):
        self._calls += 1
        digest = hashlib.sha256(request.user_payload.encode()).hexdigest()[:12]
        scope = f"judge:{self.config.judge_id}:{request.kind}:{digest}:{self._calls}"
        self._backend.last_usage = None
        try:
            out = self._backend.complete(request)
        except Exception:
            self._recorder.record_judge(self.config, self._backend.last_usage, scope, failed=True)
            raise
        self._recorder.record_judge(self.config, self._backend.last_usage, scope)
        return out
