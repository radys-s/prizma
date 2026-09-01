"""Interactive local key setup. Run this yourself in your own terminal:

    cd backend
    python ../scripts/setup_keys.py

Keys are entered with getpass (never echoed to the screen), sent straight
into the Windows Credential Manager via `keyring`, and never touch a file,
the SQLite DB, or any chat transcript.
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.providers import keystore
from app.providers.registry import REGISTRY


def main() -> None:
    print("Nastavení API klíčů pro multiagent BYOK router.")
    print("Klíč se nikde nezobrazí ani neuloží do souboru — jen v OS credential store.\n")

    for provider in REGISTRY:
        configured = keystore.has_key(provider.id)
        status = "✓ nastaveno" if configured else "— chybí"
        print(f"[{status}] {provider.display_name}  ({provider.notes})")

    print("\nZadej ID providera pro nastavení/změnu klíče (prázdné = konec, 'del <id>' = smazat klíč).")
    id_list = ", ".join(p.id for p in REGISTRY)
    print(f"Dostupná ID: {id_list}\n")

    by_id = {p.id: p for p in REGISTRY}
    while True:
        raw = input("> ").strip()
        if not raw:
            break
        if raw.startswith("del "):
            target = raw.removeprefix("del ").strip()
            if target not in by_id:
                print(f"Neznámé ID: {target}")
                continue
            keystore.delete_key(target)
            print(f"Klíč pro {target} smazán.")
            continue
        if raw not in by_id:
            print(f"Neznámé ID: {raw}")
            continue
        key = getpass.getpass(f"API klíč pro {by_id[raw].display_name} (skrytý vstup): ").strip()
        if not key:
            print("Prázdný vstup, přeskočeno.")
            continue
        keystore.set_key(raw, key)
        print(f"Uloženo pro {raw}.")

    print("\nHotovo.")


if __name__ == "__main__":
    main()
