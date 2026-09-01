"""What the model and effort pickers are allowed to offer.

Served from the registry and the effort table rather than hardcoded in the
frontend, so the two can't drift apart: a provider added to `registry.py` shows
up in the picker on the next reload, and a tier renamed in `effort.py` renames
itself in the UI.

`configured` is a boolean here, exactly as in `/api/providers` — this endpoint
reports what the user *can* pick, never anything about the keys themselves.
"""

from fastapi import APIRouter

from app.providers import keystore
from app.providers.registry import REGISTRY
from app.routing import effort as effort_tiers
from app.routing import research as researching
from app.routing import selector
from app.routing import websearch

router = APIRouter(prefix="/api/routing", tags=["routing"])

_AUTO_DESCRIPTION = (
    "Doporučeno. Nejdřív model zváží složitost a cíl úkolu, "
    "pak vybere toho nejvhodnějšího z připojených providerů."
)

# The globe control. Lives here rather than in the frontend for the same reason
# the effort tiers do: one list, so a mode cannot exist in the picker without
# existing in `research.MODES`.
_SEARCH_MODES = (
    ("auto", "Auto", "Doporučeno. Prizma sáhne na web sama, když si otázka žádá aktuální fakta."),
    ("web", "Web", "Vždycky nejdřív vyhledá na webu a odpověď doplní zdroji."),
    (
        "deep",
        "Deep research",
        "Rozdělí dotaz na podotázky, každou vyhledá zvlášť, nejlepší stránky si přečte celé "
        "a odpověď z nich složí. Počítej se dvěma minutami.",
    ),
    ("off", "Bez webu", "Nikdy nehledá. Odpovídá jen z toho, co model umí."),
)

def _keyless_only_hint() -> str:
    """Shown under the globe when nothing but the keyless floor is connected.

    The modes work — that is the point of the floor — but an encyclopedia and a
    preprint index answer "co je X" well and "co se stalo tenhle týden" not at
    all. The UI says so rather than letting the user find out from a bad answer.
    """
    sources = "ve Wikipedii a na arXivu" if websearch.wikipedia_available() else "na arXivu"
    return (
        f"Zatím není připojený žádný vyhledávací klíč, takže Prizma hledá jen "
        f"{sources}. Na pojmy a odbornou literaturu to stačí, na aktuální dění "
        "ne — přidej klíč (Jina, Exa, Tavily…) v „API klíče“."
    )


@router.get("/options")
def routing_options():
    slots = selector.configured_slots()
    models = [
        {
            "id": "auto",
            "label": "Auto",
            "description": _AUTO_DESCRIPTION,
            "configured": len(slots) >= selector.MIN_CONFIGURED_SLOTS,
            "recommended": True,
        },
        *[
            {
                "id": p.id,
                "label": p.display_name,
                "description": p.notes,
                "configured": keystore.has_key(p.credential_id),
                "recommended": False,
            }
            # Search-only entries are driven by the globe control, not pinned
            # as an answering model — see `ProviderModel.selectable`.
            for p in REGISTRY
            if p.selectable
        ],
    ]

    # The keyless Wikipedia/arXiv floor is always in the chain, so this is now
    # unconditionally true and the globe modes are never greyed out. That is
    # the intended outcome — but "can search" and "can search well" are not the
    # same claim, so `search_keyed` and `search_hint` carry the difference.
    chain = researching.searchers()
    can_search = bool(chain)
    keyed = [p for p, _ in chain if not p.keyless]

    return {
        "models": models,
        "efforts": [
            {"id": e.id, "label": e.label, "description": e.description}
            for e in effort_tiers.PROFILES
        ],
        "searches": [
            {
                "id": mode,
                "label": label,
                "description": description,
                "available": can_search or mode == "off",
            }
            for mode, label, description in _SEARCH_MODES
        ],
        "default_effort": effort_tiers.DEFAULT_EFFORT,
        "default_search": researching.DEFAULT_MODE,
        "can_search": can_search,
        "search_keyed": len({p.credential_id for p in keyed}),
        "search_hint": "" if keyed else _keyless_only_hint(),
        "configured_slots": len(slots),
        "min_slots": selector.MIN_CONFIGURED_SLOTS,
    }
