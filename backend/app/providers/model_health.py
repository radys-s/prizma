"""Remembers which model slugs a provider has stopped serving.

When a provider retires a model, every request pinned to it fails with a 404
until someone edits the registry. Parking the dead slug for an hour lets the
next candidate take over immediately, and the TTL means a slug that was only
temporarily unavailable comes back on its own instead of needing a restart.

In-memory on purpose: this is a single local process, and a stale "dead" mark
surviving a restart would be worse than re-learning it in one request.
"""

import time

DEAD_TTL_SECONDS = 3600.0

_dead: dict[str, float] = {}  # litellm slug -> unix ts after which to retry it


def mark_dead(model: str, seconds: float = DEAD_TTL_SECONDS) -> None:
    _dead[model] = time.monotonic() + seconds


def is_dead(model: str) -> bool:
    until = _dead.get(model)
    if until is None:
        return False
    if until <= time.monotonic():
        del _dead[model]
        return False
    return True


def usable_models(models: tuple[str, ...]) -> list[str]:
    """Candidate slugs worth trying, best first.

    Falls back to the full list when every candidate is parked: retrying a
    known-dead slug is still better than telling the user nothing is available,
    and the resulting 404 refreshes the mark anyway.
    """
    alive = [m for m in models if not is_dead(m)]
    return alive or list(models)


def reset() -> None:
    """Test hook — drops all parked slugs."""
    _dead.clear()
