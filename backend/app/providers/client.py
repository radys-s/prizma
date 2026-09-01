"""Stage two of the router: streams the answer, walking the selector's
providers and each provider's candidate model slugs until one produces output.

Two rules shape the fallback:

* **Before any content has streamed, every failure is retryable.** A 404 from a
  retired model, a 429, an unverified-billing 402 — none of them are the user's
  problem if some other provider can answer. The old code surfaced the raw
  LiteLLM exception for anything that wasn't a rate limit, which is how a
  retired Groq model turned into a stack trace in the chat window.
* **Once a provider has started streaming, errors are surfaced.** Splicing a
  different model's continuation onto a half-finished answer reads as garbled
  nonsense.

Three things ride on top of that chain:

* A **pinned** provider (the user's manual pick) goes to the front but does not
  replace the chain. If it can't answer, someone else does and the UI says so —
  `note` on the meta event carries that sentence.
* The **max** tier runs a second, reviewing pass over the finished draft. It is
  wrapped so that a failed review can never destroy an answer the user has
  already read: on any error the draft stands.
* **Findings** from `routing/research.py`, when the request touched the web.
  They arrive here already gathered — this module never searches, it only
  hands the digest and its numbered sources to the executor.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import litellm

from app import settings_store
from app.providers import keystore, model_health
from app.providers.registry import BY_ID, ProviderModel
from app.routing import plan as planning
from app.routing import research as researching
from app.routing import selector
from app.routing.effort import EffortProfile

# Errors that mean "this provider is temporarily busy" — park it so the next
# request doesn't queue behind it again.
_COOLDOWN_ERRORS = (
    litellm.RateLimitError,
    litellm.APIConnectionError,
    litellm.Timeout,
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
)

# Errors that mean "this exact model slug is gone" — park the slug, not the
# provider, and move to the next candidate.
_DEAD_MODEL_ERRORS = (litellm.NotFoundError,)

_STREAM_TIMEOUT = 60
_REVIEW_TIMEOUT = 90

log = logging.getLogger(__name__)


@dataclass
class StreamEvent:
    # "meta"        provider + model chosen, answer is about to start
    # "delta"       a chunk of answer text
    # "review_start" the draft is done; a revised version is about to stream
    #                in its place (max tier only)
    # "review_abort" the revision failed; the draft the user already sees stands
    # "error"       nothing usable came back
    # "done"        finished
    type: str
    provider_id: str | None = None
    model_name: str | None = None
    content: str = ""
    note: str = ""  # user-facing aside, e.g. "Groq nedostupný, odpověděl Gemini"
    revised: bool = False
    # Total tokens the provider reported for this answer, 0 when it reported
    # none. Plenty of free-tier endpoints omit usage on a stream entirely, so
    # this is a bonus number and never something the UI may depend on.
    tokens: int = 0


@dataclass
class Attempt:
    provider: ProviderModel
    model: str
    error: Exception


def _short(provider: ProviderModel) -> str:
    """Display name without the model in parentheses — for one-line notes."""
    return provider.display_name.split(" (")[0]


def _humanize(attempt: Attempt) -> str:
    """One readable line per failed attempt, in the user's language."""
    name = attempt.provider.display_name
    exc = attempt.error
    if isinstance(exc, litellm.AuthenticationError):
        return f"{name}: klíč byl odmítnut."
    if isinstance(exc, litellm.NotFoundError):
        return f"{name}: model {attempt.model} už neexistuje."
    if isinstance(exc, litellm.RateLimitError):
        return f"{name}: vyčerpaný limit."
    if isinstance(exc, (litellm.Timeout, litellm.APIConnectionError)):
        return f"{name}: nepodařilo se spojit."
    if isinstance(exc, (litellm.ServiceUnavailableError, litellm.InternalServerError)):
        return f"{name}: provider je momentálně přetížený."
    text = str(exc)
    if "payment" in text.lower() or "billing" in text.lower():
        return f"{name}: účet vyžaduje ověřenou platební kartu."
    return f"{name}: {text[:160]}"


def _summarize(attempts: list[Attempt]) -> str:
    if not attempts:
        return "Nepodařilo se získat odpověď od žádného providera."
    lines = "\n".join(f"- {_humanize(a)}" for a in attempts)
    return f"Žádný provider neodpověděl.\n\n{lines}"


def _supported_kwargs(model: str, **kwargs) -> dict:
    """Keeps only the params this slug actually accepts.

    `litellm.drop_params` would do something similar globally, but silently —
    and it would also swallow `response_format` on the planner call, where a
    dropped param means garbage instead of JSON. Filtering per call is
    deterministic and stays local to the request that asked for it.

    Probed 2026-08-28: `reasoning_effort` reaches Groq gpt-oss, Gemini Flash
    and Cerebras; every other slug in the registry silently loses it here,
    which is intended — the effort tier still shapes the planner's directives.
    """
    try:
        allowed = set(litellm.get_supported_openai_params(model=model) or ())
    except Exception:  # noqa: BLE001 - unknown slug: send nothing optional
        return {}
    return {k: v for k, v in kwargs.items() if v is not None and k in allowed}


def _collect_usage(chunk, usage: dict) -> None:
    """Adds a chunk's usage to the running total, if it carried any.

    Only the final chunk of a stream has it, and only when the provider both
    supports `stream_options` and chose to honour it. Written as a running sum
    rather than an assignment because the review pass streams a second time
    into the same sink and the two together are what the answer cost.
    """
    reported = getattr(chunk, "usage", None)
    total = getattr(reported, "total_tokens", None) if reported else None
    if isinstance(total, int) and total > 0:
        usage["total"] = usage.get("total", 0) + total


async def _try_stream(
    provider: ProviderModel,
    model: str,
    messages: list[dict],
    profile: EffortProfile,
    usage: dict,
) -> AsyncIterator[str]:
    api_key = keystore.get_key(provider.credential_id)
    response = await litellm.acompletion(
        model=model,
        api_key=api_key,
        messages=messages,
        stream=True,
        timeout=_STREAM_TIMEOUT,
        **_supported_kwargs(
            model,
            reasoning_effort=profile.reasoning_effort,
            max_tokens=profile.max_tokens,
            # Asks for a usage block on the last chunk. Dropped silently by
            # `_supported_kwargs` for any slug that does not take it, which is
            # why the token count in the UI is optional rather than promised.
            stream_options={"include_usage": True},
        ),
    )
    async for chunk in response:
        _collect_usage(chunk, usage)
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


_REVIEW_SYSTEM = (
    "Jsi recenzent. Dostaneš dotaz uživatele a návrh odpovědi od jiného modelu. "
    "Najdi v návrhu faktické chyby, chybějící kroky, nepřesnosti a zbytečnou "
    "vatu — a vrať opravenou finální odpověď.\n\n"
    "Vracíš POUZE tu finální odpověď, ve stejném jazyce jako dotaz. Žádný "
    "komentář k revizi, žádné „opravil jsem“, žádný seznam změn. Pokud je "
    "návrh správný, vrať ho prakticky beze změny — nepřepisuj ho jen proto, "
    "aby to vypadalo, že ses snažil."
)

# Appended only when the answer was written against web findings. Without it
# the reviewer treats "[2]" as a typo and strips the citations out of an
# answer whose whole value was that it had them.
_REVIEW_SOURCES = (
    "\n\nOdpověď vznikla z webové rešerše a čísla v hranatých závorkách, jako "
    "[1] nebo [2], jsou odkazy na zdroje. Zachovej je přesně tam, kde jsou, a "
    "žádná nová nepřidávej — seznam zdrojů už nerozšíříš."
)


async def _stream_review(
    provider: ProviderModel,
    model: str,
    question: str,
    draft: str,
    plan: planning.Plan,
    profile: EffortProfile,
    cited: bool = False,
    usage: dict | None = None,
) -> AsyncIterator[str]:
    """Second pass of the `max` tier, on the model that wrote the draft."""
    context = f"Dotaz uživatele:\n{question}\n\nNávrh odpovědi:\n{draft}"
    if plan.directives:
        context = f"Zadání pro odpovídající model:\n{plan.directives}\n\n{context}"

    response = await litellm.acompletion(
        model=model,
        api_key=keystore.get_key(provider.credential_id),
        messages=[
            {
                "role": "system",
                "content": _REVIEW_SYSTEM + (_REVIEW_SOURCES if cited else ""),
            },
            {"role": "user", "content": context},
        ],
        stream=True,
        timeout=_REVIEW_TIMEOUT,
        **_supported_kwargs(
            model,
            reasoning_effort=profile.reasoning_effort,
            max_tokens=profile.max_tokens,
            stream_options={"include_usage": True},
        ),
    )
    async for chunk in response:
        if usage is not None:
            _collect_usage(chunk, usage)
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


def _custom_instructions() -> dict | None:
    """The user's standing instructions as a system turn, if they set any.

    Read per request rather than cached: the settings dialog can change it
    between two messages in the same conversation, and the next answer should
    already obey. `settings_store` does its own in-process caching.
    """
    text = settings_store.get_text(
        settings_store.CUSTOM_INSTRUCTIONS, settings_store.CUSTOM_INSTRUCTIONS_MAX
    )
    if not text:
        return None
    return {
        "role": "system",
        "content": (
            "Uživatel má nastavené trvalé pokyny, jak chce odpovídat. Drž se "
            "jich ve všech odpovědích, pokud si v konkrétní zprávě neřekne "
            f"jinak:\n\n{text}"
        ),
    }


async def stream_chat(
    messages: list[dict],
    plan: planning.Plan,
    profile: EffortProfile,
    pinned: str | None = None,
    findings: researching.Findings | None = None,
) -> AsyncIterator[StreamEvent]:
    providers = selector.candidates(plan.capability, plan.complexity, pinned=pinned)
    if not providers:
        raise selector.NoProviderAvailable(plan.capability)

    # The user's standing instructions, the planner's analysis and any web
    # findings ride in front as system turns, in that order — who to be, how to
    # approach the task, then what is known about it. Custom instructions lead
    # because they are the only one the user wrote: the planner should not be
    # able to talk the model out of "always answer in English".
    #
    # The user's own message stays exactly as they typed it, always; see the
    # docstrings of routing/plan.py and routing/research.py.
    preamble = [
        m
        for m in (
            _custom_instructions(),
            planning.system_message(plan),
            researching.system_message(findings) if findings else None,
        )
        if m is not None
    ]
    outgoing = [*preamble, *messages]

    question = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    pinned_provider = BY_ID.get(pinned) if pinned else None
    attempts: list[Attempt] = []
    usage: dict = {}

    for provider in providers:
        for model in model_health.usable_models(provider.models):
            started = False
            draft = ""
            try:
                async for delta in _try_stream(provider, model, outgoing, profile, usage):
                    if not started:
                        # Announce the model only once it has actually produced
                        # output, so the UI never labels an answer with a model
                        # that turned out to be dead.
                        started = True
                        note = ""
                        if pinned_provider is not None and provider.id != pinned_provider.id:
                            note = (
                                f"{_short(pinned_provider)} nedostupný, "
                                f"odpověděl {_short(provider)}"
                            )
                        yield StreamEvent(
                            type="meta",
                            provider_id=provider.id,
                            model_name=model,
                            note=note,
                        )
                    draft += delta
                    yield StreamEvent(type="delta", provider_id=provider.id, content=delta)

                if not started:
                    # Clean finish with zero content — treat as a failed attempt
                    # so we fall through instead of showing an empty answer.
                    attempts.append(
                        Attempt(provider, model, RuntimeError("prázdná odpověď"))
                    )
                    continue

                revised = False
                if profile.review_pass and draft.strip():
                    produced = aborted = False
                    cited = bool(findings and findings.sources)
                    async for ev in _run_review(
                        provider, model, question, draft, plan, profile, cited, usage
                    ):
                        produced = produced or ev.type == "delta"
                        aborted = aborted or ev.type == "review_abort"
                        yield ev
                    # A review that broke off halfway still ends in an abort,
                    # and the abort restores the draft — so it isn't "revised".
                    revised = produced and not aborted

                yield StreamEvent(
                    type="done",
                    provider_id=provider.id,
                    model_name=model,
                    revised=revised,
                    tokens=usage.get("total", 0),
                )
                return

            except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
                if started:
                    yield StreamEvent(
                        type="error", provider_id=provider.id, content=_humanize(
                            Attempt(provider, model, exc)
                        )
                    )
                    return

                attempts.append(Attempt(provider, model, exc))
                if isinstance(exc, _DEAD_MODEL_ERRORS):
                    model_health.mark_dead(model)
                    continue  # same provider, next slug
                if isinstance(exc, _COOLDOWN_ERRORS):
                    selector.mark_cooldown(provider.id)
                break  # provider-level problem — stop trying its other slugs

    yield StreamEvent(type="error", content=_summarize(attempts))


async def _run_review(
    provider: ProviderModel,
    model: str,
    question: str,
    draft: str,
    plan: planning.Plan,
    profile: EffortProfile,
    cited: bool = False,
    usage: dict | None = None,
) -> AsyncIterator[StreamEvent]:
    """Wraps the review pass so it can only ever add value, never subtract it.

    `review_start` tells the UI to park the draft and clear the bubble; every
    later `delta` builds the revised answer in its place. If anything goes
    wrong — timeout, rate limit, an empty response — `review_abort` puts the
    draft back. The user keeps the answer they already read either way.
    """
    yield StreamEvent(type="review_start", provider_id=provider.id, model_name=model)
    produced = False
    try:
        async for delta in _stream_review(
            provider, model, question, draft, plan, profile, cited, usage
        ):
            produced = True
            yield StreamEvent(type="delta", provider_id=provider.id, content=delta)
    except Exception as exc:  # noqa: BLE001 - a failed review is not a failed answer
        # Swallowed for the user, but not for us: an always-aborting review pass
        # would otherwise look identical to a review that decided to change
        # nothing, and the `max` tier would quietly stop being worth its name.
        log.warning("review pass failed on %s: %s: %s", model, type(exc).__name__, exc)
        yield StreamEvent(
            type="review_abort", provider_id=provider.id, content="Revize se nepovedla."
        )
        return

    if not produced:
        yield StreamEvent(
            type="review_abort", provider_id=provider.id, content="Revize nic nevrátila."
        )
