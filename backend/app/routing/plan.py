"""Stage one of the router: a small fast model reads the request and decides
what kind of job it is before a second model answers it.

It returns five things — which capability the task needs, how hard it is, what
the user is actually after, a set of directives for the answering model, and
whether the answer depends on facts only the live web can supply. The
capability and complexity steer `selector.candidates()`; the goal and
directives are handed to the executor as extra context; `needs_search` is what
the globe control's "auto" position defers to (see `routing/research.py`).

**The user's message is never rewritten.** The planner's output travels as a
*system* message and the original text is passed through verbatim as the user
turn. That boundary is the whole safety story of this module: a planner that
paraphrases badly would silently change what was asked, and the user would have
no way to tell. Augment, never replace. Do not "optimise" this away.

**make_plan() never raises.** Timeout, malformed JSON, a provider that went
down, no eligible planner at all — every path ends in the heuristic plan built
from `classifier.classify()`. The planner is an upgrade to routing, not a
dependency of it; an outage here must cost latency, never an answer.

Not every provider can be the planner: it needs JSON mode. Cohere does not have
it (probed against litellm 1.98.0 on 2026-08-28), so it is filtered out here
automatically and stays an executor.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass

import litellm

from app.providers import keystore, model_health
from app.providers.registry import CAPABILITIES, ProviderModel
from app.routing import selector
from app.routing.classifier import classify
from app.routing.effort import EffortProfile

log = logging.getLogger(__name__)

COMPLEXITIES = ("trivial", "moderate", "hard")

# "search" is a capability providers advertise, not one the planner may assign:
# whether to search is the separate `needs_search` flag, and the answer itself
# is always written by a normal model. Without this, a planner that returned
# capability="search" would pin the answer onto a websearch agent.
ANSWER_CAPABILITIES = tuple(c for c in CAPABILITIES if c != "search")

# Two providers, then give up and use the heuristic. The planner sits in front
# of every answer, so an unbounded walk down the chain would turn one dead
# provider into seconds of dead air before a single token appears.
_MAX_PLANNER_ATTEMPTS = 2
_PLANNER_TIMEOUT = 12

# Hard ceiling on the whole planning stage, attempts and all. Measured on
# 2026-08-28: two providers failing in sequence cost 14.6s of silence before
# the first token. Whatever goes wrong downstream, planning gives up here and
# the heuristic takes over.
_PLANNER_BUDGET = 18

# Groq's gpt-oss models spend part of this budget on internal reasoning tokens,
# so 500 was not enough for a hard task — the JSON came back truncated and the
# whole plan was thrown away. Verified 2026-08-28.
_PLANNER_MAX_TOKENS = 1200

# History the planner sees. It only has to understand the *shape* of the task,
# so a few recent turns are plenty and each one gets clipped.
_HISTORY_TURNS = 6
_HISTORY_CHARS = 1200
_MESSAGE_CHARS = 4000

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class Plan:
    capability: str  # one of registry.CAPABILITIES
    complexity: str  # one of COMPLEXITIES
    goal: str  # one sentence in the user's language, may be empty
    directives: str  # extra instructions for the executor, may be empty
    source: str  # "model" | "heuristic"
    planner_model: str | None = None
    # Whether the answer depends on facts the model cannot have. Only ever
    # True on a model plan: the heuristic has no way to tell a dated question
    # from a timeless one, so the `light` tier stays offline by construction.
    needs_search: bool = False


_SYSTEM_PROMPT = """You are the routing planner of a multi-provider chat app.
You do NOT answer the user. You analyse their latest message and decide how it
should be handled, then hand off to a second model that writes the answer.

Reply with a single JSON object and nothing else. Keys:

"capability": which kind of model fits best. Exactly one of:
  - "code"          programming, debugging, regex, SQL, shell, config files
  - "creative"      stories, poems, marketing copy, brainstorming, tone work
  - "long_context"  the task hinges on a large pasted document or long history
  - "fast"          short factual questions, quick lookups, simple chit-chat
  - "general"       everything else, including reasoning and explanation

"complexity": how much work the answer needs. Exactly one of:
  - "trivial"   one or two sentences would fully answer it
  - "moderate"  a normal explanation or a self-contained piece of code
  - "hard"      multi-step reasoning, tricky trade-offs, or a large deliverable

"needs_search": true only if answering correctly requires information that
changes over time and that a language model cannot know from training — news,
prices, releases, versions, standings, schedules, "current"/"latest" anything,
or any question about a dated event. Use false for timeless tasks: writing
code, explaining a concept, editing text, maths, opinions. When in doubt about
a fact that would be embarrassing to get stale, prefer true.

"goal": one sentence naming what the user actually wants, written in the SAME
LANGUAGE as their message. Say the underlying intent, not a restatement of the
words.

"directives": concrete instructions for the answering model, in the same
language as the user's message. Say what to include, what format to use, what
depth to go to, and which specific mistakes to avoid on THIS task. Never repeat
the user's question and never write any part of the answer yourself.

"needs_search" is a JSON boolean; the other four are plain JSON strings. Do not
use arrays, nested objects or markdown. Keep "directives" under 600 characters.

Judge the task, not the user. If the message is vague, say so in "directives"
and tell the answering model to state its assumption instead of guessing."""

_BRIEF = '\nKeep "directives" to at most two sentences.'
_FULL = (
    '\nWrite "directives" as three to six specific, actionable sentences in one '
    'string separated by spaces. Generic advice like "be clear and accurate" is '
    "worthless — name the concrete pitfalls of this particular task."
)


def heuristic_plan(message: str, history_chars: int = 0) -> Plan:
    """The fallback, and the whole of the `light` tier.

    Complexity is deliberately "moderate": without a model reading the request
    there is nothing trustworthy to base a harder guess on, and "moderate"
    leaves the selector's proven capability ordering untouched.
    """
    return Plan(
        capability=classify(message, history_chars=history_chars),
        complexity="moderate",
        goal="",
        directives="",
        source="heuristic",
    )


def _can_do_json(model: str) -> bool:
    try:
        params = litellm.get_supported_openai_params(model=model) or ()
    except Exception:  # noqa: BLE001 - unknown slug, treat as unusable
        return False
    return "response_format" in params


def _planner_slug(provider: ProviderModel) -> str | None:
    """First live slug of this provider that supports JSON mode."""
    for model in model_health.usable_models(provider.models):
        if _can_do_json(model):
            return model
    return None


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


def _parse(raw: str) -> dict:
    """JSON mode is a strong hint, not a guarantee.

    Observed on 2026-08-28: Groq wraps clean JSON, Gemini occasionally prefixes
    or suffixes it with prose. So try the whole string first, then fall back to
    the outermost braces.
    """
    text = _FENCE.sub("", raw.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _OBJECT.search(text)
        if match is None:
            raise
        return json.loads(match.group(0))


def _as_text(value) -> str:
    """Flattens whatever the planner put in a string field.

    Models hand back a JSON array for "directives" often enough that `str()`
    would put Python list syntax — brackets, quotes and all — straight into the
    executor's system prompt. Seen on Groq gpt-oss on 2026-08-28.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(v) for v in value if v).strip()
    if isinstance(value, dict):
        return " ".join(_as_text(v) for v in value.values() if v).strip()
    return str(value).strip()


def _as_bool(value) -> bool:
    """JSON mode does not stop a model from writing "true" as a string.

    Anything unrecognised is False: reaching the internet costs latency and a
    slice of a daily quota, so an ambiguous answer should not spend one.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "ano", "1")
    return value == 1


def _build(data: dict, model: str, fallback: Plan) -> Plan:
    """Validates the planner's answer field by field.

    Anything the planner got wrong falls back to the heuristic value rather
    than poisoning the selector with an unknown capability tag.
    """
    capability = data.get("capability")
    complexity = data.get("complexity")
    return Plan(
        capability=(
            capability if capability in ANSWER_CAPABILITIES else fallback.capability
        ),
        complexity=complexity if complexity in COMPLEXITIES else "moderate",
        goal=_as_text(data.get("goal"))[:400],
        directives=_as_text(data.get("directives"))[:2000],
        source="model",
        planner_model=model,
        needs_search=_as_bool(data.get("needs_search")),
    )


async def _ask_planner(
    messages: list[dict], profile: EffortProfile, fallback: Plan
) -> Plan:
    system = _SYSTEM_PROMPT + (_FULL if profile.plan_detail == "full" else _BRIEF)
    attempts = 0

    for provider in selector.planner_candidates():
        if attempts >= _MAX_PLANNER_ATTEMPTS:
            break
        model = _planner_slug(provider)
        if model is None:
            continue  # no JSON mode here (Cohere) — not an attempt, just skip
        attempts += 1
        try:
            response = await litellm.acompletion(
                model=model,
                api_key=keystore.get_key(provider.credential_id),
                messages=[{"role": "system", "content": system}, *_condense(messages)],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=_PLANNER_MAX_TOKENS,
                timeout=_PLANNER_TIMEOUT,
            )
            raw = response.choices[0].message.content or ""
            return _build(_parse(raw), model, fallback)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
            log.warning("planner %s (%s) failed: %s", provider.id, model, exc)
            continue

    return fallback


async def make_plan(
    messages: list[dict], profile: EffortProfile, history_chars: int = 0
) -> Plan:
    """Stage-one routing decision. Never raises — see the module docstring."""
    last_user = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    fallback = heuristic_plan(last_user, history_chars=history_chars)

    if not profile.use_planner:
        return fallback

    try:
        return await asyncio.wait_for(
            _ask_planner(messages, profile, fallback), timeout=_PLANNER_BUDGET
        )
    except asyncio.TimeoutError:
        log.warning("planning budget of %ss exhausted, using heuristic", _PLANNER_BUDGET)
        return fallback
    except Exception as exc:  # noqa: BLE001 - the last line of the guarantee
        log.warning("planning failed outright: %s", exc)
        return fallback


def system_message(plan: Plan) -> dict | None:
    """Turns a plan into the system turn that precedes the user's own words.

    Returns None for a heuristic plan: there is nothing useful to say, and an
    empty scaffold would only burn tokens and dilute the user's message.
    """
    if plan.source != "model" or not (plan.goal or plan.directives):
        return None

    parts = [
        "Jsi výkonný model v dvoustupňovém routeru. "
        "Předchozí model úkol analyzoval; jeho rozbor máš níž."
    ]
    if plan.goal:
        parts.append(f"Co uživatel skutečně chce: {plan.goal}")
    if plan.directives:
        parts.append(f"Jak na to: {plan.directives}")
    parts.append(
        "Rozbor je vodítko, ne zadání. Odpovídej na původní zprávu uživatele "
        "tak, jak ji napsal, a ve stejném jazyce. O rozboru se nezmiňuj."
    )
    return {"role": "system", "content": "\n\n".join(parts)}
