"""The handful of runtime toggles the settings dialog can flip.

Separate from `/api/providers` because it is not about keys: nothing here ever
touches the credential store. Same guard as every other protected router —
loopback, Origin allowlist, session cookie, and a CSRF token on the write.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import settings_store

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


def _state() -> dict:
    return {
        "read_pages": settings_store.get_bool(settings_store.READ_PAGES),
        "custom_instructions": settings_store.get_text(
            settings_store.CUSTOM_INSTRUCTIONS
        ),
        "custom_instructions_max": settings_store.CUSTOM_INSTRUCTIONS_MAX,
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
    return _state()
