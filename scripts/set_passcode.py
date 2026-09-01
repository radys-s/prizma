"""Nastaví nebo změní přístupový kód do appky. Spusť ve vlastním terminálu:

    cd backend
    python ../scripts/set_passcode.py

Kód se zadává přes getpass (nikdy se nevypíše) a ukládá se jako scrypt hash —
z databáze ho nejde přečíst zpátky. Změna kódu **zneplatní všechny přihlášené
session**, takže tohle je zároveň cesta, jak se odhlásit všude.

Účet je jeden (appka je single-user). Když ještě neexistuje, tenhle skript ho
založí; jinak jen přepíše hash.
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlmodel import Session

from app.auth import core
from app.db import engine, init_db
from app.models.auth import AppUser


def read_passcode() -> str | None:
    """Vrací ověřený kód, nebo None když uživatel skončil."""
    first = getpass.getpass("Nový přístupový kód: ").strip()
    if not first:
        print("Zrušeno — nic se nezměnilo.")
        return None
    if len(first) < core.MIN_PASSCODE_LEN:
        print(f"Kód musí mít aspoň {core.MIN_PASSCODE_LEN} znaky.")
        return None
    if getpass.getpass("Ještě jednou pro kontrolu: ").strip() != first:
        print("Kódy se neshodují — nic se nezměnilo.")
        return None
    return first


def apply(passcode: str) -> str:
    """Zapíše hash. Vrací větu o tom, co se stalo."""
    with Session(engine) as session:
        user = core.get_user(session)
        if user is None:
            user = AppUser(
                provider="local",
                subject="local",
                display_name="Já",
                password_hash=core.hash_passcode(passcode),
            )
            session.add(user)
            session.commit()
            return "Účet založen a kód nastaven."

        user.password_hash = core.hash_passcode(passcode)
        session.add(user)
        session.commit()
        session.refresh(user)
        # Starý kód už neplatí, takže ani session vydané pod ním nesmí přežít.
        core.revoke_all_for_user(session, user.id)
        return "Kód změněn, všechny přihlášené session odhlášeny."


def main() -> int:
    init_db()
    print("Přístupový kód do Prizmy.")
    print(f"Minimálně {core.MIN_PASSCODE_LEN} znaky. Zapomenutý kód nejde obnovit — jde jen přepsat tímhle skriptem.\n")

    passcode = read_passcode()
    if passcode is None:
        return 1
    print(apply(passcode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
