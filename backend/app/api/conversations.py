import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app.db import get_session
from app.models.conversation import Conversation, Message

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

TITLE_MAX_CHARS = 120

# Full-text search over message bodies. Capped because the sidebar shows a
# list, not a report — thirty hits is already more than anyone scrolls.
SEARCH_LIMIT = 30
SEARCH_MIN_CHARS = 2
SNIPPET_RADIUS = 60


# JSON *text* columns on `message`. Dumping one straight out sends the client a
# string where its type says list, and the strip that renders it then shows
# nothing — or worse, one card per character. Decoding belongs here rather than
# in the browser: the storage format is a backend detail.
_JSON_LIST_COLUMNS = ("sources", "parts")


def _serialize(message: Message) -> dict:
    """Row -> what the client actually expects.

    A row whose JSON is unreadable degrades to no sources (or no parts) at all —
    an answer without its receipts is worse than one with them, but it is still
    an answer, and a 500 on the whole conversation is not.
    """
    data = message.model_dump()
    for column in _JSON_LIST_COLUMNS:
        raw = data.get(column)
        if not raw:
            data[column] = None
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            data[column] = None
            continue
        data[column] = parsed if isinstance(parsed, list) else None
    return data


def _conversation(conversation: Conversation) -> dict:
    """What the sidebar needs, and nothing else.

    `summary` is deliberately not here. It is auto-compact's working state — it
    can run to several hundred words, it is written by the weakest model in the
    chain, and putting it in the payload of a list endpoint would mean shipping
    all of them to render a list of titles.
    """
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at,
        # Fallback na `created_at` kvůli řádkům, které migrace teprve doplní —
        # panel podle toho řadí a nesmí mu přijít `null`.
        "updated_at": conversation.updated_at or conversation.created_at,
        "pinned": bool(conversation.pinned),
    }


class RenamePayload(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX_CHARS)


class PinPayload(BaseModel):
    pinned: bool


def _conversation_or_404(session: Session, conversation_id: int) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Konverzace nenalezena")
    return conversation


@router.get("")
def list_conversations(session: Session = Depends(get_session)):
    conversations = session.exec(
        # Podle poslední aktivity, ne podle založení: vlákno, ve kterém se
        # právě píše, patří nahoru, i kdyby vzniklo před měsícem.
        select(Conversation).order_by(
            col(Conversation.pinned).desc(),
            col(Conversation.updated_at).desc(),
            col(Conversation.created_at).desc(),
        )
    ).all()
    return [_conversation(c) for c in conversations]


# `%` and `_` are wildcards to SQL LIKE, so an unescaped needle makes the
# search quietly wrong in both directions: `100%` matches nothing (the trailing
# `%` swallows the rest of the message but the literal `%` is never required),
# and a query of `%%` matches every message in the database. `_snippet` below
# looks for the needle as a plain substring, so the two halves would also
# disagree about what was found — the row comes back with a snippet that does
# not contain the search term.
_LIKE_ESCAPE = "\\"


def _like_pattern(needle: str) -> str:
    """A LIKE pattern that matches `needle` literally, wildcards included."""
    escaped = needle
    for char in (_LIKE_ESCAPE, "%", "_"):
        escaped = escaped.replace(char, _LIKE_ESCAPE + char)
    return f"%{escaped}%"


def _snippet(content: str, needle: str) -> str:
    """±SNIPPET_RADIUS characters around the first match, with ellipses.

    Case folding is done here rather than in SQL: SQLite's LIKE only folds
    ASCII, so `Čas` and `čas` are two different strings to it. The query below
    is the coarse filter; this is what actually has to be right, because it is
    what the user reads.
    """
    haystack = content.casefold()
    at = haystack.find(needle.casefold())
    if at < 0:
        return content[: SNIPPET_RADIUS * 2].strip()
    start = max(0, at - SNIPPET_RADIUS)
    end = min(len(content), at + len(needle) + SNIPPET_RADIUS)
    return (
        ("…" if start > 0 else "")
        + content[start:end].strip()
        + ("…" if end < len(content) else "")
    )


@router.get("/search")
def search_conversations(
    q: str = Query(default="", max_length=200),
    session: Session = Depends(get_session),
):
    """Conversations whose *messages* contain `q`.

    The sidebar already filters titles as you type, which finds nothing in a
    thread called "Nová konverzace". This is the other half: one hit per
    conversation, newest first, with the matching text around it.
    """
    needle = q.strip()
    if len(needle) < SEARCH_MIN_CHARS:
        return {"query": needle, "results": []}

    rows = session.exec(
        select(Message)
        .where(col(Message.content).ilike(_like_pattern(needle), escape=_LIKE_ESCAPE))
        .order_by(col(Message.created_at).desc(), col(Message.id).desc())
        .limit(SEARCH_LIMIT * 8)  # room to collapse several hits per thread
    ).all()

    titles = {
        c.id: c.title
        for c in session.exec(
            select(Conversation).where(
                col(Conversation.id).in_({r.conversation_id for r in rows})
            )
        ).all()
    }

    results: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        if row.conversation_id in seen or row.conversation_id not in titles:
            continue
        seen.add(row.conversation_id)
        results.append(
            {
                "conversation_id": row.conversation_id,
                "title": titles[row.conversation_id],
                "role": row.role,
                "snippet": _snippet(row.content, needle),
            }
        )
        if len(results) >= SEARCH_LIMIT:
            break
    return {"query": needle, "results": results}


@router.get("/{conversation_id}")
def get_conversation(conversation_id: int, session: Session = Depends(get_session)):
    conversation = _conversation_or_404(session, conversation_id)
    messages = session.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        # `id` je tu jako rozhodčí, ne pro parádu: dvě zprávy zapsané ve stejné
        # mikrosekundě (odmítnutí, které přijde hned po otázce) měly bez něj
        # nedefinované pořadí, takže se konverzace po reloadu mohla vykreslit
        # s odpovědí nad dotazem. `chat.py` i fulltext hledání ho už měly.
        .order_by(Message.created_at, Message.id)
    ).all()
    return {
        "conversation": _conversation(conversation),
        "messages": [_serialize(m) for m in messages],
    }


@router.patch("/{conversation_id}")
def rename_conversation(
    conversation_id: int,
    payload: RenamePayload,
    session: Session = Depends(get_session),
):
    conversation = _conversation_or_404(session, conversation_id)
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Prázdný název")
    conversation.title = title[:TITLE_MAX_CHARS]
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return _conversation(conversation)


@router.post("/{conversation_id}/pin")
def pin_conversation(
    conversation_id: int,
    payload: PinPayload,
    session: Session = Depends(get_session),
):
    conversation = _conversation_or_404(session, conversation_id)
    conversation.pinned = payload.pinned
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return _conversation(conversation)


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: int, session: Session = Depends(get_session)):
    conversation = _conversation_or_404(session, conversation_id)
    messages = session.exec(
        select(Message).where(Message.conversation_id == conversation_id)
    ).all()
    for m in messages:
        session.delete(m)
    session.delete(conversation)
    session.commit()
    return {"ok": True}
