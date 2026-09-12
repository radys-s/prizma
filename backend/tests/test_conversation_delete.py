"""Smazání vlákna musí vzít i jeho zprávy — a nespadnout na cizím klíči.

Od chvíle, kdy databáze cizí klíče opravdu vynucuje, je na mazání citlivé
pořadí. Mezi `Conversation` a `Message` není žádný ORM vztah, takže když se
obojí smaže v jednom flushi, SQLAlchemy nemá podle čeho je seřadit a pošle
napřed `DELETE` vlákna — na kterém cizí klíč neprojde. Endpoint proto zprávy
maže jedním hromadným příkazem, který jde na server hned.

Endpoint se volá přímo se session: v public módu je za přihlášením a tenhle
test je o mazání, ne o autentizaci.
"""

import pytest
from sqlmodel import Session, select

from app.api.conversations import delete_conversation
from app.db import engine
from app.models.conversation import Conversation, Message


@pytest.fixture(autouse=True)
def schema():
    """Tabulky vytváří jinak až lifespan aplikace; tenhle test klienta nemá."""
    from app.db import init_db

    init_db()


def _thread_with(messages: int) -> int:
    with Session(engine) as session:
        conversation = Conversation(title="ke smazání")
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        for i in range(messages):
            session.add(
                Message(conversation_id=conversation.id, role="user", content=f"zpráva {i}")
            )
        session.commit()
        return conversation.id


def test_a_thread_with_messages_can_be_deleted():
    conversation_id = _thread_with(3)

    with Session(engine) as session:
        assert delete_conversation(conversation_id, session) == {"ok": True}

    with Session(engine) as session:
        assert session.get(Conversation, conversation_id) is None
        left = session.exec(
            select(Message).where(Message.conversation_id == conversation_id)
        ).all()
        # Osiřelé zprávy by zabíraly místo a vyskakovaly v hledání napříč vlákny.
        assert left == []


def test_an_empty_thread_deletes_too():
    conversation_id = _thread_with(0)

    with Session(engine) as session:
        delete_conversation(conversation_id, session)

    with Session(engine) as session:
        assert session.get(Conversation, conversation_id) is None
