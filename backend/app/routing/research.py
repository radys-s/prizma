"""Stage 1.5 of the router: reads the internet before the answer is written.

**Searching is a pre-stage, not a streaming concern.** Every call in here is
non-streaming, and what comes back is structured data that rides into the
executor as a system message — exactly the shape `plan.py` already uses. That
is not a stylistic choice:

* litellm drops Gemini's `groundingMetadata` entirely when `stream=True`
  (BerriAI/litellm#10237), and Groq's `executed_tools` is no more reliable
  mid-stream. Sources scraped off a token stream would be missing or partial.
* The user keeps one uninterrupted stream for the *answer*, which is the only
  part worth watching arrive.

Two depths:

* ``web``  — one search, then answer.
* ``deep`` — decompose into sub-questions, search them concurrently, hand the
  executor everything at once. This is the mode meant to beat a free research
  tool: several independent searches beat one, and the sub-questions come from
  a model that has read the request rather than from the raw query string.

**gather() never raises.** Same contract as `make_plan`: a dead searcher costs
a caveat, never an answer. Total failure returns None and the answer is written
from model weights with the UI saying so; a partial failure sets `partial` and
the executor is told which ground it could not cover.

**The findings augment, never replace.** The user's message is passed through
verbatim as always, and the digest is context the executor may use, contradict
or ignore — search results are frequently wrong, and a model forced to trust
them is worse than one that can say the sources disagree. Do not "optimise"
this into a summarise-the-search-results prompt.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace

import litellm

from app import settings_store
from app.providers import keystore, model_health
from app.providers.registry import ProviderModel
from app.routing import plan as planning
from app.routing import selector
from app.routing import websearch
from app.routing.effort import EffortProfile

log = logging.getLogger(__name__)

# What the user's globe control can be set to. "auto" defers to the planner's
# `needs_search`; "off" means the web is never touched.
MODES = ("auto", "web", "deep", "off")
DEFAULT_MODE = "auto"

_SEARCH_TIMEOUT = 25  # one search call
# The whole deep run, decomposition included. Raised from 90 when page reading
# landed: reading the top three sources adds 10-15s and it is the part of a
# deep run that actually beats a snippet-only research tool, so the budget
# moved rather than the feature.
_RESEARCH_BUDGET = 120
# Reading gets its own ceiling *inside* that budget. Without it one slow page
# could eat every second the searches left behind.
_READ_BUDGET = 20
_SUBQUESTION_TIMEOUT = 15

# Five, not six. Groq's free tier is 30 requests/minute and one research run
# must not eat two thirds of a minute's quota — the answer and a possible
# review pass still have to fit through the same door.
_MAX_SUBQUESTIONS = 5

# …and five only when there are enough accounts to actually search them. Free
# tiers meter tokens per minute: measured on 2026-08-29 a single Groq key fits
# about two Compound searches into one minute, so a five-way split on one key
# produces four sub-questions that 429 and one that answers. Asking for two
# instead is not a smaller research run — it is the same run without the four
# queries that were never going to return anything, and the progress line
# stops promising coverage the quota cannot deliver.
_SUBQUESTIONS_PER_SLOT = 2

_MAX_SOURCES = 12
_SEARCH_MAX_TOKENS = 1400
_DIGEST_CHARS = 2500  # per search; the executor's context is not infinite
_QUESTION_CHARS = 2000

_SEARCH_SYSTEM = (
    "Jsi rešeršní asistent. Vyhledej na webu, co je potřeba k zodpovězení "
    "dotazu, a stručně shrň, cos zjistil.\n\n"
    "Piš fakta, ne úvahy: konkrétní čísla, jména, verze a data. U každého "
    "údaje uveď, odkud je. Když se zdroje rozcházejí, napiš to. Když se nic "
    "použitelného najít nedá, napiš přímo, že to web neuvádí — nedomýšlej si.\n\n"
    "Nepiš finální odpověď uživateli a neoslovuj ho. Tvůj výstup čte jiný "
    "model, který odpověď teprve napíše."
)

# `{max_queries}` is filled from `_subquestion_budget()` — telling the model the
# real ceiling beats generating five and throwing three away, because a model
# asked for three picks the three that matter rather than the first three of
# five it had already planned around.
_SUBQUESTION_SYSTEM = """You split a research request into independent web searches.

Reply with a single JSON object: {{"queries": ["…", "…"]}}

Rules:
- At most {max_queries} queries. Fewer is better than padding with near-duplicates.
- Each query must be answerable on its own by a web search, and must cover a
  DIFFERENT part of the request — a fact, a counter-position, a date, a number.
- Write each query in the language most likely to have good sources on it,
  which for technical or international topics usually means English.
- Queries only. No commentary, no numbering, no explanation."""


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    #: Provider-supplied favicon URL, empty for the searchers that return none.
    #: The UI falls back to a hostname monogram — it never asks a third-party
    #: favicon service, which would hand the user's browsing to a CDN.
    favicon: str = ""


@dataclass(frozen=True)
class Findings:
    """What the search stage learned. Empty `sources` is legal — some searches
    genuinely return prose with nothing citable, and the digest still helps."""

    queries: list[str]
    digest: str
    sources: list[Source] = field(default_factory=list)
    provider_id: str | None = None
    model: str | None = None
    # True when at least one sub-question failed. The executor is told, so the
    # answer can say what it could not check instead of quietly having a hole.
    partial: bool = False


def resolve_mode(requested: str | None, needs_search: bool) -> str:
    """Turns the picker value plus the planner's opinion into one depth.

    The explicit modes win outright — a user who pressed "deep" gets deep even
    if the planner thought the question was trivial, and "off" is honoured even
    when the planner is sure the web is needed. Only "auto" delegates.
    """
    mode = requested if requested in MODES else DEFAULT_MODE
    if mode == "auto":
        return "web" if needs_search else "off"
    return mode


def _is_compound(model: str) -> bool:
    """Groq's compound systems search on their own initiative.

    They take no search parameters at all — the slug *is* the feature — so
    passing `web_search_options` at them is at best ignored and at worst a 400.
    Everyone else needs to be told to search.
    """
    return "compound" in model


def _search_kwargs(model: str, profile: EffortProfile) -> dict:
    if _is_compound(model):
        return {}
    size = "high" if profile.plan_detail == "full" else "medium"
    try:
        allowed = set(litellm.get_supported_openai_params(model=model) or ())
    except Exception:  # noqa: BLE001 - unknown slug: send nothing optional
        return {}
    if "web_search_options" not in allowed:
        return {}
    return {"web_search_options": {"search_context_size": size}}


def searchers() -> list[tuple[ProviderModel, str]]:
    """Configured (provider, slug) pairs that can actually reach the web.

    Ordered by `selector.candidates("search")`, so a missing Groq key degrades
    to Gemini's Google Search grounding without anything else changing. A slug
    that has to be *told* to search is only usable if litellm knows how to tell
    it — that is what the `web_search_options` check is for.
    """
    out: list[tuple[ProviderModel, str]] = []
    for provider in selector.candidates("search"):
        if "search" not in provider.capabilities:
            continue  # candidates() appends the whole chain; we want real searchers
        if provider.search_backend:
            # A native search API. Its `models` tuple is a display label, not a
            # litellm slug, so it must never reach the probe below — asking
            # litellm about "tavily:search" throws, the probe returns {}, and
            # the entry would be silently dropped from the chain. That is
            # exactly the bug that made a valid Tavily key do nothing.
            out.append((provider, provider.primary_model))
            continue
        for model in model_health.usable_models(provider.models):
            if _is_compound(model) or _search_kwargs(model, _ANY_PROFILE):
                out.append((provider, model))
    return out


def _extract_text(response) -> str:
    try:
        return (response.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _walk(node, found: list[Source], seen: set[str], depth: int = 0) -> None:
    """Collects {title, url} pairs out of whatever shape the provider used.

    Deliberately structural rather than provider-specific. Groq returns them
    under `executed_tools[n].search_results`, Gemini under grounding chunks in
    `_hidden_params`, and both shapes have changed before. Walking for dicts
    that carry a URL survives that; a hardcoded path does not.
    """
    if depth > 6 or len(found) >= _MAX_SOURCES:
        return
    if isinstance(node, str):
        # Groq hands back `search_results` as a JSON *string* — verified
        # 2026-08-29, the value is '{"results": [{"title", "url", "content"}]}'.
        # Without this the walk stops at the string and finds nothing at all.
        # Only obvious JSON is parsed: page content is also a string, and
        # trying to decode every one of those would be wasted work.
        text = node.lstrip()
        if text[:1] in ("{", "[") and len(text) < 400_000:
            try:
                _walk(json.loads(text), found, seen, depth + 1)
            except (ValueError, TypeError):
                pass
        return
    if isinstance(node, dict):
        url = node.get("url") or node.get("uri") or node.get("link")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            if url not in seen:
                seen.add(url)
                title = node.get("title") or node.get("name") or node.get("domain")
                found.append(
                    Source(title=str(title).strip()[:200] if title else url, url=url)
                )
            return  # a source node's children are page content, not more sources
        for value in node.values():
            _walk(value, found, seen, depth + 1)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _walk(value, found, seen, depth + 1)


def _harvest_sources(response) -> list[Source]:
    found: list[Source] = []
    seen: set[str] = set()
    for candidate in _source_bearing_parts(response):
        _walk(candidate, found, seen)
    return found[:_MAX_SOURCES]


def _source_bearing_parts(response) -> list:
    """The few places either provider is known to hide citations.

    Kept narrow on purpose: walking the entire response object would also drag
    in the prompt echo and any URL the user themselves pasted.
    """
    parts = []
    try:
        message = response.choices[0].message
        for attr in ("executed_tools", "tool_calls", "annotations", "citations"):
            value = getattr(message, attr, None)
            if value:
                parts.append(_plain(value))
    except Exception:  # noqa: BLE001
        pass
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        for key, value in hidden.items():
            if "grounding" in key or "citation" in key or "search" in key:
                parts.append(_plain(value))
    return parts


def _plain(value):
    """Pydantic models and SimpleNamespaces into plain dicts/lists."""
    for method in ("model_dump", "dict"):
        fn = getattr(value, method, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                pass
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "__dict__") and not isinstance(value, (str, bytes)):
        return {k: _plain(v) for k, v in vars(value).items()}
    return value


async def _search_once(
    provider: ProviderModel, model: str, query: str, profile: EffortProfile
) -> tuple[str, list[Source]]:
    """One non-streaming search. Raises — callers decide what a failure means."""
    if provider.search_backend:
        digest, hits = await websearch.search(
            provider.search_backend,
            keystore.get_key(provider.credential_id),
            query[:_QUESTION_CHARS],
        )
        sources = [
            Source(title=h.title, url=h.url, favicon=h.favicon)
            for h in hits[:_MAX_SOURCES]
        ]
        return digest[:_DIGEST_CHARS], sources

    response = await litellm.acompletion(
        model=model,
        **keystore.endpoint(provider),
        messages=[
            {"role": "system", "content": _SEARCH_SYSTEM},
            {"role": "user", "content": query[:_QUESTION_CHARS]},
        ],
        stream=False,
        timeout=_SEARCH_TIMEOUT,
        max_tokens=_SEARCH_MAX_TOKENS,
        **_search_kwargs(model, profile),
    )
    return _extract_text(response)[:_DIGEST_CHARS], _harvest_sources(response)


async def _search_with_fallback(
    query: str, profile: EffortProfile, start: int = 0
) -> tuple[str, list[Source], ProviderModel | None, str | None]:
    """Walks the searcher chain until one returns text.

    `start` rotates where the walk begins without shortening it — every slug is
    still tried, just in a different order. Deep mode uses it to spread its
    concurrent sub-questions across the chain: measured on the free Groq tier
    on 2026-08-29, four sub-questions all starting at `compound-mini` put ~16k
    tokens into an 8 000 TPM window and three of the four came back 429 or
    "Request Entity Too Large". Starting each one further along the chain
    spends the quota of several slugs instead of exhausting one.
    """
    chain = searchers()
    if not chain:
        return "", [], None, None
    offset = start % len(chain)
    for provider, model in chain[offset:] + chain[:offset]:
        try:
            digest, sources = await _search_once(provider, model, query, profile)
        except Exception as exc:  # noqa: BLE001 - see module docstring
            log.warning("search failed on %s (%s): %s", provider.id, model, exc)
            status = websearch.status_of(exc)
            if isinstance(exc, litellm.RateLimitError) or status == 429:
                selector.mark_cooldown(provider.id)
            elif status in (401, 403):
                # A rejected key is not an exhausted quota. Cooling the provider
                # down here would hide a wrong key behind "try again in a
                # minute" for as long as the user keeps searching.
                log.warning("search key rejected by %s (HTTP %s)", provider.id, status)
            continue
        if digest:
            return digest, sources, provider, model
        log.warning("search returned nothing on %s (%s)", provider.id, model)
    return "", [], None, None


def _search_slots() -> set[str]:
    """Credential slots that can carry a share of a deep run's searches.

    Keyless entries are excluded on purpose. They are a floor for "the search
    stage must not come back empty", not capacity: counting Wikipedia as an
    account would let a user with *no* search key at all get a five-way split,
    and four of those five sub-questions would then be answered by an
    encyclopedia that has nothing to say about them.
    """
    return {p.credential_id for p, _ in searchers() if not p.keyless}


def _subquestion_budget() -> int:
    """How many sub-questions the connected accounts can realistically serve."""
    slots = _search_slots()
    return max(1, min(_MAX_SUBQUESTIONS, _SUBQUESTIONS_PER_SLOT * len(slots)))


async def _subquestions(question: str, profile: EffortProfile) -> list[str]:
    """Splits the request into independent searches.

    Falls back to the raw question — one search is a worse research run than
    five, but it is a far better one than none.
    """
    budget = _subquestion_budget()
    if budget < 2:
        return [question]
    for provider in selector.planner_candidates():
        model = next(
            (m for m in model_health.usable_models(provider.models) if planning._can_do_json(m)),
            None,
        )
        if model is None:
            continue
        try:
            response = await litellm.acompletion(
                model=model,
                **keystore.endpoint(provider),
                messages=[
                    {
                        "role": "system",
                        "content": _SUBQUESTION_SYSTEM.format(max_queries=budget),
                    },
                    {"role": "user", "content": question[:_QUESTION_CHARS]},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=600,
                timeout=_SUBQUESTION_TIMEOUT,
            )
            data = planning._parse(response.choices[0].message.content or "")
            raw = data.get("queries") or data.get("subquestions") or []
            queries = [planning._as_text(q) for q in raw if planning._as_text(q)]
            # Dedup case-insensitively: models love to emit the same search
            # twice with different capitalisation and call it coverage.
            unique: list[str] = []
            for q in queries:
                if q.lower() not in {u.lower() for u in unique}:
                    unique.append(q)
            if unique:
                return unique[:budget]
        except Exception as exc:  # noqa: BLE001
            log.warning("sub-question split failed on %s: %s", provider.id, exc)
            continue
    return [question]


def _merge(results: list[tuple[str, str, list[Source]]]) -> tuple[str, list[Source]]:
    """Joins per-query digests and deduplicates sources across all of them."""
    blocks: list[str] = []
    sources: list[Source] = []
    seen: set[str] = set()
    for query, digest, found in results:
        blocks.append(f"### {query}\n{digest}")
        for source in found:
            if source.url not in seen:
                seen.add(source.url)
                sources.append(source)
    return "\n\n".join(blocks), sources[:_MAX_SOURCES]


async def _run_queries(
    queries: list[str], profile: EffortProfile
) -> Findings | None:
    """Searches every query concurrently and merges what came back.

    One query is the `web` case and needs no special path — a list of one runs
    through the same code, which is the only way to be sure both depths behave
    identically when a search fails.

    Concurrency is bounded by how many *accounts* can actually be searched
    against, not by how many queries there are. Free tiers are rated per
    minute, not per connection: measured on 2026-08-29, one Groq Compound
    search costs ~4 000 tokens against an 8 000 TPM ceiling, so firing five at
    once means three of them 429 in the same instant and the run comes back
    almost empty. Letting them land in waves spends the same quota across a
    larger part of the budget window and answers roughly twice as many
    sub-questions. Two accounts, two at a time — add a key, get more.
    """
    slots = _search_slots() or {"none"}
    limiter = asyncio.Semaphore(len(slots))

    async def one(index: int, query: str):
        async with limiter:
            return query, *(await _search_with_fallback(query, profile, start=index))

    settled = await asyncio.gather(
        *(one(i, q) for i, q in enumerate(queries)), return_exceptions=True
    )

    results: list[tuple[str, str, list[Source]]] = []
    provider_id = model = None
    failed = 0
    for item in settled:
        if isinstance(item, BaseException) or not item[1]:
            failed += 1
            continue
        query, digest, found, provider, used_model = item
        results.append((query, digest, found))
        provider_id = provider_id or (provider.id if provider else None)
        model = model or used_model

    if not results:
        return None
    digest, sources = _merge(results)
    return Findings(
        queries=[q for q, _, _ in results],
        digest=digest,
        sources=sources,
        provider_id=provider_id,
        model=model,
        partial=failed > 0,
    )


def _reader_key() -> str | None:
    """The Jina key, if there is one. Also raises the reader's rate ceiling."""
    return keystore.get_key("jina")


def read_targets(findings: Findings) -> list[str]:
    """Which sources page reading would fetch. Also what the UI is told.

    Applies the same `https://` filter `websearch.read_pages()` does, and in
    the same order — filter, then slice. Getting that wrong here is not a
    cosmetic bug: this list is what the `read_start` event promises, so an
    `http://` source in the top three made the UI say "Čtu 3 stránky" while
    two were fetched.
    """
    urls = [s.url for s in findings.sources if s.url.startswith("https://")]
    return urls[: websearch.read_limit(_reader_key())]


async def _enrich(findings: Findings) -> Findings:
    """Deep mode only: fetches the top sources in full and folds them in.

    This is the step that separates Prizma's deep research from a snippet
    summariser. A search API returns two sentences per hit; the executor
    writing an answer from six of those is doing the same job as the free tool
    the user was unhappy with. Reading the pages gives it the actual text.

    Appended under each source's existing `[n]`, never substituted, so the
    citation numbering the executor is told to use stays exactly the numbering
    `_merge` produced. Never raises: a failed read costs detail, not the run.
    """
    try:
        pages = await asyncio.wait_for(
            websearch.read_pages(read_targets(findings), reader_key=_reader_key()),
            timeout=_READ_BUDGET,
        )
    except Exception as exc:  # noqa: BLE001 - reading is a bonus, never a gate
        log.warning("page reading gave up: %s", exc)
        return findings
    if not pages:
        return findings

    parts = [findings.digest]
    for i, source in enumerate(findings.sources, start=1):
        text = pages.get(source.url)
        if text:
            parts.append(f"\n### Plný text zdroje [{i}] — {source.title}\n{text}")
    return replace(findings, digest="\n".join(parts))


@dataclass
class ResearchEvent:
    # "queries" what is about to be searched — emitted before the slow part
    # "reading" the searches are in and the top pages are being fetched
    # "done"    findings are ready
    # "abort"   nothing usable; the answer is written without the web
    type: str
    queries: list[str] = field(default_factory=list)
    findings: Findings | None = None
    reason: str = ""
    #: For "reading": the URLs being fetched, so the UI can say how many.
    urls: list[str] = field(default_factory=list)


async def run(
    question: str, profile: EffortProfile, depth: str
) -> AsyncIterator[ResearchEvent]:
    """The search stage as a stream of progress events.

    A generator rather than a coroutine because deep mode can legitimately take
    a minute, and a minute of blank screen is indistinguishable from a hang.
    The `queries` event lands as soon as the decomposition is done, so the UI
    can show what is being looked up while it is being looked up.

    Never raises. Every failure path ends in an `abort` event, and an abort
    means the answer gets written from model weights — which is a worse answer,
    never a missing one.
    """
    if depth not in ("web", "deep") or not question.strip():
        return
    if not searchers():
        log.info("no configured provider can search; answering offline")
        yield ResearchEvent(
            type="abort", reason="Žádný připojený provider neumí hledat na webu."
        )
        return

    try:
        if depth == "deep":
            queries = await asyncio.wait_for(
                _subquestions(question, profile), timeout=_SUBQUESTION_TIMEOUT + 5
            )
        else:
            queries = [question]
    except Exception as exc:  # noqa: BLE001 - a failed split is still searchable
        log.warning("sub-question split gave up: %s", exc)
        queries = [question]

    yield ResearchEvent(type="queries", queries=queries)

    try:
        findings = await asyncio.wait_for(
            _run_queries(queries, profile), timeout=_RESEARCH_BUDGET
        )
    except asyncio.TimeoutError:
        log.warning("research budget of %ss exhausted", _RESEARCH_BUDGET)
        yield ResearchEvent(type="abort", reason="Hledání trvalo moc dlouho.")
        return
    except Exception as exc:  # noqa: BLE001 - the last line of the guarantee
        log.warning("research failed outright: %s", exc)
        yield ResearchEvent(type="abort", reason="Hledání se nepovedlo.")
        return

    if findings is None:
        yield ResearchEvent(type="abort", reason="Hledání nic nevrátilo.")
        return

    # Reading is deep-only and opt-out. In `web` mode it would add ~8s to every
    # quick question and cost the fast path its whole point.
    if (
        depth == "deep"
        and findings.sources
        and settings_store.get_bool(settings_store.READ_PAGES)
    ):
        targets = read_targets(findings)
        if targets:
            yield ResearchEvent(type="reading", urls=targets)
            findings = await _enrich(findings)

    yield ResearchEvent(type="done", queries=findings.queries, findings=findings)


async def gather(
    question: str, profile: EffortProfile, depth: str
) -> Findings | None:
    """`run()` collapsed to its result. For probes and anything non-streaming."""
    findings = None
    async for event in run(question, profile, depth):
        findings = event.findings or findings
    return findings


def system_message(findings: Findings) -> dict:
    """Turns findings into the system turn that precedes the user's own words."""
    numbered = "\n".join(
        f"[{i}] {s.title} — {s.url}" for i, s in enumerate(findings.sources, start=1)
    )
    parts = [
        "Před tvou odpovědí proběhla rešerše na webu. Níž je, co našla.",
        f"Zjištění:\n{findings.digest}",
    ]
    if numbered:
        parts.append(f"Zdroje:\n{numbered}")
        parts.append(
            "Fakta z rešerše cituj v textu číslem zdroje v hranatých závorkách, "
            "např. [1]. Cituj jen čísla ze seznamu výš a nevymýšlej odkazy — "
            "seznam zdrojů se uživateli zobrazí zvlášť, takže ho neopisuj."
        )
    if findings.partial:
        parts.append(
            "Část hledání se nepovedla, takže rešerše je neúplná. Kde ti data "
            "chybí, řekni to rovnou místo odhadu."
        )
    parts.append(
        "Rešerše je podklad, ne pravda. Když si zdroje odporují nebo něco "
        "nepokrývají, napiš to. Odpovídej na původní zprávu uživatele tak, jak "
        "ji napsal, a ve stejném jazyce."
    )
    return {"role": "system", "content": "\n\n".join(parts)}


def sources_json(findings: Findings | None) -> str | None:
    """Sources as a JSON string for the message row, or None when there are none."""
    if findings is None or not findings.sources:
        return None
    return json.dumps(
        [
            # `favicon` is omitted when empty rather than written as "" — most
            # searchers return none, and a column full of empty strings is
            # noise in a row that is read back whole.
            {"title": s.title, "url": s.url, **({"favicon": s.favicon} if s.favicon else {})}
            for s in findings.sources
        ],
        ensure_ascii=False,
    )


# Only ever read for its `plan_detail`, in the capability probe inside
# `searchers()` where no real profile is in scope.
_ANY_PROFILE = EffortProfile(
    id="_probe",
    label="",
    description="",
    use_planner=False,
    plan_detail="brief",
    reasoning_effort=None,
    max_tokens=1024,
    review_pass=False,
)
