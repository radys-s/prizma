"""Read/write access to the user-flippable settings in `AppSetting`.

Opens its own session on purpose. Most callers here are deep inside the
routing stack (`routing/research.py` asking whether it may read pages), where
there is no request-scoped session to inject and threading one down four
layers would be worse than one short-lived read.

Values are cached in-process and the cache is dropped on every write, because
the write and the read always happen in the same process — this is a
single-user local app, and there is no second worker to invalidate.
"""

from sqlmodel import Session, select

from app.db import engine
from app.models.settings import AppSetting

# Whether deep research may fetch the pages it found through Jina Reader.
# Default on: it is the single biggest quality difference against a free
# research tool. Off means deep research falls back to snippets — see the
# disclosure note in `routing/websearch.py`.
READ_PAGES = "research.read_pages"

# A standing instruction prepended to every conversation as the first system
# turn — tone, language, format, whatever the user always wants and is tired of
# repeating. Empty by default, so a user who never opens the dialog sees no
# change at all. Free text rather than a toggle, which is why `get_text` exists.
CUSTOM_INSTRUCTIONS = "chat.custom_instructions"

# How long that text may be. Not a storage limit — it is prepended to every
# single request, so an essay here would quietly eat the context budget that
# `routing/compact.py` is working to protect.
CUSTOM_INSTRUCTIONS_MAX = 2000

# Smí model sahat na disk tohohle počítače (čtení: výpis složky, čtení souboru,
# hledání v textu). Výchozí vypnuto, a to i když je aplikace jen na localhostu:
# každý přečtený soubor odejde providerovi, a to má být rozhodnutí, ne výchozí
# stav. Co přesně smí, řeší `tools/sandbox.py`.
LOCAL_TOOLS = "tools.local"

# Smí i zapisovat. Podřízené předchozímu — bez čtení nemá zápis smysl a
# `tools/registry.py` ho v tom případě ani nenabídne.
LOCAL_WRITE = "tools.write"

# Složky, ve kterých se agent smí pohybovat; jedna na řádek. Prázdné znamená
# výchozí kořen z `tools/sandbox.py`.
TOOLS_ROOTS = "tools.roots"
TOOLS_ROOTS_MAX = 1000

# Only booleans belong here: `all_bools()` iterates it, and a text default
# would come back as `False`.
DEFAULTS: dict[str, str] = {READ_PAGES: "1", LOCAL_TOOLS: "0", LOCAL_WRITE: "0"}

_cache: dict[str, str] = {}


def get(key: str) -> str:
    if key in _cache:
        return _cache[key]
    with Session(engine) as session:
        row = session.exec(select(AppSetting).where(AppSetting.key == key)).first()
    value = row.value if row is not None else DEFAULTS.get(key, "")
    _cache[key] = value
    return value


def get_bool(key: str) -> bool:
    return get(key) == "1"


def set_bool(key: str, value: bool) -> None:
    with Session(engine) as session:
        row = session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key)
        row.value = "1" if value else "0"
        session.add(row)
        session.commit()
    _cache.pop(key, None)


def get_text(key: str, limit: int | None = None) -> str:
    value = get(key)
    return value[:limit] if limit else value


def set_text(key: str, value: str, limit: int | None = None) -> None:
    """Stores free text. An empty string clears the setting rather than
    storing a blank row — "" and "unset" mean the same thing to every reader."""
    text = (value or "").strip()
    if limit:
        text = text[:limit]
    with Session(engine) as session:
        row = session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key)
        row.value = text
        session.add(row)
        session.commit()
    _cache.pop(key, None)


def all_bools() -> dict[str, bool]:
    """Every known toggle, defaults included — what the settings dialog reads."""
    return {key: get_bool(key) for key in DEFAULTS}
