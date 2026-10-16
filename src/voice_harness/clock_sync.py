"""Conservative four-timestamp offset windows; units are milliseconds."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    client_send: float
    server_receive: float
    server_send: float
    client_receive: float

    def estimate(self):
        delay = (self.client_receive - self.client_send) - (
            self.server_send - self.server_receive
        )
        if (
            delay < 0
            or self.client_receive < self.client_send
            or self.server_send < self.server_receive
        ):
            raise ValueError("CLOCK_DISCONTINUITY")
        offset = (
            (self.server_receive - self.client_send)
            + (self.server_send - self.client_receive)
        ) / 2
        return offset, delay / 2


class ClockWindow:
    def __init__(
        self, samples: list[Sample], measured_at: float, epoch=1, drift_ppm=100
    ):
        if len(samples) != 8:
            raise ValueError("EIGHT_SYNC_SAMPLES_REQUIRED")
        self.offset, self.uncertainty = min(
            (s.estimate() for s in samples), key=lambda pair: pair[1]
        )
        self.measured_at, self.epoch, self.drift_ppm = measured_at, epoch, drift_ppm
        self.invalidated = False

    def invalidate(self):
        self.invalidated = True

    def latency(self, server_start, client_end, now, epoch):
        age = now - self.measured_at
        if self.invalidated or epoch != self.epoch or not 0 <= age <= 60:
            return {
                "value_ms": None,
                "reason": "clock_unsynchronized",
                "rank_eligible": False,
            }
        uncertainty = self.uncertainty + age * self.drift_ppm / 1000
        return {
            "value_ms": client_end + self.offset - server_start,
            "uncertainty_ms": uncertainty,
            "rank_eligible": uncertainty <= 10,
            "confidence": "low" if uncertainty > 10 else "bounded",
            "profile": "four-timestamp-v1",
        }
