"""Read-only provider billing queries; manual observations are never live balances."""

import asyncio
import datetime as dt
import json
import logging
import os
import re
import shutil

# Used only for the fixed AWS CLI read command below, without a shell.
import subprocess  # nosec B404
import time
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

PROVIDERS = {
    "openai": ("OpenAI", "Realtime, transcription and callers", "https://platform.openai.com/settings/organization/billing/overview",
               "The documented OpenAI Costs API reports spend, not remaining prepaid credit. Check billing and optionally save a dated balance below."),
    "xai": ("xAI", "Grok Voice · prepaid credit", "https://console.x.ai/",
            "Live prepaid credit needs a separate xAI Management API key and team ID on the server. An account admin must grant access to Management Keys before issuing it in xAI Console → Settings → Management Keys. The voice API key cannot read billing."),
    "google": ("Google Cloud", "Postpay account · Gemini Live on Vertex AI", "https://console.cloud.google.com/billing",
               "This Google Cloud billing account is Postpay, like AWS. The amount due is accrued spend, not credit remaining. To read it automatically, enable Cloud Billing export to BigQuery and connect the exported table; that integration is not configured yet."),
    "aws": ("AWS", "Estimated account spend · current month", "https://console.aws.amazon.com/billing/home",
            "AWS is shown as estimated month-to-date account spend, not credit remaining. Enable Cost Explorer and set AWS_billing_access_key_id plus AWS_billing_secret_access_key for a dedicated billing identity with ce:GetCostAndUsage. Bedrock credentials are not reused. Results refresh at most once per day."),
}
TTL = 60
AWS_TTL = 86400
GOOGLE_TTL = 86400
STALE_AFTER = 300
log = logging.getLogger("voice_harness.provider_balances")


class BalanceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    threshold: float = Field(10, ge=0, le=1e12)
    currency: Literal["USD", "GBP", "EUR"] = "USD"
    manual_balance: float | None = Field(None, ge=-1e12, le=1e12)
    # A separate action avoids accidentally re-dating a balance when changing an alert.
    record_balance: bool = False


def amount(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("INVALID_AMOUNT")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("INVALID_AMOUNT") from None
    if not result.is_finite() or abs(result) > Decimal("1e12"):
        raise ValueError("INVALID_AMOUNT")
    return result


async def xai_balance(env):
    key, team = env.get("XAI_MANAGEMENT_API_KEY"), env.get("XAI_TEAM_ID")
    if not key or not team:
        return {"status": "setup_required", "message": PROVIDERS["xai"][3]}
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.get(
            "https://management-api.x.ai/v1/billing/teams/" + quote(team, safe="") + "/prepaid/balance",
            headers={"Authorization": "Bearer " + key},
        )
        response.raise_for_status()
        # xAI's ledger uses negative cents for purchased/remaining credit; do not abs() debt.
        balance = -amount(response.json()["total"]["val"]) / 100
    return {"status": "live", "balance": float(balance), "currency": "USD",
            "message": "Prepaid credit only. Postpaid limits and promotional credits may affect whether calls can continue."}


def aws_balance(env):
    executable = shutil.which("aws")
    if not executable:
        return {"status": "setup_required", "message": PROVIDERS["aws"][3]}
    access_key = env.get("AWS_billing_access_key_id", "").strip()
    secret_key = env.get("AWS_billing_secret_access_key", "").strip()
    if not access_key or not secret_key:
        return {"status": "setup_required", "message": PROVIDERS["aws"][3]}
    # Billing uses its own dedicated credential pair. Strip ambient/session credentials
    # so the Bedrock role or an unrelated default identity can never silently be reused.
    aws_env = {**env, "AWS_PAGER": "", "AWS_MAX_ATTEMPTS": "1", "AWS_EC2_METADATA_DISABLED": "true"}
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN",
                 "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ROLE_ARN", "AWS_ROLE_SESSION_NAME",
                 "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
                 "AWS_CONTAINER_CREDENTIALS_FULL_URI"):
        aws_env.pop(name, None)
    aws_env["AWS_ACCESS_KEY_ID"] = access_key
    aws_env["AWS_SECRET_ACCESS_KEY"] = secret_key
    today = dt.datetime.now(dt.UTC).date()
    month_start = today.replace(day=1)
    next_month = (month_start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    # Cost Explorer data updates at most daily; use one monthly request and cache it for a day.
    result = subprocess.run(  # noqa: S603  # nosec B603
        [executable, "ce", "get-cost-and-usage", "--region", "us-east-1",
         "--time-period", f"Start={month_start.isoformat()},End={next_month.isoformat()}",
         "--granularity", "MONTHLY", "--metrics", "NetUnblendedCost", "--output", "json",
         "--cli-connect-timeout", "5", "--cli-read-timeout", "10"],
        env=aws_env, capture_output=True, text=True, timeout=18, check=False,
    )
    if result.returncode:
        message = "AWS cost check failed. Check the AWS session and Cost Explorer access."
        if "AccessDenied" in result.stderr:
            message = "AWS denied cost access. The dedicated billing IAM identity needs ce:GetCostAndUsage permission."
        elif "ExpiredToken" in result.stderr:
            message = "The dedicated AWS billing credentials were rejected or expired. Check that key pair, then refresh balances."
        elif "DataUnavailable" in result.stderr or "OptInRequired" in result.stderr:
            message = "Enable Cost Explorer in AWS Billing. Its first report can take about a day to become available."
        return {"status": "unavailable", "message": message}
    body = json.loads(result.stdout)
    buckets = body.get("ResultsByTime", [])
    cost = buckets[0].get("Total", {}).get("NetUnblendedCost") if buckets else None
    if not cost:
        return {"status": "unavailable", "message": "AWS Cost Explorer returned no current-month cost total."}
    currency = cost["Unit"].upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("INVALID_CURRENCY")
    return {"status": "live", "balance": float(amount(cost["Amount"])), "currency": currency,
            "kind": "spend", "period_start": month_start.isoformat(), "estimated": bool(buckets[0].get("Estimated")),
            "message": "Estimated month-to-date AWS account charges from Cost Explorer. Reporting can lag by more than a day; this is not a final invoice."}


def google_balance(env):
    """Estimated current-month Google Cloud spend from the standard BigQuery export."""
    project = env.get("GOOGLE_BILLING_PROJECT_ID") or env.get("GOOGLE_CLOUD_PROJECT")
    dataset = env.get("GOOGLE_BILLING_DATASET")
    if not project or not dataset:
        return {"status": "setup_required", "message": PROVIDERS["google"][3]}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", project) or not re.fullmatch(r"[A-Za-z0-9_]{1,1024}", dataset):
        return {"status": "setup_required", "message": "Google billing export configuration is invalid. Check the billing project and dataset IDs."}

    from .adapters.google_gemini_live import _fetch_access_token

    token = _fetch_access_token()
    today = dt.datetime.now(dt.UTC).date()
    month_start = today.replace(day=1)
    query = f"""
        SELECT SUM(cost + IFNULL((SELECT SUM(credit.amount) FROM UNNEST(credits) AS credit), 0)) AS net_cost,
               ANY_VALUE(currency) AS currency
        FROM `{project}.{dataset}.gcp_billing_export_v1_*`
        WHERE usage_start_time >= TIMESTAMP(@month_start)
          AND usage_start_time < TIMESTAMP(@tomorrow)
    """
    tomorrow = today + dt.timedelta(days=1)
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{quote(project, safe='')}/queries"
    body = {
        "query": query,
        "useLegacySql": False,
        "timeoutMs": 20000,
        "parameterMode": "NAMED",
        "queryParameters": [
            {"name": "month_start", "parameterType": {"type": "STRING"}, "parameterValue": {"value": month_start.isoformat()}},
            {"name": "tomorrow", "parameterType": {"type": "STRING"}, "parameterValue": {"value": tomorrow.isoformat()}},
        ],
        "location": "US",
    }
    with httpx.Client(timeout=25, follow_redirects=False) as client:
        response = client.post(url, headers={"Authorization": "Bearer " + token}, json=body)
        if response.status_code == 404 or (response.status_code == 400 and any(
                marker in response.text.lower() for marker in ("not found", "does not match any table"))):
            return {"status": "setup_required", "message": "Google billing export table is not available yet. Enable Standard usage cost export and allow time for the first data load."}
        response.raise_for_status()
        result = response.json()
        if result.get("errors"):
            err = result["errors"][0]
            reason = err.get("reason", "")
            if reason in ("accessDenied", "forbidden"):
                return {"status": "unavailable", "message": "Google denied BigQuery access. Grant the dashboard identity BigQuery Job User on the project and BigQuery Data Viewer on the export dataset."}
            if reason in ("notFound", "invalid") and "not found" in err.get("message", "").lower():
                return {"status": "setup_required", "message": "Google billing export table is not available yet. Enable Standard usage cost export and allow time for the first data load."}
            return {"status": "unavailable", "message": "Google BigQuery could not return the billing export. Check the export and try again later."}
        if not result.get("jobComplete"):
            return {"status": "unavailable", "message": "Google BigQuery billing query did not finish in time. Try again later."}
        rows = result.get("rows", [])
        if not rows or not rows[0].get("f") or rows[0]["f"][0].get("v") is None:
            return {"status": "unavailable", "message": "Google billing export returned no current-month spend yet."}
        fields = rows[0]["f"]
        currency = (fields[1].get("v") if len(fields) > 1 else None) or "USD"
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("INVALID_CURRENCY")
        return {"status": "live", "balance": float(amount(fields[0]["v"])), "currency": currency,
                "kind": "spend", "period_start": month_start.isoformat(),
                "message": "Estimated month-to-date Google Cloud charges from the standard billing export. Data is updated daily and may lag; this is not a final invoice."}


class BalanceService:
    def __init__(self, database):
        self.database = database
        self.cache = {}
        self.lock = asyncio.Lock()
        with database.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS harness_balance_settings (
                provider TEXT PRIMARY KEY, threshold DOUBLE PRECISION NOT NULL,
                currency TEXT NOT NULL, manual_balance DOUBLE PRECISION, recorded_at DOUBLE PRECISION)""")

    def save(self, provider, settings):
        if provider not in PROVIDERS:
            raise HTTPException(404, "UNKNOWN_PROVIDER")
        with self.database.connection() as db:
            old = db.execute("SELECT * FROM harness_balance_settings WHERE provider=?", (provider,)).fetchone()
            balance = settings.manual_balance if settings.record_balance else old["manual_balance"] if old else None
            recorded = time.time() if settings.record_balance and balance is not None else old["recorded_at"] if old else None
            if settings.record_balance and balance is None:
                recorded = None
            if old and balance is not None and old["currency"] != settings.currency and not settings.record_balance:
                raise HTTPException(422, "RECORD_BALANCE_IN_NEW_CURRENCY")
            db.execute("""INSERT INTO harness_balance_settings VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET threshold=excluded.threshold, currency=excluded.currency,
                manual_balance=excluded.manual_balance, recorded_at=excluded.recorded_at""",
                       (provider, settings.threshold, settings.currency, balance, recorded))
        return {"saved": True}

    async def _query(self, provider):
        try:
            if provider == "xai":
                result = await xai_balance(os.environ)
            elif provider == "google":
                result = await asyncio.to_thread(google_balance, dict(os.environ))
            else:
                result = await asyncio.to_thread(aws_balance, dict(os.environ))
        except httpx.HTTPStatusError as exc:
            if provider == "google" and exc.response.status_code in (401, 403):
                message = "Google denied BigQuery access. Grant the dashboard identity BigQuery Job User on the project and BigQuery Data Viewer on the export dataset."
            elif provider == "xai" and exc.response.status_code in (401, 403):
                message = "Billing access was denied. Check the management key, team and billing read permission."
            else:
                message = "The provider could not return a balance. Try again later or open billing."
            result = {"status": "unavailable", "message": message}
        except (httpx.HTTPError, OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("Provider balance read failed for %s (%s)", provider, type(exc).__name__)
            result = {"status": "unavailable", "message": "The balance check timed out or returned an unreadable response. Try again or open billing."}
        return provider, {**result, "checked_at": time.time()}

    async def snapshot(self):
        async with self.lock:
            now = time.time()
            expired = []
            for provider in ("xai", "aws", "google"):
                if provider not in self.cache:
                    expired.append(provider)
                    continue
                cached_at, reading = self.cache[provider]
                ttl = GOOGLE_TTL if provider == "google" and reading.get("status") == "live" else AWS_TTL if provider == "aws" and reading.get("status") == "live" else TTL
                if now - cached_at >= ttl:
                    expired.append(provider)
            if expired:
                for provider, reading in await asyncio.gather(*(self._query(p) for p in expired)):
                    self.cache[provider] = (time.time(), reading)
        with self.database.connection() as db:
            settings = {r["provider"]: dict(r) for r in db.execute("SELECT * FROM harness_balance_settings").fetchall()}
        items = []
        for provider, (name, scope, url, explanation) in PROVIDERS.items():
            saved = settings.get(provider, {})
            reading = self.cache.get(provider, (None, {"status": "unsupported", "message": explanation, "checked_at": None}))[1]
            items.append({"id": provider, "name": name, "scope": scope, "billing_url": url,
                          "balance": None, "currency": "USD", "kind": "spend" if provider in ("aws", "google") else "credit", **reading,
                          "threshold": saved.get("threshold", 10), "threshold_currency": saved.get("currency", "USD"),
                          "threshold_kind": "above" if provider in ("aws", "google") else "below",
                          "stale_after_seconds": AWS_TTL if provider in ("aws", "google") else STALE_AFTER,
                          "manual_balance": None if provider == "aws" else saved.get("manual_balance"), "manual_currency": saved.get("currency", "USD"),
                          "recorded_at": saved.get("recorded_at")})
        return {"items": items, "generated_at": time.time(), "refresh_after_seconds": TTL, "aws_refresh_after_seconds": AWS_TTL, "stale_after_seconds": STALE_AFTER}


def register_balances(app, database, auth):
    service = BalanceService(database)
    app.state.provider_balances = service
    router = APIRouter(prefix="/api/v1/provider-balances", dependencies=[Depends(auth)])

    @router.get("")
    async def balances(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if request.state.role != "operator":
            raise HTTPException(403, "FORBIDDEN")
        return await service.snapshot()

    @router.put("/{provider}")
    def save_balance(provider: str, settings: BalanceSettings):
        return service.save(provider, settings)

    app.include_router(router)
