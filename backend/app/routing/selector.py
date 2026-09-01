"""Builds the ordered list of providers to try for a capability.

Cooldown state is in-memory only (fine for a single local process). A 429 /
rate-limit error from a provider parks it for COOLDOWN_SECONDS instead of us
tracking each provider's published quota — those numbers change without
notice, but a live 429 never lies.

The chain deliberately ends with providers that *don't* advertise the
capability. A user who configured only Gemini asking a coding question is
better served by Gemini than by an error saying no coder is available.

On top of the capability tag the planner (app/routing/plan.py) supplies a
*complexity*, which reorders the same chain: "hard" pulls the biggest models
forward, "trivial" pulls the fastest forward. Nothing is ever dropped — the
ordering is a preference, and the fallback chain behind it stays intact.
"""

import time

from app.providers import keystore
from app.providers.registry import BY_ID, REGISTRY, ProviderModel, providers_for_capability

COOLDOWN_SECONDS = 60.0

# The app refuses to answer below this many *credential slots*. Tomáš asked for
# it explicitly: one key is a single point of failure, and the whole point of
# this router is having somewhere to fall back to. Counted over credential_id,
# so the two Mistral entries are one slot — the way the UI already counts them.
MIN_CONFIGURED_SLOTS = 2

_cooldowns: dict[str, float] = {}  # provider_id -> unix timestamp until which it's skipped


def mark_cooldown(provider_id: str, seconds: float = COOLDOWN_SECONDS) -> None:
    _cooldowns[provider_id] = time.monotonic() + seconds


def _in_cooldown(provider_id: str) -> bool:
    until = _cooldowns.get(provider_id)
    return until is not None and until > time.monotonic()


def _configured(providers: list[ProviderModel]) -> list[ProviderModel]:
    """Providers the app can actually call right now.

    `keyless` entries pass without a stored key — that is the whole reason the
    flag exists, and putting the exemption here means every chain built on top
    of `_configured` (candidates, searchers, planner_candidates) gets it for
    free instead of each growing its own special case. `configured_slots()`
    below deliberately does *not* use this function.
    """
    return [p for p in providers if p.keyless or keystore.has_key(p.credential_id)]


def configured_slots() -> set[str]:
    """Credential-store entries holding a key that can *answer* a question.

    Search-only slots are excluded, and that is the whole point of the filter.
    This set is what `MIN_CONFIGURED_SLOTS` counts, and the guarantee it buys
    is "there is somewhere to fall back to when a provider dies". A Tavily key
    is not somewhere to fall back to — connecting one alongside a single Groq
    key would satisfy the count while leaving exactly one model able to reply.

    Note this checks `keystore.has_key` directly rather than going through
    `_configured()`: the keyless exemption must not leak in here, or the app
    would consider itself ready to answer on one real key plus Wikipedia.
    """
    return {
        p.credential_id
        for p in REGISTRY
        if _can_answer(p) and keystore.has_key(p.credential_id)
    }


def _by_complexity(providers: list[ProviderModel], complexity: str) -> list[ProviderModel]:
    """Reorders one bucket. Python's sort is stable, so providers that tie keep
    their capability-based order."""
    if complexity == "hard":
        return sorted(providers, key=lambda p: -p.strength)
    if complexity == "trivial":
        return sorted(providers, key=lambda p: 0 if "fast" in p.capabilities else 1)
    return providers


def _can_answer(provider: ProviderModel) -> bool:
    """False for entries that exist only to serve the search stage."""
    return any(c != "search" for c in provider.capabilities)


def candidates(
    capability: str,
    complexity: str = "moderate",
    pinned: str | None = None,
) -> list[ProviderModel]:
    """Providers to try, best first.

    Preferred = configured, advertises the capability, not in cooldown. Then
    configured providers for other capabilities. Cooldowned providers go last
    rather than being dropped: a stale cooldown should never be the reason the
    user gets no answer at all.

    `pinned` is the user's manual pick. It goes to the front, but the normal
    chain stays behind it — Tomáš chose "switch and say so" over "fail loudly"
    when a pinned provider is down. `client.py` reports the substitution.

    Search-only providers never appear in the fallback tail. They are reachable
    when "search" is what was asked for, and otherwise invisible: letting the
    chain fall through to one meant a rate-limited Groq turned a "write me a
    poem" into a websearch agent writing the poem, spending the day's search
    quota on a request that never wanted the web.
    """
    preferred = _by_complexity(_configured(providers_for_capability(capability)), complexity)
    preferred_ids = {p.id for p in preferred}
    others = _by_complexity(
        [
            p
            for p in _configured(REGISTRY)
            if p.id not in preferred_ids and _can_answer(p)
        ],
        complexity,
    )

    ordered = [
        *[p for p in preferred if not _in_cooldown(p.id)],
        *[p for p in others if not _in_cooldown(p.id)],
        *[p for p in preferred if _in_cooldown(p.id)],
        *[p for p in others if _in_cooldown(p.id)],
    ]

    if pinned:
        chosen = BY_ID.get(pinned)
        # An unconfigured pin can't be honoured at all — leave the chain alone
        # and let the substitution note explain what happened.
        if chosen is not None and keystore.has_key(chosen.credential_id):
            ordered = [chosen, *[p for p in ordered if p.id != chosen.id]]

    return ordered


def planner_candidates() -> list[ProviderModel]:
    """Configured providers in planner order — fastest first.

    JSON-mode support is *not* checked here; app/routing/plan.py does that
    against litellm, so the rule stays true as litellm's capability map is
    updated rather than being frozen into this registry.

    Search-only entries are filtered out. Their `models` tuple is a display
    label, not a slug, so handing one to litellm as a planner can only fail —
    it used to fail *quietly*, inside `_can_do_json`'s except branch, which is
    the kind of thing that looks fine until something reads `[0]` directly.
    """
    ready = [
        p for p in _configured(REGISTRY) if _can_answer(p) and not _in_cooldown(p.id)
    ]
    return sorted(ready, key=lambda p: p.planner_rank)


def reset_cooldowns() -> None:
    """Test hook."""
    _cooldowns.clear()


class NoProviderAvailable(RuntimeError):
    """Keys exist, but nothing in the chain could be reached."""

    def __init__(self, capability: str):
        super().__init__(
            "Zatím nemáš uložený žádný funkční API klíč, takže není kam dotaz poslat. "
            "Otevři „API klíče“ v postranním panelu a přidej aspoň jeden."
        )
        self.capability = capability


class NotEnoughProviders(RuntimeError):
    """Fewer than MIN_CONFIGURED_SLOTS credential slots are filled."""

    def __init__(self, have: int):
        super().__init__(
            f"Máš připojený jen {have} klíč, ale Prizma potřebuje aspoň "
            f"{MIN_CONFIGURED_SLOTS} — bez záložního providera nemá kam přepnout, "
            "když první selže. Otevři „API klíče“ a přidej ještě jeden."
            if have == 1
            else f"Nemáš připojený žádný API klíč. Prizma potřebuje aspoň "
            f"{MIN_CONFIGURED_SLOTS}, aby měla kam přepnout, když jeden provider "
            "selže. Otevři „API klíče“ v postranním panelu."
        )
        self.have = have


def require_minimum() -> None:
    """Raises NotEnoughProviders unless enough credential slots are filled."""
    slots = configured_slots()
    if len(slots) < MIN_CONFIGURED_SLOTS:
        raise NotEnoughProviders(len(slots))
