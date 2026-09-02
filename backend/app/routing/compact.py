"""Keeps a long conversation inside the executor's context budget.

Before this existed, every chat route sent the entire thread on every turn with
no ceiling. That is fine for ten messages and fatal for a hundred: free tiers
meter tokens per minute — Groq's is 8 000 — so a thread that grew past the
window stopped working entirely, and the user saw a raw provider error on a
conversation that had been fine an hour earlier.

The fix Tomáš asked for is the one ChatGPT and Claude both use: when the thread
passes a threshold, a cheap model folds the oldest turns into a summary, the
recent turns stay verbatim, and the model keeps roughly the same context from
then on. The summary is *persisted*, so a fifty-turn conversation costs one
summarisation per compaction rather than one per message.

**`prepare()` never raises.** Same contract as `make_plan` and `research.run`:
a dead dependency costs quality, never an answer. If there is no provider, the
call times out, or the model returns nothing, the thread is truncated instead
and `CompactInfo.fallback` says so — the caller reports it and the user gets a
slightly forgetful answer rather than an error.

**What is sent, not what is stored.** Nothing here deletes or rewrites a
message row. The full thread stays in SQLite and still renders in the UI; only
the list handed to the provider is shortened. That matters because the summary
is written by the weakest model in the chain, and a summary is not a source of
truth worth destroying the original for.
"""

import logging

import litellm
from sqlmodel import Session

from app.models.conversation import Conversation, Message
from app.providers import keystore, model_health
from app.routing import selector

log = logging.getLogger(__name__)

# ≈3k tokens of history. Chosen against the tightest window this app targets
# (Groq's free 8 000 TPM): the research digest can be 2 500 characters, the
# answer itself is allowed up to ~4 000 tokens on the `max` tier, and the
# planner has already run. 12k characters of history leaves all of that room.
COMPACT_THRESHOLD_CHARS = 12_000

# How many messages survive verbatim. Six is two or three exchanges — enough
# that "and the second one?" still resolves against something real, which is
# exactly what a summary is worst at preserving.
KEEP_TAIL_TURNS = 6

_SUMMARY_MAX_TOKENS = 700
_SUMMARY_TIMEOUT = 20
_SUMMARY_INPUT_CHARS = 40_000  # what is handed to the summariser in one go

_SYSTEM = (
    "Shrnuješ starší část konverzace, aby se vešla do kontextu modelu, který "
    "v ní bude pokračovat.\n\n"
    "Zachovej: fakta, čísla, jména, verze, názvy souborů a identifikátorů v "
    "kódu, rozhodnutí, která padla, zadání a omezení, na kterých se uživatel "
    "shodl, a všechno, co si uživatel výslovně přál zapamatovat.\n\n"
    "Vynech: pozdravy, omluvy, opakování, průběžné komentáře a slepé uličky, "
    "které už byly opuštěné.\n\n"
    "Piš věcně a v bodech, ve stejném jazyce jako konverzace. Nepiš úvod ani "
    "závěr a neoslovuj uživatele — tvůj výstup čte model, ne člověk. Nesmí "
    "poznat, že část konverzace chybí."
)

_PREFIX = (
    "Shrnutí starší části téhle konverzace (kvůli délce byla zkrácena). "
    "Ber ho jako plnohodnotný kontext:\n\n"
)


class CompactInfo:
    """What happened, so the caller can tell the user.

    A plain class rather than a dataclass because it is three fields read in one
    place; `folded == 0` means nothing was compacted and nothing is reported.
    """

    __slots__ = ("folded", "kept", "fallback")

    def __init__(self, folded: int = 0, kept: int = 0, fallback: bool = False):
        #: How many messages the summary now stands in for.
        self.folded = folded
        #: How many are still being sent verbatim.
        self.kept = kept
        #: True when the summariser failed and the prefix was simply dropped.
        self.fallback = fallback

    @property
    def happened(self) -> bool:
        return self.folded > 0

    def as_dict(self) -> dict:
        return {"folded": self.folded, "kept": self.kept, "fallback": self.fallback}


def _as_messages(rows: list[Message]) -> list[dict]:
    return [{"role": m.role, "content": m.content} for m in rows]


def _chars(messages: list[dict]) -> int:
    return sum(len(m.get("content") or "") for m in messages)


def invalidate(session: Session, conversation: Conversation, from_message_id: int) -> None:
    """Drops a summary that describes messages which no longer exist.

    Editing a turn deletes everything after it. If the summary had already
    folded that turn in, it now describes a conversation that did not happen —
    and unlike a stale cache, that is not a performance problem but a model
    confidently continuing from a version of events the user just rewrote.
    """
    through = conversation.summarized_through_id
    if through is None or through < from_message_id:
        return
    conversation.summary = None
    conversation.summarized_through_id = None
    session.add(conversation)
    session.commit()


async def _summarise(text: str) -> str:
    """One cheap non-streaming call. Returns "" rather than raising."""
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
                    {"role": "user", "content": text[:_SUMMARY_INPUT_CHARS]},
                ],
                stream=False,
                temperature=0,
                max_tokens=_SUMMARY_MAX_TOKENS,
                timeout=_SUMMARY_TIMEOUT,
            )
            out = (response.choices[0].message.content or "").strip()
            if out:
                return out
            log.warning("compaction returned nothing on %s (%s)", provider.id, model)
        except Exception as exc:  # noqa: BLE001 - see module docstring
            log.warning("compaction failed on %s (%s): %s", provider.id, model, exc)
            continue
    return ""


def _transcript(rows: list[Message], previous: str | None) -> str:
    """What the summariser reads: the old summary plus the turns being folded."""
    parts: list[str] = []
    if previous:
        parts.append(f"Dosavadní shrnutí:\n{previous}\n")
        parts.append("Novější část konverzace, kterou do něj zapracuj:")
    labels = {"user": "Uživatel", "assistant": "Asistent", "system": "Systém"}
    for row in rows:
        parts.append(f"{labels.get(row.role, row.role)}: {row.content}")
    return "\n\n".join(parts)


async def prepare(
    session: Session, conversation: Conversation, rows: list[Message]
) -> tuple[list[dict], CompactInfo]:
    """The message list to send, plus what had to be folded to get there.

    Three outcomes, in order of how often they happen:

    1. Short thread — returned untouched, no call, no writes.
    2. Already compacted and still short enough — the stored summary is
       prepended and the folded rows are skipped. Still no call.
    3. Too long — the prefix is summarised, persisted, and replaced.
    """
    through = conversation.summarized_through_id
    summary = conversation.summary if through is not None else None

    live = [r for r in rows if summary is None or (r.id or 0) > through]
    folded_already = len(rows) - len(live)

    def assemble(text: str | None, tail: list[Message]) -> list[dict]:
        head = [{"role": "system", "content": _PREFIX + text}] if text else []
        return head + _as_messages(tail)

    messages = assemble(summary, live)
    if _chars(messages) < COMPACT_THRESHOLD_CHARS:
        return messages, CompactInfo(folded=folded_already, kept=len(live))

    # Keep the tail verbatim, fold everything before it. The final message is
    # always in the tail — it is the question being asked right now.
    tail = live[-KEEP_TAIL_TURNS:] if len(live) > KEEP_TAIL_TURNS else live
    to_fold = live[: len(live) - len(tail)]
    if not to_fold:
        # A single turn is already over budget. Nothing to summarise; the
        # provider's own limit is the next thing that will complain, and
        # truncating the user's actual question would be worse than that.
        return messages, CompactInfo(folded=folded_already, kept=len(live))

    text = await _summarise(_transcript(to_fold, summary))
    if not text:
        # Fall back to plain truncation. Deliberately still returns the tail:
        # a forgetful answer beats an error, and `fallback` tells the user.
        return (
            assemble(summary, tail),
            CompactInfo(
                folded=folded_already + len(to_fold), kept=len(tail), fallback=True
            ),
        )

    conversation.summary = text
    conversation.summarized_through_id = to_fold[-1].id
    session.add(conversation)
    session.commit()

    return (
        assemble(text, tail),
        CompactInfo(folded=folded_already + len(to_fold), kept=len(tail)),
    )
