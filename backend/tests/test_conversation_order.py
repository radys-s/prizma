"""Panel řadí vlákna podle poslední aktivity, ne podle data založení.

Dokud se řadilo podle `created_at`, konverzace založená před třemi týdny a
používaná před pěti minutami seděla pod „Starší“ — a uživatel ji hledal mezi
padesáti mrtvými vlákny. Testy níž jdou po obou půlkách toho přepnutí: že
`updated_at` skutečně rozhoduje o pořadí, a že řádky, které vznikly dřív, než
sloupec existoval, dostanou v migraci rozumnou hodnotu místo `NULL`.

Endpoint se volá přímo se session místo přes `TestClient`: v public módu je za
přihlášením a tenhle test je o řazení, ne o autentizaci.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.api.conversations import list_conversations
from app.db import engine
from app.models.conversation import Conversation, Message


@pytest.fixture(autouse=True)
def schema():
    """Tabulky vytváří jinak až lifespan aplikace; tyhle testy klienta nemají."""
    from app.db import init_db

    init_db()


def _utc(days_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _wipe(session: Session, conversation_id: int) -> None:
    """Zprávy napřed a commit mezi tím — jinak to skousne cizí klíč."""
    for row in session.exec(
        select(Message).where(Message.conversation_id == conversation_id)
    ).all():
        session.delete(row)
    session.commit()
    conversation = session.get(Conversation, conversation_id)
    if conversation is not None:
        session.delete(conversation)
        session.commit()


@pytest.fixture()
def threads():
    """Staré vlákno s čerstvou aktivitou a nové vlákno, kde se nic neděje."""
    with Session(engine) as session:
        old = Conversation(title="staré, ale živé", created_at=_utc(21))
        old.updated_at = _utc(0)
        fresh = Conversation(title="nové a ticho", created_at=_utc(1))
        fresh.updated_at = _utc(1)
        session.add(old)
        session.add(fresh)
        session.commit()
        session.refresh(old)
        session.refresh(fresh)
        ids = (old.id, fresh.id)
        yield ids
        for conversation_id in ids:
            _wipe(session, conversation_id)


def test_the_thread_used_last_comes_first(threads):
    old_id, fresh_id = threads
    with Session(engine) as session:
        listed = [c["id"] for c in list_conversations(session)]

    assert listed.index(old_id) < listed.index(fresh_id)


def test_the_list_carries_the_last_activity(threads):
    old_id, _ = threads
    with Session(engine) as session:
        row = next(c for c in list_conversations(session) if c["id"] == old_id)

    # Bez tohohle pole by panel bucketoval podle založení i po opravě řazení.
    assert row["updated_at"] is not None
    assert row["updated_at"] > row["created_at"]


def test_a_row_from_before_the_column_gets_a_stamp():
    """`ALTER TABLE` nechá starým řádkům `NULL`; dopočítat je musí backfill."""
    from app.db import _backfill_updated_at

    with Session(engine) as session:
        conversation = Conversation(title="před migrací", created_at=_utc(30))
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        conversation_id = conversation.id
        session.add(
            Message(
                conversation_id=conversation_id,
                role="user",
                content="poslední aktivita",
                created_at=_utc(2),
            )
        )
        session.commit()

    with engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE conversation SET updated_at = NULL WHERE id = :id",
            {"id": conversation_id},
        )

    _backfill_updated_at()

    with Session(engine) as session:
        stamped = session.get(Conversation, conversation_id)
        assert stamped.updated_at is not None
        # Poslední zpráva, ne datum založení — jinak by vlákno spadlo dolů.
        assert stamped.updated_at > stamped.created_at
        _wipe(session, conversation_id)
