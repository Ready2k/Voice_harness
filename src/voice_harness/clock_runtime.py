"""Runtime browser clock synchronisation over the control WebSocket (spec section 11).

The client runs eight four-timestamp exchanges at connect and again every 30 s. Each completed sample is persisted to
``harness_clock_sync_samples`` through ledger events; the window (minimum-delay
sample, conservative interval) is rebuilt by ``clock_sync.ClockWindow`` and
invalidated on reconnect, discontinuity, or age over 60 s. Units are ms.
"""

from __future__ import annotations

import math
import secrets
import time

from .clock_sync import ClockWindow, Sample

PROFILE = "four-timestamp-v1"
SAMPLES_PER_WINDOW = 8
REFRESH_SECONDS = 30
MAX_AGE_SECONDS = 60
SERVER_DOMAIN = "orchestrator_monotonic"
CLIENT_DOMAIN = "browser_performance_now"


def migrate(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS harness_clock_sync_samples ("
        "sample_id TEXT PRIMARY KEY, run_id TEXT REFERENCES harness_runs(run_id), "
        "connection_epoch INTEGER NOT NULL, window_round INTEGER NOT NULL, "
        "client_domain TEXT NOT NULL, server_domain TEXT NOT NULL, "
        "client_send DOUBLE PRECISION NOT NULL, server_receive DOUBLE PRECISION NOT NULL, "
        "server_send DOUBLE PRECISION NOT NULL, client_receive DOUBLE PRECISION NOT NULL, "
        "offset_ms DOUBLE PRECISION NOT NULL, uncertainty_ms DOUBLE PRECISION NOT NULL, "
        "chosen INTEGER NOT NULL DEFAULT 0, profile_version TEXT NOT NULL, "
        "measured_age_ms DOUBLE PRECISION, invalidation_reason TEXT)"
    )


def project(db, event):
    content, kind, run = event.content, event.type, event.run_id
    if kind == "clock.sync_sample":
        db.execute(
            "INSERT INTO harness_clock_sync_samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, NULL) ON CONFLICT DO NOTHING",
            (
                content["sample_id"],
                run,
                content["connection_epoch"],
                content["window_round"],
                content["client_domain"],
                content["server_domain"],
                content["client_send"],
                content["server_receive"],
                content["server_send"],
                content["client_receive"],
                content["offset_ms"],
                content["uncertainty_ms"],
                content["profile_version"],
            ),
        )
    elif kind == "clock.window_established":
        db.execute(
            "UPDATE harness_clock_sync_samples SET chosen=1 WHERE sample_id=?",
            (content["chosen_sample_id"],),
        )
    elif kind == "clock.window_invalidated":
        db.execute(
            "UPDATE harness_clock_sync_samples SET invalidation_reason=?, measured_age_ms=? "
            "WHERE run_id=? AND connection_epoch=? AND (window_round=? OR ?<0) AND invalidation_reason IS NULL",
            (
                content["reason"],
                content["age_ms"],
                run,
                content["connection_epoch"],
                content["window_round"],
                content["window_round"],
            ),
        )


class ClockSyncSession:
    """Server half of one connection's clock exchange; pure apart from ``record``."""

    def __init__(self, epoch, record, monotonic=time.monotonic):
        self.epoch, self.record, self.monotonic = epoch, record, monotonic
        self.round = 0
        self.pending = {}  # sample_id -> (server_receive_ms, server_send_ms, client_send)
        self.samples: list[tuple[str, Sample]] = []
        self.window: ClockWindow | None = None
        self.window_round = 0
        self.client_round = None

    def now_ms(self):
        return self.monotonic() * 1000

    def begin_round(self):
        """Start collecting a replacement window; the old one ages out on its own."""
        self.round += 1
        self.samples = []
        self.pending = {}

    def on_ping(self, payload, received_ms):
        client_send = payload.get("client_send")
        sample_id = payload.get("sample_id")
        if not isinstance(sample_id, str) or not _finite(client_send):
            raise ValueError("INVALID_CLOCK_PING")
        round_key = payload.get("round")
        if (
            self.round == 0
            or (round_key is not None and round_key != self.client_round)
            or (len(self.samples) >= SAMPLES_PER_WINDOW and not self.pending)
        ):
            self.begin_round()  # the client drives connect and 30 s refresh rounds
        self.client_round = round_key
        if len(self.pending) >= SAMPLES_PER_WINDOW:
            raise ValueError("CLOCK_SYNC_LIMIT")
        sent_ms = self.now_ms()
        self.pending[sample_id] = (received_ms, sent_ms, client_send)
        return {
            "type": "clock.pong",
            "sample_id": sample_id,
            "server_receive": received_ms,
            "server_send": sent_ms,
        }

    def on_sample(self, payload):
        expected = self.pending.pop(payload.get("sample_id"), None)
        client_receive = payload.get("client_receive")
        if expected is None or not _finite(client_receive):
            raise ValueError("INVALID_CLOCK_SAMPLE")
        receive, send, client_send = expected
        sample = Sample(client_send, receive, send, client_receive)
        try:
            offset, uncertainty = sample.estimate()
        except ValueError:
            self.record(
                "clock.discontinuity",
                {
                    "sample_id": payload["sample_id"],
                    "connection_epoch": self.epoch,
                    "reason": "CLOCK_DISCONTINUITY",
                },
            )
            self.invalidate("DISCONTINUITY")
            self.samples = []
            return None
        self.record(
            "clock.sync_sample",
            {
                "sample_id": payload["sample_id"],
                "connection_epoch": self.epoch,
                "window_round": self.round,
                "client_domain": CLIENT_DOMAIN,
                "server_domain": SERVER_DOMAIN,
                "client_send": client_send,
                "server_receive": receive,
                "server_send": send,
                "client_receive": client_receive,
                "offset_ms": offset,
                "uncertainty_ms": uncertainty,
                "profile_version": PROFILE,
            },
        )
        self.samples.append((payload["sample_id"], sample))
        if len(self.samples) == SAMPLES_PER_WINDOW:
            self.window = ClockWindow(
                [s for _, s in self.samples], self.monotonic(), epoch=self.epoch
            )
            self.window_round = self.round
            chosen = min(self.samples, key=lambda item: item[1].estimate()[1])[0]
            self.record(
                "clock.window_established",
                {
                    "connection_epoch": self.epoch,
                    "window_round": self.round,
                    "chosen_sample_id": chosen,
                    "offset_ms": self.window.offset,
                    "uncertainty_ms": self.window.uncertainty,
                    "drift_ppm": self.window.drift_ppm,
                    "profile_version": PROFILE,
                },
            )
        return sample

    def invalidate(self, reason):
        if self.window is None or self.window.invalidated:
            return False
        self.window.invalidate()
        self.record(
            "clock.window_invalidated",
            {
                "connection_epoch": self.epoch,
                "window_round": self.window_round,
                "reason": reason,
                "age_ms": (self.monotonic() - self.window.measured_at) * 1000,
            },
        )
        return True

    def expire_if_stale(self):
        if (
            self.window
            and not self.window.invalidated
            and self.monotonic() - self.window.measured_at > MAX_AGE_SECONDS
        ):
            return self.invalidate("SAMPLE_AGE")
        return False

    def latency(self, server_start_ms, client_end_ms):
        if self.window is None:
            return {
                "value_ms": None,
                "reason": "clock_unsynchronized",
                "rank_eligible": False,
            }
        return self.window.latency(
            server_start_ms, client_end_ms, self.monotonic(), self.epoch
        )


def invalidate_prior_connection(record, epoch):
    """A reconnect invalidates every window of the previous connection epoch."""
    record(
        "clock.window_invalidated",
        {
            "connection_epoch": epoch - 1,
            "window_round": -1,
            "reason": "RECONNECT",
            "age_ms": 0.0,
        },
    )


def new_sample_id():
    return "cs_" + secrets.token_hex(8)


def _finite(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
