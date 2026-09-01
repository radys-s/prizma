"""Authenticated encryption for secrets stored in the database.

AES-256-GCM. Every record gets a fresh 12-byte nonce, and the tag GCM produces
is what makes this *authenticated*: a row someone edited by hand, a nonce that
was reused, or the wrong master key all fail loudly at `decrypt()` instead of
handing back plausible-looking garbage that would then be sent to a provider as
an API key.

The master key never touches the database. It lives in `SECRET_KEY` — an
environment variable on the server, `backend/.env` locally — so a dump of the
database on its own decrypts to nothing.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import DATA_DIR, settings

KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # the size GCM is defined for; anything else weakens it

# Where the master key lives when nobody configured one — local development
# only. See `_local_key()`.
LOCAL_KEY_PATH = DATA_DIR / "master.key"


class CryptoError(RuntimeError):
    """Configuration or integrity failure. Never contains the plaintext."""


def _master_key() -> bytes:
    """The 32-byte key, accepted as base64 or hex.

    Read on every call rather than cached at import: a test harness that swaps
    `settings.secret_key` has to be able to prove that the *wrong* key fails,
    and a cached key would quietly keep decrypting with the old one.
    """
    raw = (settings.secret_key or "").strip()
    if not raw:
        return _local_key()

    # Length is the selector, not decoding success. A 64-character hex key is
    # *also* valid base64 (its alphabet is a subset and 64 % 4 == 0) and decodes
    # cleanly to 48 meaningless bytes — so "the first decoder that didn't throw"
    # would reject every hex key ever pasted. Only a decoder that yields exactly
    # 32 bytes has actually understood the input.
    for decode in (_from_base64, _from_hex):
        key = decode(raw)
        if key is not None and len(key) == KEY_BYTES:
            return key

    raise CryptoError(
        f"SECRET_KEY musí být {KEY_BYTES} bajtů v base64 nebo hex. "
        "Vygeneruj nový: python scripts/gen_secret.py"
    )


def _local_key() -> bytes:
    """A master key generated on first use, in the gitignored data directory.

    This exists so that running the app on a laptop needs no setup at all —
    the same promise `db_path` makes. It is a genuinely weaker arrangement than
    an environment variable, because the key sits next to the database it
    protects, and anyone who can read one can read the other. On one person's
    machine, where the alternative was the OS credential store on the same
    machine, that is an even trade.

    On a server it is not an even trade at all, so it is refused: in
    `public_mode` the key must come from the environment, where a database dump
    cannot reach it. `check_public_mode()` already stops the app from booting
    without one, and this is the second lock on the same door.
    """
    if settings.public_mode:
        raise CryptoError(
            "SECRET_KEY není nastavený. Na serveru musí být v proměnných prostředí — "
            "soubor vedle databáze by ji nechránil."
        )

    if LOCAL_KEY_PATH.exists():
        key = LOCAL_KEY_PATH.read_bytes()
        if len(key) == KEY_BYTES:
            return key
        raise CryptoError(
            f"{LOCAL_KEY_PATH} je poškozený ({len(key)} B místo {KEY_BYTES}). "
            "Smaž ho a zadej klíče znovu."
        )

    key = os.urandom(KEY_BYTES)
    LOCAL_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_KEY_PATH.write_bytes(key)
    return key


def _from_base64(raw: str) -> bytes | None:
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None


def _from_hex(raw: str) -> bytes | None:
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return None


def encrypt(plaintext: str) -> tuple[bytes, bytes]:
    """-> (nonce, ciphertext_with_tag). Both belong in the same row."""
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(_master_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce, ciphertext


def decrypt(nonce: bytes, ciphertext: bytes) -> str:
    """-> plaintext, or raises. There is deliberately no lenient mode.

    Returning `None` on failure would be worse than raising: `get_key` would
    read it as "no key configured", the provider would be skipped as
    unconfigured, and the actual problem — a wrong or rotated `SECRET_KEY` —
    would never be stated anywhere.
    """
    try:
        return AESGCM(_master_key()).decrypt(bytes(nonce), bytes(ciphertext), None).decode("utf-8")
    except InvalidTag as exc:
        raise CryptoError(
            "Uložený klíč nejde dešifrovat — SECRET_KEY se změnil, nebo je záznam poškozený. "
            "Zadej klíč znovu v nastavení."
        ) from exc


def is_configured() -> bool:
    """Whether encryption can work at all, without raising to find out."""
    try:
        _master_key()
    except CryptoError:
        return False
    return True
