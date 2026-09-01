"""Move API keys from the Windows Credential Manager into the database.

    # local database
    python scripts/migrate_keys_to_db.py

    # straight into Neon, without the keys ever appearing on screen
    DATABASE_URL=postgresql+psycopg://... SECRET_KEY=... python scripts/migrate_keys_to_db.py

Reads every credential the app used to store under the `multiagent-app`
service, encrypts each one with `SECRET_KEY`, and writes it to
`DATABASE_URL` (or the local SQLite file when that is unset).

Nothing is printed but provider ids and outcomes — no key value is ever
written to the console, a log, or a file. The Credential Manager entries are
left exactly as they are, so this can be run twice, or run and then ignored.

Run it from the repository root with the backend venv:
    backend/.venv/Scripts/python.exe scripts/migrate_keys_to_db.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# The service name `keyring` was called with before round 4. Keep it here
# rather than importing it: `app/providers/keystore.py` no longer knows about
# keyring at all, which is the point of this script existing.
LEGACY_SERVICE = "multiagent-app"


def main() -> int:
    try:
        import keyring
    except ImportError:
        print(
            "keyring není nainstalovaný — už není závislostí aplikace.\n"
            "Pro jednorázovou migraci:  backend/.venv/Scripts/python.exe -m pip install keyring"
        )
        return 1

    from app import crypto
    from app.db import engine, init_db
    from app.providers import keystore
    from app.providers.registry import REGISTRY

    if not crypto.is_configured():
        print("SECRET_KEY chybí. Vygeneruj ho: python scripts/gen_secret.py")
        return 1

    print(f"cíl: {engine.url}")
    init_db()

    credential_ids = sorted({p.credential_id for p in REGISTRY})
    moved, skipped, missing = [], [], []

    for credential_id in credential_ids:
        try:
            value = keyring.get_password(LEGACY_SERVICE, credential_id)
        except Exception as exc:  # noqa: BLE001 - any backend failure is "not here"
            print(f"  ??  {credential_id}: Credential Manager selhal ({exc.__class__.__name__})")
            missing.append(credential_id)
            continue

        if not value:
            missing.append(credential_id)
            continue

        if keystore.is_shadowed(credential_id):
            # Writing it would be worse than useless: the row would be correct
            # and permanently ignored, and the next person to debug a wrong key
            # would be looking at the database instead of the environment.
            print(f"  --  {credential_id}: přeskočeno, přebíjí ho proměnná prostředí")
            skipped.append(credential_id)
            continue

        keystore.set_key(credential_id, value)
        # Read it back through the same path the app uses. "It wrote without
        # raising" is not proof the app can use it; a round trip is.
        if keystore.get_key(credential_id) != value:
            print(f"  !!  {credential_id}: zápis proběhl, ale kontrolní čtení nesedí")
            return 1
        print(f"  ok  {credential_id}")
        moved.append(credential_id)

    print(
        f"\npřeneseno {len(moved)}, přeskočeno {len(skipped)}, "
        f"bez klíče {len(missing)} z {len(credential_ids)}"
    )
    if missing:
        print("bez klíče: " + ", ".join(missing))
    print("\nZáznamy ve Windows Credential Manageru zůstaly nedotčené.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
