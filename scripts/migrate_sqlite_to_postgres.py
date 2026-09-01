"""Copy the local SQLite database into Postgres (Neon).

    DATABASE_URL=postgresql+psycopg://... backend/.venv/Scripts/python.exe \
        scripts/migrate_sqlite_to_postgres.py

Copies conversations, messages, app settings and encrypted provider keys, in id
order, so foreign keys land after what they point at. Read-only against the
SQLite file — it is opened, never written.

**Refuses to run if the target already holds rows.** Re-running would either
duplicate everything or collide on primary keys, and "it half-ran" is a much
worse state than "it did not run". Pass --force to overwrite a target you are
certain is disposable; it deletes the target's rows first and says so.

Provider keys copy across as ciphertext, so the *same* SECRET_KEY has to be set
on the server afterwards. If it will not be, skip them with --skip-keys and
paste the keys again in the settings dialog instead.

The auth tables (`appuser`, `authsession`) are deliberately not copied: the
local account is a 4-digit code hashed on this machine, and sessions are tied
to cookies in this browser. The server gets a fresh account on first sign-in.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# Order matters: a message references a conversation.
TABLES = ["conversation", "message", "appsetting", "providerkey"]
KEY_TABLE = "providerkey"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="wipe the target's rows first")
    parser.add_argument("--skip-keys", action="store_true", help="do not copy provider keys")
    args = parser.parse_args()

    from sqlalchemy import create_engine, inspect, text

    from app.config import settings

    if not settings.database_url:
        print("DATABASE_URL není nastavený — není kam kopírovat.")
        return 1
    if not settings.db_path.exists():
        print(f"Zdrojová databáze {settings.db_path} neexistuje.")
        return 1

    tables = [t for t in TABLES if not (args.skip_keys and t == KEY_TABLE)]

    source = create_engine(f"sqlite:///{settings.db_path}")
    target = create_engine(settings.database_url, pool_pre_ping=True)

    # Build the schema on the target from the models, not from the source's
    # DDL: SQLite's column types do not translate, and `create_all()` produces
    # the correct Postgres types for free.
    from app.db import init_db as _unused  # noqa: F401  (imports every model)
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(target)

    source_tables = set(inspect(source).get_table_names())
    target_inspector = inspect(target)

    with target.begin() as conn:
        for table in tables:
            if table not in source_tables:
                continue
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            if count and not args.force:
                print(f"Cílová tabulka {table} už má {count} řádků. Nic jsem neudělal.")
                print("Buď použij prázdnou databázi, nebo spusť s --force.")
                return 1

        if args.force:
            for table in reversed(tables):
                if table in source_tables:
                    deleted = conn.execute(text(f"DELETE FROM {table}")).rowcount
                    print(f"  --  {table}: smazáno {deleted} řádků v cíli")

    moved: dict[str, int] = {}
    for table in tables:
        if table not in source_tables:
            print(f"  --  {table}: ve zdroji není, přeskočeno")
            continue

        # Only columns that exist on *both* sides. The local file has been
        # through several rounds of additive migrations, and a column the
        # models no longer declare would fail the insert for every row.
        source_cols = [c["name"] for c in inspect(source).get_columns(table)]
        target_cols = {c["name"] for c in target_inspector.get_columns(table)}
        cols = [c for c in source_cols if c in target_cols]
        dropped = [c for c in source_cols if c not in target_cols]
        if dropped:
            print(f"  ..  {table}: sloupce mimo model, nekopírují se: {', '.join(dropped)}")

        with source.connect() as src:
            rows = src.execute(text(f"SELECT {', '.join(cols)} FROM {table}")).mappings().all()
        if not rows:
            moved[table] = 0
            print(f"  ok  {table}: 0")
            continue

        placeholders = ", ".join(f":{c}" for c in cols)
        insert = text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})")
        with target.begin() as conn:
            conn.execute(insert, [dict(r) for r in rows])
        moved[table] = len(rows)
        print(f"  ok  {table}: {len(rows)}")

    # Verify by counting on the target, not by trusting the inserts.
    print("\nkontrola v cíli:")
    ok = True
    with target.connect() as conn:
        for table, expected in moved.items():
            actual = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            mark = "ok" if actual == expected else "!!"
            if actual != expected:
                ok = False
            print(f"  {mark}  {table}: {actual} (čekáno {expected})")

    # Postgres sequences do not know about ids inserted explicitly, so the next
    # INSERT would try id=1 and collide with a row that already exists. This is
    # the single most common way a migration like this looks fine and then
    # breaks on the first new message.
    with target.begin() as conn:
        for table in moved:
            if not moved[table]:
                continue
            has_id = any(c["name"] == "id" for c in target_inspector.get_columns(table))
            if not has_id:
                continue
            conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:t, 'id'), "
                    "COALESCE((SELECT MAX(id) FROM " + table + "), 1))"
                ),
                {"t": table},
            )
            print(f"  ..  {table}: sekvence id posunuta za poslední řádek")

    if args.skip_keys:
        print("\nProvider klíče se nekopírovaly — zadej je znovu v nastavení.")
    elif moved.get(KEY_TABLE):
        print(
            f"\n{moved[KEY_TABLE]} klíčů přeneseno jako šifrovaný text. "
            "Na serveru musí být *stejný* SECRET_KEY, jinak je nepůjde přečíst."
        )

    print("\nZdrojový soubor zůstal nedotčený.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
