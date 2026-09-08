"""A small fixed-window rate limiter for the routes that cost real money.

`throttle.py` already guards the sign-in routes, but it counts *failed*
credentials and persists them — the right shape for brute force, the wrong one
for "this endpoint is expensive". Three routes need the other kind:

* `POST /api/chat` — every call spends provider quota and holds a worker for
  up to two minutes.
* `POST /api/providers/{id}/test` — one outbound request per call.
* `GET /api/auth/oauth/{id}/start` — reachable without a session, and each call
  costs a slot in the pending-handshake table.

This is deliberately in-process rather than a dependency plus a Redis: the
deployment is a single uvicorn worker on a 512 MB instance (`Dockerfile`), so a
process-local dict *is* the whole picture. If that ever becomes two workers,
this stops being exact — and the failure mode is that the effective limit
doubles, which is survivable.

Counters are dropped on restart. That is fine here: these limits protect a
budget and a worker pool, not a secret.
"""

import time
from collections import deque

# route key -> ip -> timestamps of recent calls
_hits: dict[str, dict[str, deque[float]]] = {}


class RateLimited(Exception):
    """Raised with the number of seconds the caller should wait."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"retry after {retry_after}s")
        self.retry_after = retry_after


def check(key: str, ip: str, *, limit: int, window: float) -> None:
    """Records one call and raises `RateLimited` if `ip` is over `limit`."""
    now = time.monotonic()
    per_ip = _hits.setdefault(key, {})

    # Drop callers who have gone quiet, so the dict cannot grow without bound
    # on a public URL. Cheap: it only runs when the map is already large.
    if len(per_ip) > 512:
        for addr in [a for a, seen in per_ip.items() if not seen or now - seen[-1] > window]:
            del per_ip[addr]

    seen = per_ip.setdefault(ip, deque())
    while seen and now - seen[0] > window:
        seen.popleft()

    if len(seen) >= limit:
        raise RateLimited(max(1, int(window - (now - seen[0])) + 1))

    seen.append(now)


def reset() -> None:
    """Test hook."""
    _hits.clear()
