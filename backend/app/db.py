import logging
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings

log = logging.getLogger(__name__)


def normalize_database_url(raw: str) -> str:
    """Turn whatever the provider's copy button produced into a URL that boots.

    Neon hands out `postgresql://…` and plenty of tools still emit
    `postgres://…`. Both are *valid* Postgres URLs and both crash this app,
    because only `psycopg[binary]` is installed: SQLAlchemy maps the first onto
    the psycopg2 dialect (`ModuleNotFoundError: psycopg2`) and rejects the
    second outright (`Can't load plugin: sqlalchemy.dialects:postgres`). The
    only spelling that works is the hand-edited `postgresql+psycopg://`, which
    is a footgun that goes off *after* the image has built, as a crash loop.

    TLS is forced for the same reason it is not optional on a hosted database:
    libpq's default `sslmode=prefer` will silently accept an unencrypted
    connection, and the credentials in this URL are the whole database.
    """
    url = raw.strip()
    if not url:
        return url

    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    if scheme in ("postgres", "postgresql", "postgresql+psycopg2"):
        url = f"postgresql+psycopg://{rest}"
        scheme = "postgresql+psycopg"

    if not scheme.startswith("postgresql"):
        return url

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1") and "sslmode=" not in (parts.query or ""):
        query = f"{parts.query}&sslmode=require" if parts.query else "sslmode=require"
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    return url


def _build_engine():
    """SQLite on a laptop, Postgres on a server — one switch, two dialects.

    The two need genuinely different arguments, and passing either set to the
    other is an immediate crash: `check_same_thread` is a SQLite driver flag
    that psycopg rejects outright, and SQLite's default pool has nothing to
    pre-ping.

    `pool_pre_ping` and the short `pool_recycle` are not tuning, they are what
    makes Neon usable at all. Neon autosuspends its compute after five idle
    minutes; a connection held open across that suspend is dead, and without a
    pre-ping the first request after a quiet spell fails with a closed-socket
    error instead of just reconnecting. Prizma is idle far more often than it
    is busy, so this is the common path, not the edge case.
    """
    if settings.database_url:
        url = normalize_database_url(settings.database_url)
        # Scheme and host only. The rest of this string is the database
        # password, and a deploy log is not a secret store.
        parts = urlsplit(url)
        log.info("Databáze: %s na %s", parts.scheme, parts.hostname or "?")
        return create_engine(url, pool_pre_ping=True, pool_recycle=300)
    return create_engine(
        f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False},
    )


engine = _build_engine()

# Columns added to `message` after the table already existed in the wild.
# `SQLModel.metadata.create_all()` creates missing *tables* and nothing else —
# it will not touch a table that is already there, so a new field on the model
# silently turns into "no such column" at runtime on any existing database.
#
# Additive only, on purpose: every entry here appends a nullable column with a
# safe default. Nothing in this file drops, renames or rewrites anything, so
# running it against a database full of real conversations is a no-op past the
# first time.
_MESSAGE_COLUMNS: dict[str, str] = {
    "capability": "TEXT",
    "complexity": "TEXT",
    "effort": "TEXT",
    "planner_model": "TEXT",
    "substituted_from": "TEXT",
    "revised": "BOOLEAN NOT NULL DEFAULT 0",
    "search_mode": "TEXT",
    "sources": "TEXT",
    "parts": "TEXT",
    # What the answer cost. Nullable because every row written before this
    # existed has neither, and because plenty of providers never report usage —
    # a missing token count is normal, not an error.
    "latency_ms": "INTEGER",
    "tokens": "INTEGER",
    # Uvažování modelu a čas nad ním. Obojí nullable: většina slugů v katalogu
    # žádné neposílá a starší řádky ho nemají vůbec.
    "reasoning": "TEXT",
    "reasoning_ms": "INTEGER",
}


# Same story on `conversation`. Auto-compact needs somewhere to keep the folded
# prefix of a long thread, and pinning needs a flag; both land on a table that
# has existed since the first version of the app.
_CONVERSATION_COLUMNS: dict[str, str] = {
    # The compacted prefix, plain text, written by a cheap model. Nullable
    # because most conversations never get long enough to need one.
    "summary": "TEXT",
    # id of the last message folded into `summary`. Everything at or below it is
    # represented by the summary and is not resent; everything above is verbatim.
    "summarized_through_id": "INTEGER",
    "pinned": "BOOLEAN NOT NULL DEFAULT 0",
}

# And on `appuser`, which existed before it had any second factor to remember.
_APPUSER_COLUMNS: dict[str, str] = {
    "email_verified": "BOOLEAN NOT NULL DEFAULT 0",
    "totp_secret": "TEXT",
    "totp_confirmed": "BOOLEAN NOT NULL DEFAULT 0",
}

_COLUMNS: dict[str, dict[str, str]] = {
    "message": _MESSAGE_COLUMNS,
    "conversation": _CONVERSATION_COLUMNS,
    "appuser": _APPUSER_COLUMNS,
}


def _existing_columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def _migrate() -> None:
    """Repair databases that predate the columns above. SQLite only, on purpose.

    Every statement in here is SQLite dialect: `PRAGMA table_info`,
    `sqlite_master`, `BOOLEAN NOT NULL DEFAULT 0`. Postgres understands none of
    the first two and spells the third differently.

    Postgres gets the same intent through a different door, below: it has no
    `PRAGMA`, but it does have `ADD COLUMN IF NOT EXISTS`, which makes the whole
    check-then-alter dance one statement.

    Skipping Postgres entirely — as this used to — is only safe once. The first
    deploy is fine, because `create_all()` builds every table complete. The
    *second* time a column is added to a model, `create_all()` will not touch
    the table that already exists, the column silently is not there, and every
    read of that table 500s in production with no way to fix it short of
    hand-written SQL against Neon.
    """
    if engine.dialect.name != "sqlite":
        _migrate_postgres()
        return

    with engine.begin() as conn:
        for table, columns in _COLUMNS.items():
            # Table names come from the literal dict above, never from input.
            if not conn.exec_driver_sql(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
            ).first():
                continue  # fresh database — create_all() already built it correctly

            present = _existing_columns(conn, table)
            for column, ddl in columns.items():
                if column not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


# SQLite spells booleans as 0/1 and Postgres does not, so the handful of
# non-portable defaults above are translated rather than duplicated.
_PG_DDL = {
    "BOOLEAN NOT NULL DEFAULT 0": "BOOLEAN NOT NULL DEFAULT FALSE",
    "BOOLEAN NOT NULL DEFAULT 1": "BOOLEAN NOT NULL DEFAULT TRUE",
}


def _migrate_postgres() -> None:
    """The same additive repair, in one statement per column.

    `ADD COLUMN IF NOT EXISTS` has been in Postgres since 9.6 and is a no-op
    when the column is already there, so this runs on every boot and costs
    nothing after the first. Table and column names come from the literal dicts
    above — never from input.
    """
    with engine.begin() as conn:
        present = {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            )
        }
        for table, columns in _COLUMNS.items():
            if table not in present:
                continue  # fresh database — create_all() already built it correctly
            for column, ddl in columns.items():
                conn.execute(
                    text(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
                        f"{column} {_PG_DDL.get(ddl, ddl)}"
                    )
                )


def _backfill_identities() -> None:
    """Give every pre-existing account its `AuthIdentity` row.

    `AppUser.provider`/`subject` used to *be* the sign-in method. Round 4 moved
    that to its own table so an account can have several, and this is what
    stops the move from logging Tomáš out of his own app: without it his
    existing account would have zero identities and the access code would stop
    working the first time the server restarted.

    Written with the ORM rather than SQL so it runs identically on SQLite and
    Postgres, and it is a no-op once every user has at least one identity.
    """
    from app.models.auth import AppUser, AuthIdentity

    with Session(engine) as session:
        users = session.exec(select(AppUser)).all()
        if not users:
            return
        linked = {i.user_id for i in session.exec(select(AuthIdentity)).all()}
        added = False
        for user in users:
            if user.id in linked or not user.provider:
                continue
            session.add(
                AuthIdentity(
                    user_id=user.id,
                    provider=user.provider,
                    subject=user.subject or user.provider,
                    email=user.email,
                    display_name=user.display_name,
                    secret_hash=user.password_hash,
                )
            )
            added = True
        if added:
            session.commit()


def init_db() -> None:
    from app.models import auth  # noqa: F401  (register models before create_all)
    from app.models import conversation  # noqa: F401
    from app.models import provider_key  # noqa: F401
    from app.models import settings  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate()
    _backfill_identities()


def get_session():
    with Session(engine) as session:
        yield session
