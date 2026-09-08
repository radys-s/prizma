"""Stage 1.5 of the router: split a hard task, give each piece to a *different*
model, run them at the same time, then hand every result to the model that
writes the final answer.

The point is not speed. One free-tier model asked to do four things at once does
all four shallowly; four models each asked to do one thing go deeper, and the
synthesiser gets to see where they disagree. Different providers also means
different training data and different blind spots — a part the fan-out gets
wrong is more likely to be visibly contradicted by a sibling than to pass
unnoticed.

Where it sits::

    plan (stage 1) -> decompose (here) -> stream_chat (stage 2) -> review

Only `pro` and `max` run it, and only when the planner rated the task "hard"
(`EffortProfile.decompose`). On free tiers every extra call is a slice of a
daily quota, so a stage that costs three or four requests must not fire on
"kolik je hodin".

**Three invariants, all of them load-bearing:**

1. **run() never raises.** Same guarantee as `plan.make_plan()`, for the same
   reason: this is an upgrade to the answer, not a dependency of it. A
   malformed split, a dead provider, an exhausted budget — every path ends with
   the normal single-model answer, at worst a few seconds later.

2. **The user's message is never rewritten.** The results travel as a *system*
   message exactly like `plan.system_message()` and `research.system_message()`;
   the user turn is passed through verbatim. The synthesiser answers the
   original question and treats the parts as notes.

3. **Fewer than two usable parts means no system message at all.** One part is
   not a decomposition — it is the same answer written twice, and feeding it
   back would only bias the synthesiser toward a single model's take.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass

import litellm

from app.providers import keystore, model_health
from app.providers.registry import CAPABILITIES, ProviderModel
from app.routing import selector
from app.routing.effort import EffortProfile
from app.routing.plan import Plan

# The planner's JSON tolerances, reused rather than copied: models wrap JSON in
# fences and hand back arrays where a string was asked for, and those two
# helpers already know it. A second copy here would drift the first time one of
# them is fixed.
from app.routing.plan import _as_text as _text
from app.routing.plan import _parse as _parse_json

log = logging.getLogger(__name__)

# Two is the minimum that is actually a decomposition; four is where a free
# tier starts hurting — each part is a separate request against a separate
# daily quota, and the synthesiser has to read all of them.
MIN_PARTS = 2
MAX_PARTS = 4

# One part gets this long. Deliberately shorter than the planner's budget is
# generous: parts run concurrently, so the slowest one sets the wall clock.
_PART_TIMEOUT = 45

# Hard ceiling on the whole stage, splitting included. Past this the user is
# staring at a spinner, and a plain answer now beats a better answer later.
_STAGE_BUDGET = 75

_SPLIT_TIMEOUT = 15
_SPLIT_MAX_TOKENS = 1200
_SPLIT_ATTEMPTS = 2

# How much of one part's answer reaches the synthesiser. Four parts at 6000
# characters is roughly 6k tokens of system prompt on top of the conversation —
# already more than some free-tier context windows enjoy.
_RESULT_CHARS = 6000

_HISTORY_TURNS = 4
_HISTORY_CHARS = 1000
_MESSAGE_CHARS = 6000

_ANSWER_CAPABILITIES = tuple(c for c in CAPABILITIES if c != "search")


@dataclass(frozen=True)
class Subtask:
    index: int  # 1-based, shown to the user and cited by the synthesiser
    title: str  # short label for the UI, in the user's language
    prompt: str  # self-contained instruction for one model
    capability: str  # steers which provider gets it


@dataclass(frozen=True)
class PartResult:
    subtask: Subtask
    provider_id: str
    display_name: str
    model: str
    text: str  # empty when the part failed
    error: str  # empty when the part worked
    ms: int

    @property
    def ok(self) -> bool:
        return bool(self.text)


@dataclass(frozen=True)
class Breakdown:
    subtasks: tuple[Subtask, ...]
    results: tuple[PartResult, ...]
    splitter_model: str

    @property
    def done(self) -> tuple[PartResult, ...]:
        return tuple(r for r in self.results if r.ok)

    @property
    def failed(self) -> tuple[PartResult, ...]:
        return tuple(r for r in self.results if not r.ok)

    @property
    def usable(self) -> bool:
        return len(self.done) >= MIN_PARTS


@dataclass(frozen=True)
class Event:
    """Mirrors `research.Revision` so `chat.py` can stream both the same way."""

    type: str  # "split" | "part_start" | "part_done" | "done" | "abort"
    subtasks: tuple[Subtask, ...] = ()
    result: PartResult | None = None
    breakdown: Breakdown | None = None
    reason: str = ""


_SPLIT_PROMPT = """You are the decomposition stage of a multi-provider chat app.
You do NOT answer the user. You split their task into independent parts, and a
different model will be given each part. A final model then merges the parts
into one answer.

Reply with a single JSON object and nothing else:

{"parts": [{"title": "...", "prompt": "...", "capability": "..."}, ...]}

Rules that decide whether this is worth doing at all:

- Parts must be INDEPENDENT. Each one is answered by a model that cannot see
  the other parts or their results. If part 2 needs part 1's output, they are
  one part, not two.
- Between 2 and 4 parts. Fewer is better when the task does not genuinely have
  separable pieces — return an empty list when splitting would only duplicate
  work.
- Split by ASPECT, not by paragraph: "security review", "performance", "API
  design", "counter-arguments", "worked example". Never "first half of the
  answer" / "second half".
- Together the parts must cover the task. Do not leave the merging model to
  invent the missing piece.

"title": two to four words naming the part, in the SAME LANGUAGE as the user.
Shown to the user while the part runs.

"prompt": the complete instruction for that part, in the same language as the
user. It must stand alone: restate everything the model needs, because it will
NOT see the conversation. Say what to produce and how long it should be. Do not
tell it to write an introduction or a conclusion — its text is raw material for
another model, not an answer to a person.

"capability": one of "code", "creative", "long_context", "fast", "general" —
which kind of model suits that one part.

Return {"parts": []} if the task is a single question, a short lookup, a
rewrite of text the user pasted, or anything else where four independent models
would just say the same thing four times."""

_PART_SYSTEM = (
    "Zpracováváš jednu část většího úkolu. Tvůj text nečte uživatel — čte ho "
    "jiný model, který z několika takových částí složí výslednou odpověď.\n\n"
    "Piš proto věcně a hutně: žádný úvod, žádné shrnutí na konci, žádné "
    "zdvořilosti. Drž se zadané části a nepiš, co mají na starost ostatní. "
    "Když ti pro odpověď chybí podklad, napiš to rovnou místo odhadu — "
    "vymyšlený detail projde do finální odpovědi jako fakt."
)


def should_split(plan: Plan, profile: EffortProfile) -> bool:
    """Whether this request earns a fan-out.

    Two conditions, both required: the tier allows it and the planner rated the
    task hard. The heuristic plan is always "moderate", so a planner outage
    silently disables the stage — which is the right failure direction.
    """
    return profile.decompose and plan.complexity == "hard"


def _condense(messages: list[dict]) -> list[dict]:
    recent = messages[-_HISTORY_TURNS:]
    out: list[dict] = []
    for i, m in enumerate(recent):
        limit = _MESSAGE_CHARS if i == len(recent) - 1 else _HISTORY_CHARS
        content = m.get("content", "")
        if len(content) > limit:
            content = content[:limit] + "\n…(zkráceno)"
        out.append({"role": m.get("role", "user"), "content": content})
    return out


def _build_subtasks(data: dict) -> tuple[Subtask, ...]:
    """Validates the splitter's answer. Anything malformed drops that part."""
    raw = data.get("parts")
    if not isinstance(raw, list):
        return ()

    out: list[Subtask] = []
    for item in raw[:MAX_PARTS]:
        if not isinstance(item, dict):
            continue
        prompt = _text(item.get("prompt"))[:4000]
        if len(prompt) < 20:
            continue  # a part nobody could act on is worse than no part
        title = _text(item.get("title"))[:60] or f"Část {len(out) + 1}"
        capability = item.get("capability")
        out.append(
            Subtask(
                index=len(out) + 1,
                title=title,
                prompt=prompt,
                capability=(
                    capability if capability in _ANSWER_CAPABILITIES else "general"
                ),
            )
        )

    return tuple(out) if len(out) >= MIN_PARTS else ()


async def _ask_splitter(
    messages: list[dict], plan: Plan
) -> tuple[tuple[Subtask, ...], str]:
    """Asks a JSON-capable provider for the split. Returns () when it declines."""
    from app.routing.plan import _planner_slug  # same JSON-mode probe as stage 1

    context = _SPLIT_PROMPT
    if plan.goal:
        context += f"\n\nWhat the user is after, per the planner: {plan.goal}"

    attempts = 0
    for provider in selector.planner_candidates():
        if attempts >= _SPLIT_ATTEMPTS:
            break
        model = _planner_slug(provider)
        if model is None:
            continue
        attempts += 1
        try:
            response = await litellm.acompletion(
                model=model,
                **keystore.endpoint(provider),
                messages=[
                    {"role": "system", "content": context},
                    *_condense(messages),
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=_SPLIT_MAX_TOKENS,
                timeout=_SPLIT_TIMEOUT,
            )
            raw = response.choices[0].message.content or ""
            return _build_subtasks(_parse_json(raw)), model
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
            log.warning("splitter %s (%s) failed: %s", provider.id, model, exc)
            continue

    return (), ""


def _assign(subtasks: tuple[Subtask, ...]) -> list[tuple[Subtask, ProviderModel]]:
    """One provider per part, each part a *different* provider where possible.

    Walks each part's own capability chain and takes the best provider nobody
    else has claimed. When the app has fewer providers configured than there are
    parts, the last ones reuse a provider rather than going unanswered — a
    duplicate model still answers a different question.
    """
    taken: set[str] = set()
    pairs: list[tuple[Subtask, ProviderModel]] = []

    for sub in subtasks:
        chain = selector.candidates(sub.capability, "hard")
        chosen = next((p for p in chain if p.id not in taken), None)
        if chosen is None:
            chosen = chain[0] if chain else None
        if chosen is None:
            continue  # nothing configured at all — the caller aborts the stage
        taken.add(chosen.id)
        pairs.append((sub, chosen))

    return pairs


async def _run_part(
    sub: Subtask, provider: ProviderModel, profile: EffortProfile
) -> PartResult:
    """One part on one provider. Never raises — failures come back as a result.

    Model slugs are walked the same way `client.stream_chat` walks them, so a
    slug that died today is skipped here too instead of burning the part.
    """
    started = time.monotonic()
    last_error = "nedostupný model"

    for model in model_health.usable_models(provider.models):
        try:
            response = await litellm.acompletion(
                model=model,
                **keystore.endpoint(provider),
                messages=[
                    {"role": "system", "content": _PART_SYSTEM},
                    {"role": "user", "content": sub.prompt},
                ],
                max_tokens=max(profile.max_tokens // 2, 1024),
                timeout=_PART_TIMEOUT,
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                last_error = "prázdná odpověď"
                continue
            return PartResult(
                subtask=sub,
                provider_id=provider.id,
                display_name=provider.display_name,
                model=model,
                text=text,
                error="",
                ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001 - one dead part must not kill the rest
            last_error = type(exc).__name__
            log.warning("part %s on %s (%s) failed: %s", sub.index, provider.id, model, exc)
            model_health.mark_dead(model)

    selector.mark_cooldown(provider.id)
    return PartResult(
        subtask=sub,
        provider_id=provider.id,
        display_name=provider.display_name,
        model="",
        text="",
        error=last_error,
        ms=int((time.monotonic() - started) * 1000),
    )


async def _guarded_part(
    sub: Subtask, provider: ProviderModel, profile: EffortProfile
) -> PartResult:
    """`_run_part` with a net under it.

    `_run_part` already turns provider failures into a failed result, so this
    only catches the unforeseen — but "unforeseen" is exactly what would
    otherwise propagate out of `as_completed`, abort a fan-out whose other
    parts were fine, and leave their exceptions unretrieved in the log.
    """
    try:
        return await _run_part(sub, provider, profile)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - one bad part, not one bad answer
        log.warning("part %s crashed: %s", sub.index, exc)
        return PartResult(
            subtask=sub,
            provider_id=provider.id,
            display_name=provider.display_name,
            model="",
            text="",
            error=type(exc).__name__,
            ms=0,
        )


async def _fan_out(
    pairs: list[tuple[Subtask, ProviderModel]], profile: EffortProfile
):
    """Yields each part's result as it lands, so the UI can tick them off."""
    tasks = [
        asyncio.create_task(_guarded_part(sub, provider, profile))
        for sub, provider in pairs
    ]
    try:
        for done in asyncio.as_completed(tasks):
            yield await done
    finally:
        # A stage-level timeout cancels this generator mid-loop. Without the
        # cancel the parts keep running against the user's daily quota for
        # another 45 seconds; without the gather they are torn down at garbage
        # collection instead, which logs "Task was destroyed but it is pending".
        pending = [t for t in tasks if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _run(messages: list[dict], plan: Plan, profile: EffortProfile):
    subtasks, splitter_model = await _ask_splitter(messages, plan)
    if not subtasks:
        yield Event(type="abort", reason="Úkol nemá smysl dělit.")
        return

    pairs = _assign(subtasks)
    if len(pairs) < MIN_PARTS:
        yield Event(type="abort", reason="Není dost dostupných modelů na rozdělení.")
        return

    yield Event(type="split", subtasks=tuple(sub for sub, _ in pairs))

    results: list[PartResult] = []
    async for result in _fan_out(pairs, profile):
        results.append(result)
        yield Event(type="part_done", result=result)

    breakdown = Breakdown(
        subtasks=tuple(sub for sub, _ in pairs),
        results=tuple(sorted(results, key=lambda r: r.subtask.index)),
        splitter_model=splitter_model,
    )
    if not breakdown.usable:
        yield Event(type="abort", reason="Většina částí selhala.")
        return
    yield Event(type="done", breakdown=breakdown)


async def run(messages: list[dict], plan: Plan, profile: EffortProfile):
    """Public entry point. Never raises and never runs past `_STAGE_BUDGET`.

    Yields events for the UI and, on success, a final "done" carrying the
    breakdown. Any abort simply means the caller answers the normal way.
    """
    if not should_split(plan, profile):
        return

    deadline = time.monotonic() + _STAGE_BUDGET
    agen = _run(messages, plan, profile)
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                yield Event(type="abort", reason="Rozdělení trvalo příliš dlouho.")
                return
            try:
                event = await asyncio.wait_for(agen.__anext__(), timeout=left)
            except StopAsyncIteration:
                return
            yield event
    except asyncio.TimeoutError:
        log.warning("decomposition budget of %ss exhausted", _STAGE_BUDGET)
        yield Event(type="abort", reason="Rozdělení trvalo příliš dlouho.")
    except Exception as exc:  # noqa: BLE001 - the last line of the guarantee
        log.warning("decomposition failed outright: %s", exc)
        yield Event(type="abort", reason="Rozdělení se nepovedlo.")
    finally:
        await agen.aclose()


def parts_json(breakdown: Breakdown | None) -> str | None:
    """The fan-out as a JSON string for the message row, or None when it never ran.

    Stejný tvar, jaký stream posílá v události `part_done`, aby se karta po
    reloadu nepromlěnila v něco jiného, než co uživatel viděl živě.

    Ukládá se i část, která se nepovedla. Rozpad na části je jediná věc, kterou
    uživatel na výsledku nemůže zpětně poznát — „tři modely ze čtyř“ je jiná
    informace než „tři modely“, a druhé čtení odpovědi je právě ta chvíle, kdy
    na tom záleží.
    """
    if breakdown is None or not breakdown.results:
        return None
    return json.dumps(
        [
            {
                "index": r.subtask.index,
                "title": r.subtask.title,
                "provider_id": r.provider_id,
                "display_name": r.display_name,
                "model": r.model,
                "ok": r.ok,
                "ms": r.ms,
            }
            for r in breakdown.results
        ],
        ensure_ascii=False,
    )


def system_message(breakdown: Breakdown) -> dict | None:
    """Turns the parts into the system turn that precedes the user's own words.

    Returns None when fewer than two parts came back — see invariant 3 in the
    module docstring.
    """
    if not breakdown.usable:
        return None

    parts = [
        "Úkol byl rozdělený na části a každou zpracoval jiný model, nezávisle "
        "na ostatních. Jejich výstupy máš níž. Tvoje práce je složit z nich "
        "jednu souvislou odpověď."
    ]

    for result in breakdown.done:
        body = result.text
        if len(body) > _RESULT_CHARS:
            body = body[:_RESULT_CHARS] + "\n…(zkráceno)"
        parts.append(
            f"### Část {result.subtask.index}: {result.subtask.title}\n"
            f"(zpracoval {result.display_name})\n\n{body}"
        )

    if breakdown.failed:
        missing = ", ".join(f"„{r.subtask.title}“" for r in breakdown.failed)
        parts.append(
            f"Tyto části se nepodařilo zpracovat: {missing}. Co měly pokrýt, "
            "buď doplň sám, nebo napiš, že to chybí — nepředstírej, že tam ta "
            "informace je."
        )

    parts.append(
        "Části jsou podklad, ne odpověď. Slož je do jednoho celku, ne za sebe "
        "jako kapitoly. Když si dvě části odporují, rozhodni která má pravdu a "
        "řekni proč — rozpor je ta nejcennější věc, kterou ti rozdělení dalo. "
        "Vypusť, co se opakuje nebo s otázkou nesouvisí.\n"
        "Odpovídej na původní zprávu uživatele tak, jak ji napsal, a ve stejném "
        "jazyce. O rozdělení, o částech ani o modelech se nezmiňuj — uživatel "
        "chce odpověď, ne zprávu o postupu."
    )
    return {"role": "system", "content": "\n\n".join(parts)}
