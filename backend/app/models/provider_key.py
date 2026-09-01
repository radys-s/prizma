from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProviderKey(SQLModel, table=True):
    """One encrypted API key per provider credential.

    The ciphertext and its nonce are stored together because they are useless
    apart — the nonce is not a secret, it just must never repeat for a given
    key, so a fresh one is generated on every write.

    Nothing here is readable without `SECRET_KEY`, which lives only in the
    server's environment. That is the whole reason a key may sit in a hosted
    database at all: a database dump on its own yields ciphertext.
    """

    credential_id: str = Field(primary_key=True)
    nonce: bytes
    ciphertext: bytes
    created_at: datetime = Field(default_factory=_now)
