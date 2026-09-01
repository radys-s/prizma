from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings


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
        return create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_recycle=300,
        )
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
    # What the answer cost. Nullable because every row written before this
    # existed has neither, and because plenty of providers never report usage —
    # a missing token count is normal, not an error.
    "latency_ms": "INTEGER",
    "tokens": "INTEGER",
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

    Rewriting it to be dialect-neutral would be pretending to support a
    migration that cannot exist: there is no Postgres database in the world that
    predates these columns, because the first one will be created by
    `create_all()` — correctly and complete — the moment this app first connects
    to Neon. Legacy repair is a legacy concern, and the legacy is a local file.
    """
    if engine.dialect.name != "sqlite":
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
