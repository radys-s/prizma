"""Prizma na pozadí, bez okna a s logem.

Naplánovaná úloha spouští `pythonw.exe`, protože `python.exe` by při každém
přihlášení blikl konzolí. Jenže pythonw nemá stdout ani stderr — obojí je
`None` — a uvicorn si při startu staví `StreamHandler(sys.stderr)`. První
zalogovaný řádek proto skončí na `AttributeError: 'NoneType' object has no
attribute 'write'`, proces zhasne a Správce úloh k tomu řekne jen „výsledek 1".
Přesně tak to tady jednou spadlo.

Řešení je tenhle soubor: nejdřív se obě roury nasměrují do souboru, teprve pak
se importuje uvicorn. Vedlejší efekt je ten důležitější — když se appka po
přihlášení nespustí, je se kam podívat:

    %LOCALAPPDATA%\\Prizma\\server.log

Spouští se přímo, ne přes `uvicorn.main`, aby se nedalo omylem předat
`--reload`: reloader forkuje potomka, který by tenhle přesměrovaný stdio
nezdědil a byl by zpátky u prvního odstavce.
"""

import argparse
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"

# 2 MB je asi měsíc běžného provozu. Rotace na jeden soubor dozadu stačí —
# tohle je log pro „proč to dneska nenaběhlo", ne archiv.
MAX_LOG_BYTES = 2 * 1024 * 1024

# Kolik nejdéle čekat na server, než se otevře okno. Studený start s Nortonem
# na krku a s importem litellm trvá několik sekund; minuta je hranice, za
# kterou už je jasné, že server nenaběhl a okno by ukázalo chybovou stránku.
OPEN_TIMEOUT = 60.0
OPEN_POLL = 0.5


def _edge() -> str | None:
    """Cesta k Edge, nebo None. Obě větve ProgramFiles, 32bit napřed —
    Edge se na Windows 11 instaluje do `Program Files (x86)`."""
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(var)
        if not root:
            continue
        exe = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if exe.exists():
            return str(exe)
    return None


def _open_when_ready(port: int) -> None:
    """Otevře okno Prizmy, jakmile server opravdu odpovídá.

    Úloha ve Správci úloh umí spustit server, ale okno si musí někdo otevřít —
    bez toho po přihlášení běží appka, kterou není vidět. Zástupce na ploše to
    řešil jedním kliknutím, tohle to řeší nulou.

    Čeká se na `/api/health`, ne na pevné zpoždění. Než se naimportuje litellm
    a postaví routovací tabulka, uběhne podle nálady disku několik sekund a
    okno otevřené dřív ukáže „nelze se připojit" — což vypadá jako rozbitá
    appka, i když jde jen o pár sekund netrpělivosti.
    """
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + OPEN_TIMEOUT
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=2) as res:
                if res.status == 200:
                    break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(OPEN_POLL)
    else:
        print(f"okno se neotevřelo: server neodpověděl do {OPEN_TIMEOUT:.0f} s")
        return

    edge = _edge()
    try:
        if edge:
            # --app dá okno bez adresního řádku a panelů: vypadá to jako
            # aplikace, ne jako další tab v prohlížeči.
            subprocess.Popen([edge, f"--app={url}"], close_fds=True)
        else:
            os.startfile(url)  # noqa: S606 - výchozí prohlížeč, když Edge není
        print("okno appky otevřeno")
    except OSError as exc:
        print(f"okno se neotevřelo: {exc}")


def _log_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    directory = base / "Prizma"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "server.log"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prizma jako služba na pozadí")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--open", action="store_true", help="po naběhnutí otevřít okno appky"
    )
    args = parser.parse_args()

    log = _log_path()
    if log.exists() and log.stat().st_size > MAX_LOG_BYTES:
        log.replace(log.with_suffix(".log.old"))

    stream = log.open("a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    print(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} — start na {args.host}:{args.port} ===")

    # Až teď, s přesměrovaným stdio: uvicorn si logging staví při importu
    # a konfiguruje ho podle toho, co v `sys.stderr` najde.
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    import uvicorn

    # Vlákno, ne kód za `uvicorn.run`: ten se vrátí až při vypnutí serveru.
    # Daemon, aby neblokoval ukončení, kdyby server nikdy nenaběhl.
    if args.open:
        threading.Thread(target=_open_when_ready, args=(args.port,), daemon=True).start()

    try:
        uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")
    except OSError as exc:
        # Nejčastěji „port je obsazený" — appka už běží, což není chyba,
        # jen důvod tenhle proces ukončit.
        print(f"server nenaběhl: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
