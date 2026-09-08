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
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"

# 2 MB je asi měsíc běžného provozu. Rotace na jeden soubor dozadu stačí —
# tohle je log pro „proč to dneska nenaběhlo", ne archiv.
MAX_LOG_BYTES = 2 * 1024 * 1024


def _log_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    directory = base / "Prizma"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "server.log"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prizma jako služba na pozadí")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
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
