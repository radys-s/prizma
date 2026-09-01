"""API key storage.

Two places a key can live, checked in this order:

1. **An environment variable**, `PRIZMA_KEY_<CREDENTIAL_ID>` upper-cased. The
   database is never consulted when one is present, and the key dialog refuses
   to overwrite it rather than writing a row that would be silently ignored.
2. **The database**, encrypted with AES-256-GCM (`app/crypto.py`). The master
   key is `SECRET_KEY` and lives only in the process environment, so a dump of
   the database decrypts to nothing.

This replaces the OS credential store. `keyring` worked on Tomáš's laptop and
nowhere else: inside a Linux container it has no backend, so every read either
raised or returned `None` — which reads as "no key configured" and would have
made every provider look unconfigured on the server, with no error anywhere.

The four functions below are the entire API and their signatures have not
changed, so `client.py`, `selector.py`, `providers.py` and every harness are
untouched by the move.

Nothing ever reads a key back out over HTTP: the API exposes only whether a
provider is configured. A key is written once and from then on is used solely
server-side.
"""

import os

from sqlmodel import Session, select

from app import crypto
from app.db import engine
from app.models.provider_key import ProviderKey

ENV_PREFIX = "PRIZMA_KEY_"

# `has_key` runs once per provider per routing decision and `get_key` once per
# call, so an uncached implementation would issue a dozen queries to Neon to
# answer one message — over a network, on a compute that autosuspends. The
# cache is per-process and every writer below invalidates it, which is correct
# here because this app has exactly one writer: itself.
_cache: dict[str, str | None] = {}


def _env_name(credential_id: str) -> str:
    return ENV_PREFIX + credential_id.upper().replace("-", "_")


def env_key(credential_id: str) -> str | None:
    """The environment override, or None. Public so the API can report it."""
    value = os.environ.get(_env_name(credential_id))
    return value.strip() or None if value else None


def is_shadowed(credential_id: str) -> bool:
    """True when an env var owns this key and the database cannot win."""
    return env_key(credential_id) is not None


def reset_cache() -> None:
    """For harnesses that swap `SECRET_KEY` or the engine underneath us."""
    _cache.clear()


def set_key(credential_id: str, api_key: str) -> None:
    """Encrypt and store. Raises `CryptoError` if there is no master key."""
    nonce, ciphertext = crypto.encrypt(api_key)
    with Session(engine) as session:
        row = session.get(ProviderKey, credential_id)
        if row is None:
            session.add(ProviderKey(credential_id=credential_id, nonce=nonce, ciphertext=ciphertext))
        else:
            row.nonce = nonce
            row.ciphertext = ciphertext
            session.add(row)
        session.commit()
    _cache[credential_id] = api_key


def get_key(credential_id: str) -> str | None:
    """The key, or None if this provider has none.

    A row that exists but will not decrypt is *not* None — that would report a
    rotated `SECRET_KEY` as "you never set this key up", and the real cause
    would never appear anywhere. It raises.
    """
    override = env_key(credential_id)
    if override is not None:
        return override

    if credential_id in _cache:
        return _cache[credential_id]

    with Session(engine) as session:
        row = session.get(ProviderKey, credential_id)

    value = None if row is None else crypto.decrypt(row.nonce, row.ciphertext)
    _cache[credential_id] = value
    return value


def delete_key(credential_id: str) -> None:
    with Session(engine) as session:
        row = session.get(ProviderKey, credential_id)
        if row is not None:
            session.delete(row)
            session.commit()
    _cache.pop(credential_id, None)


def has_key(credential_id: str) -> bool:
    """Whether a key is usable — which is not the same as a row existing.

    A stored key that cannot be decrypted is unusable, and this is the one
    place that must answer `False` for it rather than raising: it is called
    while *building the list of candidate providers*, so an exception here
    would take down routing entirely instead of dropping one provider. The
    error is still visible — `get_key` on the same id raises it — but that
    happens on a path that can report it to one request.
    """
    override = env_key(credential_id)
    if override is not None:
        return True
    try:
        return get_key(credential_id) is not None
    except crypto.CryptoError:
        return False


def stored_ids() -> list[str]:
    """Credential ids with a row in the database. Used by the migration script."""
    with Session(engine) as session:
        return list(session.exec(select(ProviderKey.credential_id)).all())
