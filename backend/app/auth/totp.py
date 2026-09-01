"""RFC 6238 time-based one-time passwords, from the standard library.

TOTP is HMAC-SHA1 over a 30-second counter, truncated to six digits. That is
genuinely all of it, and writing it here keeps `app/auth` dependency-free —
the same reason `core.py` uses `hashlib.scrypt` instead of a password library.

SHA-1 is not a weakness here: HMAC-SHA1 has no practical break, and every
authenticator app in existence (Google Authenticator, Authy, 1Password, Bitwarden)
assumes SHA-1 unless told otherwise. Choosing SHA-256 would be marginally more
modern and would silently produce wrong codes in half the apps people use.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD_SECONDS = 30
SECRET_BYTES = 20  # what RFC 4226 recommends for HMAC-SHA1

# How many 30-second steps either side of now are accepted. One step means a
# code stays valid for at most 90 seconds, which covers a phone whose clock has
# drifted a little and a person who types slowly, without meaningfully widening
# the guess space.
DEFAULT_WINDOW = 1


def secret() -> str:
    """A fresh base32 secret, the encoding every authenticator app expects."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def uri(shared_secret: str, account: str, issuer: str = "Prizma") -> str:
    """The `otpauth://` URI behind every setup QR code.

    Returned as text rather than an image: a QR encoder would be a dependency
    whose only job is to draw a string the app can also just accept typed in,
    and on a phone the link is tappable, which is easier than scanning a code
    displayed on that same phone.
    """
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={shared_secret}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={PERIOD_SECONDS}"
    )


def _decode(shared_secret: str) -> bytes:
    padded = shared_secret.strip().replace(" ", "").upper()
    padded += "=" * (-len(padded) % 8)
    return base64.b32decode(padded, casefold=True)


def code_at(shared_secret: str, counter: int) -> str:
    digest = hmac.new(_decode(shared_secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    # Dynamic truncation, RFC 4226 §5.3: the low nibble of the last byte picks
    # which four bytes of the digest become the code.
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**DIGITS)).zfill(DIGITS)


def verify(shared_secret: str, code: str, window: int = DEFAULT_WINDOW) -> bool:
    """Constant-time comparison against every code in the window."""
    cleaned = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(cleaned) != DIGITS or not shared_secret:
        return False

    try:
        counter = int(time.time()) // PERIOD_SECONDS
        # `any(...)` over a generator would short-circuit and leak, through
        # timing, roughly which step matched. Summing every comparison keeps
        # the work constant.
        matches = sum(
            hmac.compare_digest(code_at(shared_secret, counter + step), cleaned)
            for step in range(-window, window + 1)
        )
    except (ValueError, TypeError, base64.binascii.Error):
        return False
    return matches > 0
