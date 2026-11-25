"""In-memory rate limits for sign-up and failed logins (threat model #2).

One control plane process serves the API, so counting in memory is enough; the counts reset when
it restarts, which is fine for slowing down guessing and mass sign-up.
"""

import time
from collections import deque
from collections.abc import Callable

from fastapi import Request

from .errors import ApiError


class TooManyRequests(ApiError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.headers = {"Retry-After": str(max(retry_after, 1))}


class RateLimiter:
    """At most `limit` events per `window` seconds for each key; limit 0 turns it off."""

    def __init__(
        self, limit: int, window: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self._events: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        now = self.clock()
        events = self._events.get(key)
        if events is None:
            if len(self._events) > 10_000:  # forget keys whose events have all expired
                self._events = {
                    k: v for k, v in self._events.items() if v and v[-1] > now - self.window
                }
            events = self._events[key] = deque()
        while events and events[0] <= now - self.window:
            events.popleft()
        return events

    def exceeded(self, key: str) -> bool:
        return self.limit > 0 and len(self._recent(key)) >= self.limit

    def hit(self, key: str) -> None:
        if self.limit > 0:
            self._recent(key).append(self.clock())

    def retry_after(self, key: str) -> int:
        events = self._recent(key)
        if not events:
            return 1
        return int(events[0] + self.window - self.clock()) + 1


def client_ip(request: Request) -> str:
    # Behind Cloudflare Tunnel every request reaches the app from 127.0.0.1; Cloudflare puts the
    # real address in CF-Connecting-IP and replaces any value a client sends. The API listens on
    # 127.0.0.1 only, so the header cannot be forged by bypassing Cloudflare.
    return request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client else "unknown"
    )
