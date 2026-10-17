"""Gateway-side per-run tool-call rate limiter (AR-06)."""

import threading
import time


class RateLimiter:
    """Sliding window: at most `limit` calls per `window` seconds per key."""

    def __init__(self, limit=60, window=10.0, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits, self._lock = {}, threading.Lock()

    def allow(self, key):
        now = self.clock()
        with self._lock:
            hits = [t for t in self._hits.get(key, ()) if now - t < self.window]
            allowed = len(hits) < self.limit
            if allowed:
                hits.append(now)
            self._hits[key] = hits
            return allowed
