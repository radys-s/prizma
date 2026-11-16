"""`tools/hub/export_keys.py`: a database for a hub, with the keys and nothing else."""

import contextlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "hub" / "export_keys.py"
SECRET_CHAT = "the chat history that must stay on the notebook"


def _export(*args: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


@pytest.fixture()
def source(tmp_path):
    """A database with the app's real schema, one conversation and two keys."""
    import app.main  # noqa: F401 - registers every table on the metadata
    from app.models.conversation import Conversation, Message
    from app.models.provider_key import ProviderKey

    path = tmp_path / "app.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        conversation = Conversation(title=SECRET_CHAT)
        session.add(conversation)
        session.commit()
        session.add(Message(conversation_id=conversation.id, role="user", content=SECRET_CHAT))
        session.add(ProviderKey(credential_id="groq", nonce=b"n" * 12, ciphertext=b"\x00groq-cipher\xff"))
        session.add(ProviderKey(credential_id="gemini", nonce=b"m" * 12, ciphertext=b"gemini-cipher"))
        session.commit()
    engine.dispose()
    return path


def test_the_copy_holds_the_key_rows_and_nothing_else(source, tmp_path):
    dest = tmp_path / "hub" / "app.db"
    result = _export(source, dest)
    assert result.returncode == 0, result.stderr
    assert "2 keys" in result.stdout

    with contextlib.closing(sqlite3.connect(dest)) as copy:
        tables = [row[0] for row in copy.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        rows = copy.execute("SELECT credential_id, nonce, ciphertext FROM providerkey ORDER BY 1").fetchall()
    assert tables == ["providerkey"]
    assert rows == [("gemini", b"m" * 12, b"gemini-cipher"), ("groq", b"n" * 12, b"\x00groq-cipher\xff")]
    assert SECRET_CHAT.encode() not in dest.read_bytes()
    assert SECRET_CHAT.encode() in source.read_bytes()  # the source is read, never changed


def test_the_app_builds_its_other_tables_around_the_copy(source, tmp_path):
    from app.models.conversation import Conversation
    from app.models.provider_key import ProviderKey
    from sqlmodel import select

    dest = tmp_path / "app-hub.db"
    assert _export(source, dest).returncode == 0
    engine = create_engine(f"sqlite:///{dest.as_posix()}")
    SQLModel.metadata.create_all(engine)  # what `init_db` runs on start
    with Session(engine) as session:
        assert session.exec(select(Conversation)).all() == []
        assert session.get(ProviderKey, "groq").ciphertext == b"\x00groq-cipher\xff"
    engine.dispose()


def test_an_existing_file_is_never_overwritten(source, tmp_path):
    dest = tmp_path / "taken.db"
    dest.write_bytes(b"something else")
    result = _export(source, dest)
    assert result.returncode != 0
    assert dest.read_bytes() == b"something else"


def test_a_missing_source_fails_without_leaving_a_file(tmp_path):
    dest = tmp_path / "out.db"
    assert _export(tmp_path / "nope.db", dest).returncode != 0
    assert not dest.exists()
