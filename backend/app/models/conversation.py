from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = "Nová konverzace"
    created_at: datetime = Field(default_factory=utcnow)

    # Auto-compact state. Once a thread outgrows the executor's context budget,
    # `routing/compact.py` folds its oldest turns into `summary` and records how
    # far it got in `summarized_through_id`. Persisted rather than recomputed so
    # a long conversation costs one summarisation call per compaction, not one
    # per turn. Both nullable — most threads never get here.
    summary: Optional[str] = None
    summarized_through_id: Optional[int] = None

    # Sorts above every date bucket in the sidebar. Not a "favourite" — the
    # point is keeping the thread you are living in reachable once fifty others
    # have pushed it down.
    pinned: bool = False


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id", index=True)
    role: str  # "user" | "assistant" | "system"
    content: str
    provider_id: Optional[str] = None
    model_name: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)

    # How the router got here. Kept per message rather than per conversation:
    # the picker can change mid-thread, and reloading a conversation should
    # show what each answer actually cost, not what the picker says right now.
    # Every field is nullable because rows written before this existed have
    # none — see the migration in app/db.py.
    capability: Optional[str] = None
    complexity: Optional[str] = None
    effort: Optional[str] = None
    planner_model: Optional[str] = None  # None means the heuristic decided
    substituted_from: Optional[str] = None  # pinned provider that couldn't answer
    revised: bool = False  # went through the max-tier review pass

    # Which depth the search stage ran at: "web", "deep", or None when the
    # answer never touched the internet.
    search_mode: Optional[str] = None
    # JSON array of {"title", "url"} — a text column rather than a table on
    # purpose: sources are written once, read back whole with their message,
    # and never queried or joined. A table would buy nothing and cost a
    # migration. Numbering is positional and matches the [n] citations in
    # `content`, so the order must never be rewritten after the fact.
    sources: Optional[str] = None

    # JSON array of {"index", "title", "provider_id", "display_name", "model",
    # "ok", "ms"} — the fan-out that produced this answer, or None when the
    # task was never split. Text column for the same reason as `sources`:
    # written once, read back whole with its message, never queried.
    #
    # Uložené poté, co byl rozpad původně jen živý. Živá verze zmizela s posledním
    # tokenem, takže po reloadu nebylo poznat, jestli odpověď napsal jeden model,
    # nebo čtyři — což je u funkce, která stojí tři až čtyři requesty z denní
    # kvóty, ta jediná informace, kterou uživatel na výsledku nevidí.
    parts: Optional[str] = None

    # Uvažování modelu, jak přišlo ve streamu — buď zvláštním polem delty
    # (`reasoning_content`), nebo v bloku `<think>` uvnitř textu. Uložené
    # odděleně od `content` ze dvou důvodů: v bublině odpovědi nemá co dělat, a
    # do dalšího kola konverzace se posílat nesmí (historie se skládá z
    # `content`, takže tohle je z ní vyloučené samo od sebe).
    reasoning: Optional[str] = None
    # Jak dlouho model přemýšlel, než napsal první token odpovědi. Vlastní
    # číslo, ne kus `latency_ms` — z celkového času nejde poznat, kolik padlo na
    # uvažování a kolik na hledání na webu.
    reasoning_ms: Optional[int] = None

    # What the answer cost, so the router's value is visible instead of
    # implied. `latency_ms` is wall clock from the first byte of the request to
    # the last token — search stage included, because that is the wait the user
    # actually sat through. `tokens` is whatever the provider reported and is
    # very often None: only some of them return usage on a stream.
    latency_ms: Optional[int] = None
    tokens: Optional[int] = None
