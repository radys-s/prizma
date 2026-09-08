"""Names a conversation from what it turned out to be about.

The old title was `req.message.strip()[:60]` — the first sixty characters of
the first thing the user typed. For "ahoj" that is a sidebar full of "ahoj",
and for a pasted stack trace it is a sidebar entry made of Python.

So: after the first answer is written, one cheap non-streaming call reads the
exchange and names it. Deliberately *after*, not before — the answer is the
half that reveals what the conversation actually was, and doing it before would
also put a model call in front of the first token of every new chat.

**Never raises, and never blocks the stream.** `chat.py` fires this into the
background once the answer is saved. If it fails the truncated title stays,
which is exactly what the app did before this module existed.
"""

import logging
import re

import litellm
from sqlmodel import Session

from app.db import engine
from app.models.conversation import Conversation
from app.providers import keystore, model_health
from app.routing import selector

log = logging.getLogger(__name__)

MAX_CHARS = 48
# Prompt žádá nejvýš šest slov. Deset je rezerva pro model, který se netrefí —
# a zároveň hranice, za kterou už řádek není název, ale věta.
MAX_WORDS = 10
_TIMEOUT = 15
_MAX_TOKENS = 40
_INPUT_CHARS = 1500

_SYSTEM = (
    "Pojmenuj konverzaci. Odpověz jedním krátkým názvem, nic jiného.\n\n"
    "Pravidla: nejvýš 6 slov, bez uvozovek, bez tečky na konci, bez uvozovacích "
    "frází jako „Konverzace o“. Ve stejném jazyce, jakým píše uživatel. "
    "Popiš téma, ne to, že jde o dotaz."
)

# Models like to answer a naming request with `"Název: Něco"` or `**Něco**`.
_STRIP = re.compile(r'^["\'`*_\s]+|["\'`*_\s.]+$')
_PREFIX = re.compile(r"^(název|nazev|title|téma|tema)\s*[:\-–]\s*", re.IGNORECASE)

# Reasoning modely nahlas přemýšlejí, než odpoví. Některé to zabalí do
# <think>…</think>, jiné prostě začnou mluvit. Neuzavřený blok je taky případ,
# který se stane — když dojdou tokeny (`_MAX_TOKENS` je 40), uzavírací značka
# nedorazí a zbytek odpovědi je jen přemýšlení.
_THINK = re.compile(r"<think>.*?(?:</think>|$)", re.IGNORECASE | re.DOTALL)


def _looks_like_a_title(line: str) -> bool:
    """Jméno, ne věta o pojmenovávání."""
    if not line or line.endswith(":"):
        return False
    return len(line.split()) <= MAX_WORDS and len(line) <= MAX_CHARS * 2


def _shorten(line: str) -> str:
    """Ořez na `MAX_CHARS`, ale ne uprostřed slova."""
    if len(line) <= MAX_CHARS:
        return line
    cut = line[:MAX_CHARS].rstrip()
    space = cut.rfind(" ")
    return (cut[:space] if space > MAX_CHARS // 2 else cut).rstrip(" ,;:-–")


def _clean(raw: str) -> str:
    """Název schovaný v odpovědi modelu, nebo "" když tam žádný není.

    Dřív se bralo natvrdo první slovo první řádky. U reasoning modelu je první
    řádka jeho přemýšlení, takže v postranním panelu skončilo vlákno pojmenované
    „We need to respond with a short title, m…“ — useknutá věta o tom, že se má
    vymyslet název. Ořez na 48 znaků to jen schoval; text se tím nestal názvem.

    Proto se hledá *první řádka, která vypadá jako název*: přemýšlení se přeskočí
    (je moc dlouhé), a když nevypadá jako název ani jedna, vrátí se prázdný
    řetězec. To `suggest` pošle na dalšího providera a v nejhorším případě zůstane
    původní název podle otázky — což je pořád lepší než odstavec v seznamu.
    """
    text = _THINK.sub("", raw or "")
    for line in text.splitlines():
        candidate = _STRIP.sub("", line.strip())
        candidate = _PREFIX.sub("", candidate).strip()
        candidate = _STRIP.sub("", candidate)
        if _looks_like_a_title(candidate):
            return _shorten(candidate)
    return ""


async def suggest(question: str, answer: str) -> str:
    """A title, or "" if no provider could produce one."""
    exchange = f"Uživatel: {question[:_INPUT_CHARS]}\n\nAsistent: {answer[:_INPUT_CHARS]}"
    for provider in selector.planner_candidates():
        model = next(iter(model_health.usable_models(provider.models)), None)
        if model is None:
            continue
        try:
            response = await litellm.acompletion(
                model=model,
                **keystore.endpoint(provider),
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": exchange},
                ],
                stream=False,
                temperature=0.2,
                max_tokens=_MAX_TOKENS,
                timeout=_TIMEOUT,
            )
            title = _clean(response.choices[0].message.content or "")
            if title:
                return title
        except Exception as exc:  # noqa: BLE001 - a name is never worth an error
            log.info("title suggestion failed on %s: %s", provider.id, exc)
            continue
    return ""


async def autoname(conversation_id: int, question: str, answer: str) -> None:
    """Names the conversation in place, if it still has its placeholder title.

    Re-reads the row in its own session because this runs after the request that
    created it has finished. Checks `summary`-style staleness the cheap way: if
    the user has renamed the thread in the meantime, leave their name alone.
    """
    if not question.strip() or not answer.strip():
        return
    title = await suggest(question, answer)
    if not title:
        return
    # `suggest` už nikdy nevyhodí výjimku, ale zápis do databáze ano — zamčená
    # SQLite, spadlé spojení do Postgresu. Tenhle korutin běží jako
    # fire-and-forget task z `chat.py`, takže by se výjimka neobjevila v žádném
    # requestu: asyncio ji vypíše až při úklidu tasku, jako „Task exception was
    # never retrieved“, klidně o minuty později a bez souvislosti s odpovědí.
    # Modul slibuje v docstringu, že nikdy nevyhodí výjimku — tady to platí.
    try:
        with Session(engine) as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.title != _placeholder(question):
                return  # renamed by hand while we were thinking — theirs wins
            conversation.title = title
            session.add(conversation)
            session.commit()
    except Exception as exc:  # noqa: BLE001 - a name is never worth an error
        log.warning("uložení názvu konverzace %s selhalo: %s", conversation_id, exc)


def _placeholder(question: str) -> str:
    """The title `chat.py` writes up front, so `autoname` can tell whether the
    user has since changed it. One definition, used by both."""
    return question.strip()[:60] or "Nová konverzace"
