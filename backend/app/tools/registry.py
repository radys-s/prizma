"""Nástroje, kterými model sahá na tenhle počítač.

Model dostane v požadavku seznam funkcí (`specs()`), místo odpovědi vrátí volání
jedné z nich, `run()` ji provede a výsledek se vrátí do konverzace jako další
zpráva. Model pak pokračuje — přečte si výsledek a buď odpoví, nebo sáhne po
dalším nástroji. Smyčka je v `providers/client.py`.

Rozdělení na tři úrovně oprávnění není kosmetika, každá znamená něco jiného:

* **čtení** (`list_dir`, `read_file`, `search_text`) — obsah odejde providerovi,
  jinak nic nezmění. Zapíná se jednou v nastavení.
* **zápis** (`edit_file`, `write_file`) — mění soubor na disku. Zvlášť, protože
  špatně pochopené zadání tu stojí data, ne jen zbytečný request.
* **příkazy** (`run_command`) — spustí program pod uživatelovým účtem. Zapíná se
  u konkrétní zprávy, ne trvale.

Celá vrstva je mrtvá, když aplikace běží v public mode. Nástroj, který čte disk
a spouští programy, je na veřejné adrese vzdálený shell — a to je pravda bez
ohledu na to, jak dobré je přihlašování před ním. `available()` je jediné místo,
kde se to rozhoduje.
"""

import asyncio
import json
import logging
import re
from pathlib import Path

from app import settings_store
from app.config import settings
from app.tools import sandbox

log = logging.getLogger(__name__)

# Kolik nejdéle smí běžet jeden příkaz. `npm run build` v tomhle repu trvá
# kolem dvaceti sekund, `pytest` dvanáct; dvě minuty jsou strop, po kterém je
# pravděpodobnější, že příkaz čeká na vstup, než že ještě počítá.
COMMAND_TIMEOUT = 120

# Kolik řádků vrátí `read_file`, když model neřekne jinak.
DEFAULT_LINES = 400

# Kolik shod nejvíc vrátí `search_text`. Víc už není odpověď, ale výpis.
MAX_MATCHES = 60


def available() -> bool:
    """Smí se nástroje vůbec nabídnout?"""
    return not settings.public_mode and settings_store.get_bool(
        settings_store.LOCAL_TOOLS
    )


def can_write() -> bool:
    return available() and settings_store.get_bool(settings_store.LOCAL_WRITE)


_READ_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": (
                "Vypíše obsah složky na počítači uživatele: názvy, jestli jde "
                "o soubor nebo složku, a velikost. Použij, když potřebuješ "
                "zjistit, co v projektu je, než začneš číst soubory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolutní cesta ke složce.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Přečte textový soubor z počítače uživatele. Vrací řádky "
                "s čísly, takže se na ně dá odkazovat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolutní cesta k souboru."},
                    "start_line": {
                        "type": "integer",
                        "description": "Od kterého řádku číst, od 1. Výchozí 1.",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": f"Kolik řádků vrátit. Výchozí {DEFAULT_LINES}.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": (
                "Najde regulární výraz v souborech pod danou složkou a vrátí "
                "shody i s cestou a číslem řádku. Rychlejší než číst soubory "
                "jeden po druhém, když hledáš, kde je něco definované."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regulární výraz (Python `re`).",
                    },
                    "path": {
                        "type": "string",
                        "description": "Složka, ve které se hledá.",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Filtr názvů, např. *.py. Výchozí všechny textové.",
                    },
                },
                "required": ["pattern", "path"],
            },
        },
    },
]

_EDIT_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Zamění v souboru jeden přesný úsek textu za jiný. Tohle je "
            "standardní způsob, jak se mění existující soubor — `write_file` "
            "si nech na zakládání nových. `old_string` musí sedět znak po "
            "znaku včetně odsazení a musí být v souboru právě jednou; když "
            "není, přiber okolní řádky, ať je jednoznačný."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolutní cesta k souboru."},
                "old_string": {
                    "type": "string",
                    "description": "Text, který se má nahradit. Přesně, včetně odsazení.",
                },
                "new_string": {
                    "type": "string",
                    "description": "Čím se nahradí. Prázdný řetězec úsek smaže.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": (
                        "Nahradit všechny výskyty. Bez toho je víc než jeden "
                        "výskyt chyba, ne tichý přepis prvního."
                    ),
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}

_WRITE_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Zapíše text do souboru na počítači uživatele; existující obsah "
            "přepíše celý. Používej na nové soubory. U existujícího souboru "
            "sáhni po `edit_file` — přepsat tisíciřádkový soubor celý znamená "
            "znovu vypsat i to, co se nemění, a přesně tam se ztrácí kód."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolutní cesta k souboru."},
                "content": {"type": "string", "description": "Celý nový obsah souboru."},
            },
            "required": ["path", "content"],
        },
    },
}

_COMMAND_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": (
            "Spustí jeden program na počítači uživatele a vrátí jeho výstup. "
            "Bez shellu, takže roury (|), řetězení (&&) ani přesměrování (>) "
            "nefungují. Povolené programy: "
            + ", ".join(sorted(sandbox.ALLOWED_COMMANDS))
            + "."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Celý příkaz včetně argumentů, např. `git status --short`.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Složka, ve které se má spustit.",
                },
            },
            "required": ["command", "cwd"],
        },
    },
}


def specs(*, allow_commands: bool) -> list[dict]:
    """Co se v tomhle požadavku nabídne modelu.

    Nabídnout nástroj, na který uživatel nedal svolení, by byl horší návrh, než
    se zdá: model by ho zavolal, dostal odmítnutí a hlásil uživateli, že úkol
    nejde splnit. Co v seznamu není, o tom model neví, a rovnou hledá jinou
    cestu.
    """
    if not available():
        return []
    out = list(_READ_SPECS)
    if can_write():
        # `edit_file` první: pořadí v seznamu je pro model náznak, co je běžný
        # postup. Když viděl `write_file` dřív, sahal po něm i na jednořádkovou
        # opravu a přepisoval celý soubor.
        out.append(_EDIT_SPEC)
        out.append(_WRITE_SPEC)
    if allow_commands:
        out.append(_COMMAND_SPEC)
    return out


def system_message(*, allow_commands: bool) -> dict | None:
    """Kontext k nástrojům, nebo None, když se žádné nenabízejí.

    Bez seznamu povolených kořenů model nemá kde začít. Na „co mám ve složce,
    do které vidíš?" odpovídal, že žádnou složku nevidí — a měl pravdu:
    `list_dir` chce absolutní cestu a nikde se nedozvěděl jedinou, kterou smí
    použít. Kořeny se proto čtou při každém požadavku, ne jednou při importu:
    jsou to uživatelská nastavení a mění se za běhu.
    """
    if not available():
        return None

    allowed = "\n".join(f"- {p}" for p in sandbox.roots())
    parts = [
        "Máš přístup k počítači uživatele (Windows) přes nástroje. Používej je, "
        "když se odpověď dá ověřit místo odhadu — přečti soubor, o kterém je "
        "řeč, než o něm začneš mluvit. Nikdy netvrď, že na soubory nevidíš.",
        "Smíš pracovat jen v těchto složkách a kdekoli pod nimi:\n" + allowed,
        "Když se uživatel ptá na „svoji složku“ nebo „svoje projekty“ a "
        "nejmenuje cestu, myslí tímhle právě ty kořeny výš — vypiš je "
        "(`list_dir`) a pokračuj dovnitř. Cesty piš vždy absolutní; když nevíš, "
        "kde soubor je, najdi ho (`search_text`), nehádej.",
        "Nekomentuj, co se chystáš udělat — zavolej nástroj. Až budeš mít "
        "výsledky, odpověz na původní otázku a opři se o to, cos viděl, ne "
        "o obecné znalosti.",
        # Pracovní postup. Bez něj model na free tieru sáhne po prvním souboru,
        # který ho napadne, přepíše ho podle dojmu a ohlásí hotovo — a teprve
        # uživatel zjistí, že se to nepřeloží. Tyhle tři věty stojí pár desítek
        # tokenů a mění to.
        "Postupuj takhle: (1) **najdi** — než něco změníš, zjisti hledáním "
        "(`search_text`), kde všude to místo je; celý soubor čti jen tehdy, "
        "když opravdu potřebuješ vidět jeho stavbu. (2) **uprav** — jednu věc "
        "po druhé, `edit_file` s úsekem, který jsi právě viděl. (3) **ověř** — "
        "po zásahu se přesvědč, že to platí: přečti změněné místo zpátky, a "
        "když smíš spouštět příkazy, pusť build nebo testy projektu. Teprve "
        "pak piš odpověď.",
        "Nehlas hotovou práci, kterou jsi neověřil. Když se ověření nepovedlo "
        "nebo jsi ho udělat nemohl, napiš to rovnou — půl věty „build jsem "
        "nespouštěl“ je k nezaplacení oproti tomu tvrdit, že je vše v pořádku.",
        "Volání nástrojů je omezené. Neplýtvej jimi: nečti dokola tentýž "
        "soubor, nevypisuj složku, kterou už jsi viděl, a nedávej jeden dotaz "
        "do dvou volání, když se dá položit jednou.",
        "Když nástroj vrátí „Odmítnuto“, je to hranice nastavená uživatelem. "
        "Nezkoušej ji obejít jinou cestou; napiš, co bys potřeboval.",
    ]
    # Obě poznámky mluví jen o tom, co chybí, a hned dodávají, co funguje.
    # První verze říkala u příkazů jen „nesmíš, ať si to uživatel zapne“ — a
    # model to vzal jako podmínku celého úkolu: na dotaz „co mám ve složce“
    # odpověděl, že bez režimu „Počítač + příkazy“ nemůže vypsat ani soubory.
    # Přitom `list_dir` měl připojený a fungoval.
    if not can_write():
        parts.append(
            "Zapisovat do souborů u téhle zprávy nesmíš; čtení funguje "
            "normálně. Změny napiš do odpovědi jako návrh — konkrétně, "
            "s cestou a s tím, co za co zaměnit."
        )
    if not allow_commands:
        parts.append(
            "Spouštět programy u téhle zprávy nesmíš. Výpis složky, čtení "
            "souboru i hledání v textu fungují — na ty příkazy nepotřebuješ "
            "a nikdy kvůli nim o povolení nežádej. Teprve když by úkol bez "
            "spuštění programu nešel dokončit, dodej na konec odpovědi, že by "
            "pomohl režim „Počítač + příkazy“."
        )
    return {"role": "system", "content": "\n\n".join(parts)}


def _list_dir(args: dict) -> str:
    path = sandbox.resolve(str(args.get("path", "")))
    if not path.is_dir():
        raise sandbox.Denied(f"{path} není složka.")
    lines = []
    for entry in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if entry.name.lower() in sandbox.DENIED_DIRS or entry.name.lower() in sandbox.DENIED_NAMES:
            continue
        if entry.is_dir():
            lines.append(f"{entry.name}/")
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            lines.append(f"{entry.name}  ({size} B)")
    if not lines:
        return f"{path} je prázdná."
    return f"{path}\n" + "\n".join(lines)


def _read_file(args: dict) -> str:
    path = sandbox.resolve(str(args.get("path", "")))
    if not path.is_file():
        raise sandbox.Denied(f"{path} není soubor.")
    start = max(1, int(args.get("start_line") or 1))
    count = max(1, int(args.get("max_lines") or DEFAULT_LINES))
    try:
        # `errors="replace"`, ne `strict`: binární soubor se má ohlásit jako
        # nečitelný text, ne shodit nástroj výjimkou uprostřed odpovědi.
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise sandbox.Denied(f"Soubor nejde přečíst: {exc}") from exc
    lines = text.splitlines()
    chunk = lines[start - 1 : start - 1 + count]
    if not chunk:
        return f"{path}: soubor má {len(lines)} řádků, od {start} už nic není."
    numbered = "\n".join(f"{start + i}\t{line}" for i, line in enumerate(chunk))
    tail = ""
    if start - 1 + count < len(lines):
        tail = f"\n\n[…dál pokračuje do řádku {len(lines)}]"
    return sandbox.clip(f"{path}\n{numbered}{tail}")


def _search_text(args: dict) -> str:
    root = sandbox.resolve(str(args.get("path", "")))
    if not root.is_dir():
        root = root.parent
    try:
        needle = re.compile(str(args.get("pattern", "")), re.IGNORECASE)
    except re.error as exc:
        raise sandbox.Denied(f"Neplatný regulární výraz: {exc}") from exc
    pattern = str(args.get("glob") or "*")

    hits: list[str] = []
    for file in sorted(root.rglob(pattern)):
        if len(hits) >= MAX_MATCHES:
            break
        if not file.is_file():
            continue
        lowered = {p.lower() for p in file.parts}
        if lowered & {d.lower() for d in sandbox.DENIED_DIRS}:
            continue
        if file.name.lower() in sandbox.DENIED_NAMES:
            continue
        if file.suffix.lower() in sandbox.DENIED_SUFFIXES:
            continue
        try:
            content = file.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue  # binárka nebo zamčený soubor — hledá se v textu
        for number, line in enumerate(content.splitlines(), start=1):
            if needle.search(line):
                hits.append(f"{file}:{number}: {line.strip()[:200]}")
                if len(hits) >= MAX_MATCHES:
                    break
    if not hits:
        return f"Žádná shoda pro `{args.get('pattern')}` v {root}."
    return sandbox.clip("\n".join(hits))


def _edit_file(args: dict) -> str:
    """Záměna přesného úseku textu — základní způsob, jak model mění soubor.

    Proč vůbec, když `write_file` umí totéž: přepis celého souboru nutí model
    znovu vypsat i řádky, kterých se změna netýká. U dvou set řádků to ještě
    projde, u dvou tisíc ne — model na free tieru dojde k limitu tokenů a
    uřízne konec, nebo cestou nenápadně přepíše, co jen opisoval. Záměna úseku
    posílá jen to, co se mění, takže cena opravy neroste s velikostí souboru.

    Jednoznačnost se vynucuje schválně. Kdyby se tiše nahradil první výskyt,
    model by neměl jak poznat, že trefil jiné místo, než mířil — a soubor by
    zůstal opravený na špatném řádku. Zpráva o víc výskytech je naopak něco,
    s čím umí pracovat: přibere kontext a zavolá znovu.
    """
    path = sandbox.resolve(str(args.get("path", "")))
    old = args.get("old_string")
    new = args.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str):
        raise sandbox.Denied("Chybí `old_string` nebo `new_string`.")
    if not old:
        raise sandbox.Denied(
            "`old_string` je prázdný. Na založení souboru je `write_file`."
        )
    if old == new:
        raise sandbox.Denied("`old_string` a `new_string` jsou stejné, není co měnit.")
    if not path.is_file():
        raise sandbox.Denied(f"{path} není soubor.")

    try:
        # `newline=""` vypíná univerzální překlad konců řádků. Bez něj by se
        # CRLF soubor načetl jako LF, zapsal jako LF — a jediná oprava jednoho
        # řádku by tiše přepsala konce řádků v celém souboru. V diffu to pak
        # vypadá, že se změnilo všechno.
        with path.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise sandbox.Denied(f"Soubor nejde přečíst jako text: {exc}") from exc

    needle, replacement = old, new
    if needle not in text and "\r\n" in text:
        # Model cituje soubor tak, jak ho dostal z `read_file` — a tam se řádky
        # rozpadly přes `splitlines()`, takže o CR na konci nemá jak vědět.
        # Bez tohohle by záměna selhala na každém souboru s CRLF, což je na
        # Windows většina.
        needle = needle.replace("\n", "\r\n")
        replacement = replacement.replace("\n", "\r\n")

    found = text.count(needle)
    if found == 0:
        raise sandbox.Denied(
            f"`old_string` se v {path} nenašel. Přečti si soubor znovu "
            "(`read_file`) a zkopíruj úsek přesně, včetně odsazení."
        )
    if found > 1 and not bool(args.get("replace_all")):
        raise sandbox.Denied(
            f"`old_string` je v {path} {found}×. Přiber okolní řádky, ať je "
            "jednoznačný, nebo nastav `replace_all`."
        )

    updated = text.replace(needle, replacement)
    try:
        # `newline=""`, ať se konce řádků nepřekládají podruhé: needle už je
        # v té podobě, v jaké soubor na disku je.
        path.write_text(updated, encoding="utf-8", newline="")
    except OSError as exc:
        raise sandbox.Denied(f"Zápis selhal: {exc}") from exc

    # Číslo řádku, ne jen „hotovo": model podle něj pozná, jestli trefil místo,
    # které měl na mysli, a v UI je vidět, kde se soubor změnil.
    line = text[: text.index(needle)].count("\n") + 1
    where = f"{found}× (první na řádku {line})" if found > 1 else f"na řádku {line}"
    return f"Soubor {path} upraven {where}."


def _write_file(args: dict) -> str:
    path = sandbox.resolve(str(args.get("path", "")), must_exist=False)
    content = args.get("content")
    if not isinstance(content, str):
        raise sandbox.Denied("Chybí obsah souboru.")
    if not path.parent.exists():
        raise sandbox.Denied(f"Složka {path.parent} neexistuje.")
    existed = path.exists()
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise sandbox.Denied(f"Zápis selhal: {exc}") from exc
    verb = "přepsán" if existed else "vytvořen"
    return f"Soubor {path} {verb}, {len(content)} znaků."


async def _run_command(args: dict) -> str:
    argv = sandbox.check_command(str(args.get("command", "")))
    cwd = sandbox.resolve(str(args.get("cwd", "")))
    if not cwd.is_dir():
        cwd = cwd.parent

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            # Sloučené se stdoutem: model potřebuje vidět chybu ve stejném
            # pořadí, v jakém vznikla. Dva oddělené proudy by u selhaného buildu
            # znamenaly výpis, ve kterém chyba visí bez kontextu.
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        raise sandbox.Denied(f"Program `{argv[0]}` na tomhle počítači není.") from None
    except OSError as exc:
        raise sandbox.Denied(f"Příkaz nešel spustit: {exc}") from exc

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=COMMAND_TIMEOUT)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise sandbox.Denied(
            f"Příkaz běžel přes {COMMAND_TIMEOUT} s a byl ukončen. "
            "Nejspíš čeká na vstup — přidej neinteraktivní přepínač."
        ) from None

    text = stdout.decode("utf-8", errors="replace").strip()
    head = f"$ {' '.join(argv)}  (v {cwd})\nnávratový kód {process.returncode}"
    return sandbox.clip(f"{head}\n\n{text}" if text else f"{head}\n\n(bez výstupu)")


_HANDLERS = {
    "list_dir": _list_dir,
    "read_file": _read_file,
    "search_text": _search_text,
    "edit_file": _edit_file,
    "write_file": _write_file,
}

# Nástroje, které sahají na disk. Kontrolují se v `run()` proti `can_write()`,
# ne jen v `specs()` — model si jméno funkce může vymyslet a některé vymyslí.
_WRITING_TOOLS = frozenset({"edit_file", "write_file"})


async def run(name: str, arguments: str | dict, *, allow_commands: bool) -> str:
    """Provede jedno volání nástroje a vrátí text, který uvidí model.

    Nikdy nevyhazuje výjimku. Chyba nástroje je pro model informace jako každá
    jiná — dostane ji jako výsledek a může zkusit něco jiného; výjimka by místo
    toho zabila celou odpověď kvůli překlepu v cestě.

    Oprávnění se kontrolují **tady**, ne jen v `specs()`. Model si jméno funkce
    může vymyslet a některé ho vymyslí — zvlášť po pár kolech, kdy `run_command`
    viděl v historii konverzace, ale v aktuálním požadavku už nabídnutý není.
    """
    if not available():
        return "Práce se soubory je vypnutá v nastavení aplikace."

    args = arguments
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            return f"Argumenty pro `{name}` nejsou platný JSON: {arguments!r}"
    if not isinstance(args, dict):
        return f"Argumenty pro `{name}` musí být objekt."

    try:
        if name == "run_command":
            if not allow_commands:
                return (
                    "Spouštění příkazů není u téhle zprávy povolené. Uživatel ho "
                    "zapíná přepínačem u pole pro psaní."
                )
            return await _run_command(args)
        if name in _WRITING_TOOLS and not can_write():
            return "Zápis do souborů je vypnutý v nastavení aplikace."
        handler = _HANDLERS.get(name)
        if handler is None:
            return f"Nástroj `{name}` neexistuje."
        return await asyncio.to_thread(handler, args)
    except sandbox.Denied as exc:
        return f"Odmítnuto: {exc}"
    except Exception as exc:  # noqa: BLE001 - výsledek nástroje, ne pád odpovědi
        log.warning("nástroj %s selhal: %s: %s", name, type(exc).__name__, exc)
        return f"Nástroj `{name}` selhal: {exc}"


def summarize(name: str, arguments: str | dict) -> str:
    """Jedna řádka pro UI: co se právě děje, bez JSONu.

    Uživatel má vidět „čte backend/app/main.py“, ne `{"path": "…"}`. Když se
    argumenty nepodaří přečíst, zůstane aspoň jméno nástroje — tenhle popisek
    nikdy nesmí být důvod, proč se odpověď rozbije.
    """
    args = arguments
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            args = {}
    if not isinstance(args, dict):
        args = {}

    if name == "run_command":
        return str(args.get("command") or "příkaz")
    if name == "search_text":
        return f"hledá „{args.get('pattern', '')}“ v {Path(str(args.get('path', ''))).name}"
    path = args.get("path")
    if path:
        return str(path)
    return name
