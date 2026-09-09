"""The handful of runtime toggles the settings dialog can flip.

Separate from `/api/providers` because it is not about keys: nothing here ever
touches the credential store. Same guard as every other protected router —
loopback, Origin allowlist, session cookie, and a CSRF token on the write.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import settings_store
from app.config import settings
from app.tools import sandbox

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsPayload(BaseModel):
    # None means "leave this one alone", so the dialog can send one field
    # without having to echo back the rest of the state it did not touch.
    read_pages: bool | None = None
    # Capped here as well as in the store: the store's limit protects the
    # context budget, this one rejects an absurd payload before it is parsed.
    custom_instructions: str | None = Field(
        default=None, max_length=settings_store.CUSTOM_INSTRUCTIONS_MAX * 2
    )
    local_tools: bool | None = None
    local_write: bool | None = None
    tools_roots: str | None = Field(
        default=None, max_length=settings_store.TOOLS_ROOTS_MAX * 2
    )


def _state() -> dict:
    return {
        "read_pages": settings_store.get_bool(settings_store.READ_PAGES),
        "custom_instructions": settings_store.get_text(
            settings_store.CUSTOM_INSTRUCTIONS
        ),
        "custom_instructions_max": settings_store.CUSTOM_INSTRUCTIONS_MAX,
        "local_tools": settings_store.get_bool(settings_store.LOCAL_TOOLS),
        "local_write": settings_store.get_bool(settings_store.LOCAL_WRITE),
        "tools_roots": settings_store.get_text(settings_store.TOOLS_ROOTS),
        # Kam agent skutečně vidí, včetně výchozí hodnoty, když si uživatel
        # nenastavil nic. Dialog to ukazuje jako placeholder — jinak by prázdné
        # pole vypadalo jako „nikam“, což je pravý opak toho, co znamená.
        "tools_roots_effective": [str(p) for p in sandbox.roots()],
        # Na veřejné adrese je nástroj, který čte disk a spouští programy,
        # vzdálený shell. Přepínač proto v public mode neexistuje — a UI to má
        # říct, ne ho jen tiše nezobrazit.
        "tools_supported": not settings.public_mode,
    }


@router.get("")
def get_settings():
    return _state()


@router.put("")
def update_settings(payload: SettingsPayload):
    if payload.read_pages is not None:
        settings_store.set_bool(settings_store.READ_PAGES, payload.read_pages)
    if payload.custom_instructions is not None:
        settings_store.set_text(
            settings_store.CUSTOM_INSTRUCTIONS,
            payload.custom_instructions,
            limit=settings_store.CUSTOM_INSTRUCTIONS_MAX,
        )
    # V public mode se přepínače neuloží ani když je někdo pošle ručně mimo UI.
    # `registry.available()` by je stejně přebilo, ale uložená jednička v
    # databázi by znamenala, že se lokální nástroje zapnou samy ve chvíli, kdy
    # se aplikace jednou spustí lokálně — a to je překvapení, které nikdo nechce.
    if not settings.public_mode:
        if payload.local_tools is not None:
            settings_store.set_bool(settings_store.LOCAL_TOOLS, payload.local_tools)
        if payload.local_write is not None:
            settings_store.set_bool(settings_store.LOCAL_WRITE, payload.local_write)
        if payload.tools_roots is not None:
            settings_store.set_text(
                settings_store.TOOLS_ROOTS,
                payload.tools_roots,
                limit=settings_store.TOOLS_ROOTS_MAX,
            )
    return _state()
