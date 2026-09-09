"""Hranice toho, co smí model na tomhle počítači.

Tenhle modul nic neprovádí. Odpovídá jen na dvě otázky — „smí se sáhnout na
tuhle cestu?“ a „smí se spustit tenhle příkaz?“ — a dělá to na jednom místě,
aby se odpověď nedala obejít tím, že někdo přidá třetí nástroj.

Model, který nástroje volá, je běžně cizí model na free tieru. To mění dvě věci
proti tomu, co dělá vývojář ve vlastním terminálu:

1. Obsah každého přečteného souboru **odejde do cizí služby.** Odtud denylist
   níž — klíče, `.env`, databáze aplikace. Ne proto, že by je model zneužil,
   ale proto, že by je viděl provider.
2. Nikdo nemá jistotu, jak si model vyloží nejednoznačné zadání. Odtud povolené
   kořeny pro cesty a allowlist pro příkazy: chyba modelu pak skončí chybovou
   hláškou, ne smazaným adresářem.

Co tenhle modul **nedělá**: neizoluje procesy. Povolený `python` nebo `npm`
spustí libovolný kód pod uživatelovým účtem — to je smysl nástroje, ne jeho
selhání. Proto je spouštění příkazů vypnuté, dokud ho uživatel u konkrétní
zprávy nezapne, a proto je celá tahle vrstva mrtvá v public mode (viz
`tools/registry.py`).
"""

import os
import shlex
from pathlib import Path

from app import settings_store

# Kolik nejvíc textu smí jeden nástroj vrátit do konverzace. Přečtený soubor
# jde do promptu a odtud do kontextu modelu; na free tieru je kontext 8k–32k
# tokenů, takže 100 kB textu není „velký soubor“, ale konec konverzace.
MAX_OUTPUT_CHARS = 20_000

# Nikdy, bez ohledu na kořeny. Jména se porovnávají malými písmeny na celý
# segment cesty, ne na podřetězec — `.env` ano, `environment.ts` ne.
DENIED_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "app.db",
        "id_rsa",
        "credentials.json",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
    }
)

# Adresáře, které nemá smysl číst a je v nich hodně souborů: procházení by
# stálo minutu a v odpovědi by nebylo nic užitečného. `.git` je navíc místo,
# kde leží uložené přihlašovací údaje k remote.
DENIED_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__", "dist", ".next"})

# Přípony, které jsou skoro vždycky tajemství.
DENIED_SUFFIXES = frozenset({".key", ".pem", ".pfx", ".p12", ".keystore"})

# První slovo příkazu. Allowlist, ne denylist: seznam zakázaných věcí se dá
# obejít překlepem (`rm` vs `rm.exe`, `RM`), seznam povolených ne.
#
# `python`, `node` a `npm` jsou tu s plným vědomím, že spustí cokoliv. Bez nich
# by nástroj neuměl spustit testy ani build, což je většina toho, k čemu je
# dobrý. Ochrana proti nim není tenhle seznam, ale to, že spouštění příkazů si
# uživatel zapíná u každé zprávy zvlášť.
#
# Nejsou tu `dir`, `type`, `echo` ani `cd`: to nejsou programy, ale vestavěné
# příkazy `cmd.exe`. Bez shellu je není čím spustit a jejich uvedení by slibovalo
# něco, co skončí „soubor nenalezen“. Výpis složky a čtení souboru mají vlastní
# nástroje, které navíc respektují denylist — což `type` nedělá.
ALLOWED_COMMANDS = frozenset(
    {
        "git",
        "python",
        "python3",
        "py",
        "pip",
        "pytest",
        "ruff",
        "mypy",
        "node",
        "npm",
        "npx",
        "tsc",
        "eslint",
        "findstr",
        "where",
    }
)

class Denied(Exception):
    """Nástroj se pokusil o něco, co pravidla nedovolují.

    Není to chyba serveru — je to odpověď modelu. `registry.run` ji zabalí do
    textu, který se vrátí jako výsledek nástroje, takže model uvidí, proč
    neuspěl, a může zkusit jinou cestu.
    """


def _default_root() -> Path:
    """Kam agent kouká, když si uživatel nenastavil nic vlastního.

    Složka s projekty, ne celý disk a ne celý domovský adresář: v `Projekty`
    je to, o čem se s modelem baví, kdežto v `%USERPROFILE%` je i pošta,
    prohlížečové profily a záloha telefonu.
    """
    projects = Path.home() / "OneDrive" / "Desktop" / "Projekty"
    if projects.is_dir():
        return projects
    # Bez OneDrive: repozitář, ve kterém tenhle soubor leží. Vždycky existuje.
    return Path(__file__).resolve().parents[3]


def roots() -> list[Path]:
    """Povolené kořeny. Neexistující řádky se tiše zahodí."""
    text = settings_store.get_text(
        settings_store.TOOLS_ROOTS, settings_store.TOOLS_ROOTS_MAX
    )
    if not text.strip():
        return [_default_root()]
    found: list[Path] = []
    for line in text.splitlines():
        candidate = line.strip().strip('"')
        if not candidate:
            continue
        try:
            resolved = Path(candidate).resolve()
        except OSError:
            continue
        if resolved.is_dir():
            found.append(resolved)
    return found or [_default_root()]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve(raw: str, *, must_exist: bool = True) -> Path:
    """Cesta z argumentů modelu na skutečnou cestu, nebo `Denied`.

    `Path.resolve()` se volá **před** kontrolou kořenů, ne po ní: bez toho by
    `Projekty\\..\\..\\.ssh` prošlo, protože jako řetězec kořenem začíná.
    Zároveň tím padá i symlink, který vede ven z kořene.
    """
    if not raw or not raw.strip():
        raise Denied("Chybí cesta.")
    # Zkratky, které model rád použije, protože je zná ze shellu.
    expanded = os.path.expandvars(os.path.expanduser(raw.strip().strip('"')))
    try:
        path = Path(expanded).resolve()
    except OSError as exc:
        raise Denied(f"Neplatná cesta: {exc}") from exc

    allowed = roots()
    if not any(_inside(path, root) for root in allowed):
        where = ", ".join(str(r) for r in allowed)
        raise Denied(
            f"Cesta {path} je mimo povolené složky. Agent smí pracovat jen v: {where}"
        )

    lowered = {p.lower() for p in path.parts}
    if lowered & {d.lower() for d in DENIED_DIRS}:
        raise Denied(f"Cesta {path} vede do složky, která je pro agenta zavřená.")
    if path.name.lower() in DENIED_NAMES:
        raise Denied(f"Soubor {path.name} může obsahovat přihlašovací údaje a nečte se.")
    if path.suffix.lower() in DENIED_SUFFIXES:
        raise Denied(f"Soubor {path.name} vypadá jako klíč a nečte se.")

    if must_exist and not path.exists():
        raise Denied(f"Cesta {path} neexistuje.")
    return path


def check_command(command: str) -> list[str]:
    """Rozparsovaný příkaz, nebo `Denied`.

    Vrací seznam argumentů, ne řetězec, protože se spouští **bez shellu**.
    Kdyby se spouštěl shellem, `git status && curl … | sh` by prošlo kontrolou
    prvního slova a udělalo něco úplně jiného. Operátory se proto odmítají
    rovnou: bez shellu by stejně neudělaly nic než zmatený chybový výstup.
    """
    text = (command or "").strip()
    if not text:
        raise Denied("Prázdný příkaz.")
    for operator in ("&&", "||", "|", ";", ">", "<", "`", "$("):
        if operator in text:
            raise Denied(
                f"Příkaz obsahuje `{operator}`. Spouští se bez shellu, takže "
                "roury ani řetězení nefungují — pošli jeden příkaz."
            )
    try:
        argv = shlex.split(text, posix=False)
    except ValueError as exc:
        raise Denied(f"Příkaz se nepodařilo rozparsovat: {exc}") from exc
    if not argv:
        raise Denied("Prázdný příkaz.")

    head = Path(argv[0].strip('"')).name.lower()
    if head.endswith(".exe"):
        head = head[: -len(".exe")]
    if head not in ALLOWED_COMMANDS:
        allowed = ", ".join(sorted(ALLOWED_COMMANDS))
        raise Denied(f"Příkaz `{head}` není povolený. Povolené jsou: {allowed}")
    return argv


def clip(text: str) -> str:
    """Ořez výstupu na to, co se vejde do kontextu, s poznámkou o zbytku."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n\n[…zkráceno, celkem {len(text)} znaků]"
