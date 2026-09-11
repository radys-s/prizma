"""Úprava zprávy nesmí smazat vlákno dřív, než je za co ho vyměnit.

Regenerate se k tomu propsal už dřív: nic se nezahodí, dokud neexistuje nová
odpověď, takže běh, kde jsou všichni provideři vyčerpaní, nechá konverzaci
přesně tak, jak byla. Úprava zprávy to dělala obráceně — smazala a commitla
všechno za upravenou otázkou hned na začátku. Když se pak nikdo nedovolal,
uživatel přišel o celý zbytek vlákna a nedostal za něj nic.

Testy níž jdou po tom rozdílu ze dvou stran: co `chat_edit` udělá s databází,
než začne streamovat, a co s ní udělá stream, který nic nenapsal.
"""

import asyncio
import json

import pytest
from sqlmodel import Session

from app.api import chat as chat_api
from app.db import engine
from app.models.conversation import Conversation, Message
from app.routing import effort as effort_tiers
from app.routing import plan as planning


@pytest.fixture(autouse=True)
def schema():
    """Tabulky vytváří jinak až lifespan aplikace; tyhle testy klienta nemají."""
    from app.db import init_db

    init_db()


@pytest.fixture()
def thread():
    """Vlákno o čtyřech zprávách: otázka, odpověď, otázka, odpověď."""
    with Session(engine) as session:
        conversation = Conversation(title="Test")
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        rows = []
        for role, text in (
            ("user", "první otázka"),
            ("assistant", "první odpověď"),
            ("user", "druhá otázka"),
            ("assistant", "druhá odpověď"),
        ):
            row = Message(
                conversation_id=conversation.id, role=role, content=text
            )
            session.add(row)
            rows.append(row)
        session.commit()
        for row in rows:
            session.refresh(row)
        yield conversation.id, [row.id for row in rows]


def _remaining(conversation_id: int) -> list[str]:
    with Session(engine) as session:
        return [
            m.content
            for m in chat_api._rows(session, conversation_id)
        ]


def test_edit_does_not_delete_the_tail_up_front(thread, monkeypatch):
    """Smazání se jen **naplánuje** — přes `replaces`, jako u regenerate."""
    conversation_id, ids = thread
    captured: dict = {}

    def fake_stream(*args, **kwargs):
        captured.update(kwargs)
        return "stream"

    monkeypatch.setattr(chat_api, "_stream_response", fake_stream)
    monkeypatch.setattr(chat_api.selector, "require_minimum", lambda: None)

    async def fake_plan(*args, **kwargs):
        return planning.Plan(
            capability="general",
            complexity="simple",
            goal="",
            directives="",
            source="heuristika",
            planner_model=None,
            needs_search=False,
        )

    monkeypatch.setattr(chat_api.planning, "make_plan", fake_plan)

    request = chat_api.EditRequest(
        conversation_id=conversation_id,
        message_id=ids[0],
        content="první otázka jinak",
    )
    with Session(engine) as session:
        asyncio.run(chat_api.chat_edit(_FakeRequest(), request, session))

    # Upravená otázka se zapsala hned — je to text, který se posílá modelu.
    remaining = _remaining(conversation_id)
    assert remaining[0] == "první otázka jinak"
    # Zbytek vlákna je pořád na svém místě.
    assert remaining[1:] == ["první odpověď", "druhá otázka", "druhá odpověď"]
    # …a odsouzený je, jen dokud nepřijde odpověď, která ho nahradí.
    assert captured["replaces"] == tuple(ids[1:])


class _FakeRequest:
    """Tolik z `Request`, kolik potřebuje `_limit_chat`."""

    client = type("C", (), {"host": "127.0.0.1"})()


def test_a_run_that_answers_nothing_keeps_the_replaced_messages(thread, monkeypatch):
    """Stream bez jediného tokenu nesmí sáhnout na `replaces`."""
    conversation_id, ids = thread
    _drive_stream(monkeypatch, conversation_id, replaces=tuple(ids[1:]), text="")
    assert _remaining(conversation_id) == [
        "první otázka",
        "první odpověď",
        "druhá otázka",
        "druhá odpověď",
    ]


def test_a_run_that_answers_replaces_them(thread, monkeypatch):
    conversation_id, ids = thread
    _drive_stream(monkeypatch, conversation_id, replaces=tuple(ids[1:]), text="nová")
    assert _remaining(conversation_id) == ["první otázka", "nová"]


def _drive_stream(monkeypatch, conversation_id: int, *, replaces, text: str) -> None:
    """Projede `_stream_response` s vypnutým hledáním, rozpadem i providery."""

    async def no_research(*args, **kwargs):
        return
        yield  # pragma: no cover - dělá z funkce generátor

    async def no_split(*args, **kwargs):
        return
        yield  # pragma: no cover

    async def fake_chat(*args, **kwargs):
        if text:
            yield _Event("delta", content=text)
            yield _Event("done")

    monkeypatch.setattr(chat_api.researching, "run", no_research)
    monkeypatch.setattr(chat_api.decomposing, "run", no_split)
    monkeypatch.setattr(chat_api, "stream_chat", fake_chat)

    response = chat_api._stream_response(
        conversation_id,
        [{"role": "user", "content": "otázka"}],
        planning.Plan(
            capability="general",
            complexity="simple",
            goal="",
            directives="",
            source="heuristika",
            planner_model=None,
            needs_search=False,
        ),
        effort_tiers.resolve("light"),
        None,
        replaces=replaces,
    )

    async def drain():
        async for _ in response.body_iterator:
            pass

    asyncio.run(drain())


class _Event:
    """Tolik z `StreamEvent`, kolik `_stream_response` čte."""

    def __init__(self, type_: str, *, content: str = "") -> None:
        self.type = type_
        self.content = content
        self.provider_id = "test"
        self.model_name = "test/model"
        self.note = None
        self.revised = False
        self.tokens = 0
        self.tool = None
        self.tool_ok = True
        self.retry_after = None


def test_the_stream_shape_is_still_sse(thread, monkeypatch):
    """Pojistka pod testy výš: kdyby se generátor rozbil, nemají co ověřovat."""
    conversation_id, _ = thread

    async def no_research(*args, **kwargs):
        return
        yield  # pragma: no cover

    async def fake_chat(*args, **kwargs):
        yield _Event("delta", content="ahoj")

    monkeypatch.setattr(chat_api.researching, "run", no_research)
    monkeypatch.setattr(chat_api.decomposing, "run", no_research)
    monkeypatch.setattr(chat_api, "stream_chat", fake_chat)

    response = chat_api._stream_response(
        conversation_id,
        [{"role": "user", "content": "otázka"}],
        planning.Plan(
            capability="general",
            complexity="simple",
            goal="",
            directives="",
            source="heuristika",
            planner_model=None,
            needs_search=False,
        ),
        effort_tiers.resolve("light"),
        None,
    )

    async def collect():
        return [chunk async for chunk in response.body_iterator]

    events = asyncio.run(collect())
    assert any(e.get("event") == "start" for e in events)
    first = next(e for e in events if e.get("event") == "start")
    assert json.loads(first["data"])["conversation_id"] == conversation_id
