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

    # What the answer cost, so the router's value is visible instead of
    # implied. `latency_ms` is wall clock from the first byte of the request to
    # the last token — search stage included, because that is the wait the user
    # actually sat through. `tokens` is whatever the provider reported and is
    # very often None: only some of them return usage on a stream.
    latency_ms: Optional[int] = None
    tokens: Optional[int] = None
