import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from app.auth import ratelimit
from app.db import engine, get_session
from app.models.conversation import Conversation, Message
from app.providers.client import stream_chat
from app.routing import compact as compacting
from app.routing import decompose as decomposing
from app.routing import effort as effort_tiers
from app.routing import plan as planning
from app.routing import research as researching
from app.routing import selector
from app.routing import titles

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Background naming tasks, held only so asyncio does not collect them while
# they are still running. Discarded by their own done-callback.
_naming: set[asyncio.Task] = set()


# One answer can hold a worker for up to two minutes and spends provider quota,
# so the ceiling is "a person typing fast", not "a script".
_CHAT_LIMIT = 20
_CHAT_WINDOW = 60.0


def _limit_chat(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    try:
        ratelimit.check("chat", ip, limit=_CHAT_LIMIT, window=_CHAT_WINDOW)
    except ratelimit.RateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail=f"Příliš mnoho zpráv za sebou. Zkus to za {exc.retry_after} s.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


class ChatRequest(BaseModel):
    # Capped for the same reason `EditRequest.content` is: nothing else in the
    # stack bounds a request body, and this one is persisted, planned over and
    # forwarded to a provider.
    message: str = Field(min_length=1, max_length=32000)
    conversation_id: int | None = None
    # None or "auto" means full routing. Anything else is a registry provider
    # id the user pinned in the picker — honoured first, but not exclusively.
    model: str | None = None
    effort: str = effort_tiers.DEFAULT_EFFORT
    # Globe control: "auto" | "web" | "deep" | "off". Not validated as an enum
    # on purpose — `research.resolve_mode` falls back to "auto" for anything it
    # does not recognise, so a stale value in localStorage costs nothing.
    search: str = researching.DEFAULT_MODE


class RegenerateRequest(BaseModel):
    conversation_id: int
    model: str | None = None
    effort: str = effort_tiers.DEFAULT_EFFORT
    search: str = researching.DEFAULT_MODE


class EditRequest(RegenerateRequest):
    """Rewrite one user turn and re-run the thread from there.

    Everything after the edited message is deleted, because an answer to a
    question that no longer exists is worse than no answer: it reads as if the
    model replied to the new text and got it wrong.
    """

    message_id: int
    content: str = Field(min_length=1, max_length=32000)


def _rows(session: Session, conversation_id: int) -> list[Message]:
    return list(
        session.exec(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
        ).all()
    )


def _pinned(model: str | None) -> str | None:
    return None if model in (None, "", "auto") else model


def _refusal(message: str) -> EventSourceResponse:
    """Turns a pre-flight refusal into the same SSE shape as any other error.

    Deliberately not an HTTP error: the frontend has one streaming path, and a
    409 would need a second one. This also means no conversation row and no
    user message are written for a request that was never going to run.
    """

    async def event_generator():
        yield {"event": "error", "data": json.dumps({"content": message})}

    return EventSourceResponse(event_generator())


def _resolve_profile(effort: str, search: str) -> effort_tiers.EffortProfile:
    """Deep research needs a planner, so `light` cannot host it.

    Light skips the planner entirely, and the planner is what splits a research
    request into sub-questions. Rather than silently degrading deep mode to a
    single search, the tier is lifted to `medium` — the user asked for research
    and gets research.
    """
    profile = effort_tiers.resolve(effort)
    if search == "deep" and not profile.use_planner:
        return effort_tiers.resolve("medium")
    return profile


def _stream_response(
    conversation_id: int,
    messages: list[dict],
    plan: planning.Plan,
    profile: effort_tiers.EffortProfile,
    pinned: str | None,
    depth: str = "off",
    compact_info: compacting.CompactInfo | None = None,
    replaces: tuple[int, ...] = (),
    name_from: str | None = None,
) -> EventSourceResponse:
    """Streams one answer and persists it, even if the client hangs up early.

    The save lives in a `finally` on purpose: when the user hits stop the
    browser aborts the fetch, sse-starlette cancels this generator mid-loop,
    and anything already streamed would otherwise be lost — the UI would show
    text that vanishes on the next reload.

    `replaces` holds message ids the new answer supersedes — regenerate's old
    answers. They are deleted in the same `finally`, and only once there is
    something to replace them with, so a run where every provider is down
    leaves the conversation exactly as it was.

    `name_from` is the question a brand-new conversation should be named after;
    the naming call is fired after the answer is saved and is never awaited by
    the stream.
    """

    async def event_generator():
        # Wall clock from here, not from the first token: the search stage and
        # the planner are part of the wait the user actually sat through, and a
        # number that excludes them would flatter the router.
        started_at = time.monotonic()
        yield {
            "event": "start",
            "data": json.dumps(
                {"conversation_id": conversation_id, "capability": plan.capability}
            ),
        }
        # Before the plan, because it happened before the plan: the thread was
        # already too long when the planner read it.
        if compact_info is not None and compact_info.happened:
            yield {"event": "compact", "data": json.dumps(compact_info.as_dict())}
        yield {
            "event": "plan",
            "data": json.dumps(
                {
                    "capability": plan.capability,
                    "complexity": plan.complexity,
                    "goal": plan.goal,
                    "source": plan.source,
                    "planner_model": plan.planner_model,
                    "effort": profile.id,
                    "needs_search": plan.needs_search,
                    "search": depth,
                }
            ),
        }

        # Jediné místo, kde je rozhodnutí routeru vidět v logu serveru. Bez něj
        # se u chybějícího fan-outu nedá odlišit „tier to nedovolil“ od „plánovač
        # úkol označil za moderate“ — a obojí vypadá stejně: jedna odpověď,
        # žádný rozpad na části.
        log.info(
            "router: effort=%s capability=%s complexity=%s source=%s planner=%s "
            "search=%s split=%s",
            profile.id,
            plan.capability,
            plan.complexity,
            plan.source,
            plan.planner_model,
            depth,
            decomposing.should_split(plan, profile),
        )

        # The search stage runs here rather than before the response so its
        # progress can be streamed: deep mode is allowed a minute and change,
        # and a minute of silence before the first frame looks like a hang.
        findings: researching.Findings | None = None
        question = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        async for rev in researching.run(question, profile, depth):
            if rev.type == "queries":
                yield {
                    "event": "search_start",
                    "data": json.dumps({"depth": depth, "queries": rev.queries}),
                }
            elif rev.type == "reading":
                yield {
                    "event": "read_start",
                    "data": json.dumps({"count": len(rev.urls), "urls": rev.urls}),
                }
            elif rev.type == "abort":
                yield {"event": "search_abort", "data": json.dumps({"reason": rev.reason})}
            elif rev.type == "done" and rev.findings is not None:
                findings = rev.findings
                yield {
                    "event": "search_done",
                    "data": json.dumps(
                        {
                            "queries": findings.queries,
                            "provider_id": findings.provider_id,
                            "partial": findings.partial,
                            # Same shape `researching.sources_json()` persists,
                            # favicon included. They used to differ, so a source
                            # card grew its icon only after a reload.
                            "sources": [
                                {
                                    "title": s.title,
                                    "url": s.url,
                                    **({"favicon": s.favicon} if s.favicon else {}),
                                }
                                for s in findings.sources
                            ],
                        }
                    ),
                }

        # Fan-out. Only fires when the planner rated the task hard and the
        # tier allows it, and every failure path here just leaves `breakdown`
        # None — the answer below is then written the ordinary way.
        breakdown: decomposing.Breakdown | None = None
        async for part in decomposing.run(messages, plan, profile):
            if part.type == "split":
                yield {
                    "event": "split_start",
                    "data": json.dumps(
                        {
                            "parts": [
                                {"index": sub.index, "title": sub.title}
                                for sub in part.subtasks
                            ]
                        }
                    ),
                }
            elif part.type == "part_done" and part.result is not None:
                res = part.result
                yield {
                    "event": "part_done",
                    "data": json.dumps(
                        {
                            "index": res.subtask.index,
                            "title": res.subtask.title,
                            "provider_id": res.provider_id,
                            "display_name": res.display_name,
                            "model": res.model,
                            "ok": res.ok,
                            "ms": res.ms,
                        }
                    ),
                }
            elif part.type == "abort":
                yield {"event": "split_abort", "data": json.dumps({"reason": part.reason})}
            elif part.type == "done":
                breakdown = part.breakdown

        assistant_text = ""
        draft = ""  # the pre-review answer, kept so a failed review can restore it
        in_review = False
        used_provider: str | None = None
        used_model: str | None = None
        substituted_from: str | None = None
        revised = False
        tokens = 0
        latency_ms = 0
        try:
            async for ev in stream_chat(
                messages,
                plan,
                profile,
                pinned=pinned,
                findings=findings,
                breakdown=breakdown,
            ):
                if ev.type == "review_start":
                    # The bubble is about to be rewritten in place — park the
                    # draft so an abort can put it back.
                    draft, assistant_text, in_review = assistant_text, "", True
                elif ev.type == "review_abort":
                    assistant_text, in_review = draft, False
                elif ev.type == "delta":
                    assistant_text += ev.content
                if ev.type in ("meta", "done"):
                    used_provider = ev.provider_id or used_provider
                    used_model = ev.model_name or used_model
                if ev.type == "meta" and ev.note:
                    substituted_from = pinned
                payload = {
                    "provider_id": ev.provider_id,
                    "model_name": ev.model_name,
                    "content": ev.content,
                    "note": ev.note,
                    "revised": ev.revised,
                }
                if ev.type == "done":
                    in_review = False
                    revised = ev.revised
                    tokens = ev.tokens
                    latency_ms = int((time.monotonic() - started_at) * 1000)
                    payload["latency_ms"] = latency_ms
                    payload["tokens"] = tokens
                yield {"event": ev.type, "data": json.dumps(payload)}
        except selector.NotEnoughProviders as exc:
            yield {"event": "error", "data": json.dumps({"content": str(exc)})}
        except selector.NoProviderAvailable as exc:
            yield {"event": "error", "data": json.dumps({"content": str(exc)})}
        finally:
            if in_review:
                # Stopped mid-revision: the draft is a complete answer, the
                # half-written revision is not. Keep the complete one.
                assistant_text = draft or assistant_text
                revised = False
            # Konverzace se dá smazat uprostřed streamu a `finally` se provede
            # tak jako tak. Bez téhle kontroly by odpověď skončila jako řádek
            # ukazující na neexistující vlákno: nikdy se nezobrazí, nikdy se
            # nesmaže a drží cizí klíč na záznam, který už není.
            #
            # Vlastní krátká session, ne `return` uvnitř `finally` — ten by
            # spolkl i GeneratorExit, kterým se generátor zavírá při odpojení
            # klienta, a asyncio by to ohlásilo jako „async generator ignored
            # GeneratorExit“. Cena je jeden SELECT na konci streamu.
            if assistant_text:
                with Session(engine) as check_session:
                    if check_session.get(Conversation, conversation_id) is None:
                        log.info(
                            "odpověď zahozena — konverzace %s mezitím smazána",
                            conversation_id,
                        )
                        assistant_text = ""

            if assistant_text:
                with Session(engine) as save_session:
                    # Only now, with a real answer in hand, do the superseded
                    # ones go away. Deleting them up front — which is what
                    # regenerate used to do — meant a run where every provider
                    # was down destroyed an answer the user already had.
                    for stale_id in replaces:
                        stale = save_session.get(Message, stale_id)
                        if stale is not None:
                            save_session.delete(stale)
                    save_session.add(
                        Message(
                            conversation_id=conversation_id,
                            role="assistant",
                            content=assistant_text,
                            provider_id=used_provider,
                            model_name=used_model,
                            capability=plan.capability,
                            complexity=plan.complexity,
                            effort=profile.id,
                            planner_model=plan.planner_model,
                            substituted_from=substituted_from,
                            revised=revised,
                            # Only on success: the badge means "this answer
                            # used the web", and a search that aborted did not.
                            search_mode=depth if findings else None,
                            sources=researching.sources_json(findings),
                            parts=decomposing.parts_json(breakdown),
                            # Measured even when the stream was cut short, so a
                            # stopped answer still reports the time it took.
                            latency_ms=latency_ms
                            or int((time.monotonic() - started_at) * 1000),
                            tokens=tokens or None,
                        )
                    )
                    save_session.commit()

                if name_from:
                    # Fire and forget. The stream is over; making the user wait
                    # on a naming call would add a second to every new chat for
                    # a sidebar label. A reference is kept only so the task is
                    # not garbage-collected mid-flight.
                    task = asyncio.create_task(
                        titles.autoname(conversation_id, name_from, assistant_text)
                    )
                    _naming.add(task)
                    task.add_done_callback(_naming.discard)

    return EventSourceResponse(event_generator())


@router.post("/stream")
async def chat_stream(
    request: Request,
    req: ChatRequest,
    session: Session = Depends(get_session),
):
    _limit_chat(request)
    try:
        selector.require_minimum()
    except selector.NotEnoughProviders as exc:
        return _refusal(str(exc))

    if req.conversation_id is None:
        # A placeholder until the first answer is written and `titles.autoname`
        # can name the thread after what it turned out to be about.
        conversation = Conversation(title=titles._placeholder(req.message))
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        name_from = req.message
    else:
        # An unknown id used to be accepted silently, writing the question and
        # its answer into a conversation that does not exist — rows nothing
        # will ever display. Regenerate and edit have always 404'd here.
        conversation = session.get(Conversation, req.conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Konverzace nenalezena")
        name_from = None

    conversation_id = conversation.id
    session.add(Message(conversation_id=conversation_id, role="user", content=req.message))
    session.commit()

    rows = _rows(session, conversation_id)
    messages, compact_info = await compacting.prepare(session, conversation, rows)
    history_chars = sum(len(m["content"]) for m in messages[:-1])
    profile = _resolve_profile(req.effort, req.search)
    plan = await planning.make_plan(messages, profile, history_chars=history_chars)
    depth = researching.resolve_mode(req.search, plan.needs_search)
    return _stream_response(
        conversation_id,
        messages,
        plan,
        profile,
        _pinned(req.model),
        depth,
        compact_info=compact_info,
        name_from=name_from,
    )


@router.post("/regenerate")
async def chat_regenerate(
    request: Request,
    req: RegenerateRequest,
    session: Session = Depends(get_session),
):
    _limit_chat(request)
    """Drops the trailing answer and re-runs the last user turn.

    Worth having here rather than client-side: the planner runs again and may
    well land on a different provider than last time.
    """
    try:
        selector.require_minimum()
    except selector.NotEnoughProviders as exc:
        return _refusal(str(exc))

    conversation = session.get(Conversation, req.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Konverzace nenalezena")

    rows = _rows(session, req.conversation_id)
    # Identify the answers being replaced but do NOT delete them yet — see
    # `replaces` in `_stream_response`. Nothing is destroyed until there is a
    # replacement, so a regenerate that fails outright is a no-op.
    doomed: list[int] = []
    while rows and rows[-1].role == "assistant":
        stale = rows.pop()
        if stale.id is not None:
            doomed.append(stale.id)

    if not rows or rows[-1].role != "user":
        raise HTTPException(status_code=400, detail="Není co přegenerovat.")

    messages, compact_info = await compacting.prepare(session, conversation, rows)
    history_chars = sum(len(m["content"]) for m in messages[:-1])
    profile = _resolve_profile(req.effort, req.search)
    plan = await planning.make_plan(messages, profile, history_chars=history_chars)
    depth = researching.resolve_mode(req.search, plan.needs_search)
    return _stream_response(
        req.conversation_id,
        messages,
        plan,
        profile,
        _pinned(req.model),
        depth,
        compact_info=compact_info,
        replaces=tuple(doomed),
    )


@router.post("/edit")
async def chat_edit(
    request: Request,
    req: EditRequest,
    session: Session = Depends(get_session),
):
    _limit_chat(request)
    """Rewrites one user message and re-runs the thread from that point.

    Same shape as regenerate — the planner runs again on the new text, so an
    edited question can legitimately land on a different provider. The two
    differ only in what gets dropped: regenerate throws away the trailing
    answers, this throws away everything after the edited turn.
    """
    try:
        selector.require_minimum()
    except selector.NotEnoughProviders as exc:
        return _refusal(str(exc))

    conversation = session.get(Conversation, req.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Konverzace nenalezena")

    rows = _rows(session, req.conversation_id)
    index = next((i for i, m in enumerate(rows) if m.id == req.message_id), None)
    # A forged or stale id is a 404, never a 500: the frontend can hold an id
    # from a conversation that has since been deleted and rebuilt.
    if index is None:
        raise HTTPException(status_code=404, detail="Zpráva nenalezena")
    if rows[index].role != "user":
        raise HTTPException(status_code=400, detail="Upravit lze jen vlastní zprávu.")

    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Prázdná zpráva.")

    for stale in rows[index + 1 :]:
        session.delete(stale)
    rows[index].content = content
    session.add(rows[index])
    session.commit()

    # The edited turn may already have been folded into the compaction summary,
    # in which case the summary now describes a conversation that did not
    # happen — and the model would confidently continue from the version the
    # user just rewrote. Drop it and let the next compaction rebuild.
    compacting.invalidate(session, conversation, req.message_id)

    rows = rows[: index + 1]
    messages, compact_info = await compacting.prepare(session, conversation, rows)
    history_chars = sum(len(m["content"]) for m in messages[:-1])
    profile = _resolve_profile(req.effort, req.search)
    plan = await planning.make_plan(messages, profile, history_chars=history_chars)
    depth = researching.resolve_mode(req.search, plan.needs_search)
    return _stream_response(
        req.conversation_id,
        messages,
        plan,
        profile,
        _pinned(req.model),
        depth,
        compact_info=compact_info,
    )
