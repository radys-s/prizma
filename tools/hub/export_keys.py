#!/usr/bin/env python3
"""Write a copy of a Refrakt database that holds the provider keys and nothing else.

A hub only needs the keys. `app.db` also holds every conversation, so sending the
whole file puts the chat history on a second machine for no reason. This writes a
new database with the one `providerkey` table; Refrakt creates its other tables,
empty, when it starts on the copy.

    python tools/hub/export_keys.py [SOURCE] [DEST]

SOURCE defaults to backend/data/app.db and DEST to backend/data/hub/app.db. The
source is opened read-only and DEST is never overwritten. The keys stay encrypted:
the hub still needs `master.key` (or SECRET_KEY) to use them.

Standard library only, so the notebook's plain `python` runs it.
"""

import contextlib
import os
import sqlite3
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "backend" / "data"
TABLE = "providerkey"


def export(source: Path, dest: Path) -> int:
    """Copies the key rows into a new database at `dest`; returns how many."""
    # `mode=ro` also refuses to create a missing source, which a plain connect would.
    with contextlib.closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)) as db:
        schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
        if schema is None:
            raise SystemExit(f"{source} has no {TABLE} table: no key was ever saved in it.")
        rows = db.execute(f"SELECT * FROM {TABLE}").fetchall()

    dest.parent.mkdir(parents=True, exist_ok=True)
    # Created here, exclusively and owner-only, before SQLite writes a byte into it.
    os.close(os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    with contextlib.closing(sqlite3.connect(dest)) as copy:
        copy.execute(schema[0])
        if rows:
            copy.executemany(f"INSERT INTO {TABLE} VALUES ({', '.join('?' * len(rows[0]))})", rows)
        copy.commit()
    return len(rows)


def main(argv: list[str]) -> None:
    if len(argv) > 2 or any(a.startswith("-") for a in argv):
        raise SystemExit(__doc__)
    source = Path(argv[0]) if argv else DATA / "app.db"
    dest = Path(argv[1]) if len(argv) > 1 else DATA / "hub" / "app.db"
    try:
        count = export(source, dest)
    except FileExistsError:
        raise SystemExit(f"{dest} already exists. Delete it or name another file.") from None
    except sqlite3.Error as exc:
        raise SystemExit(f"Cannot read {source}: {exc}") from None
    print(f"{count} keys (still encrypted) written to {dest}. No conversations are in it.")


if __name__ == "__main__":
    main(sys.argv[1:])
