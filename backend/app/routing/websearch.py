"""Native search APIs — the half of the search stage that is not an LLM.

Prizma's first two searchers were language models that happen to have a web
tool (Groq Compound, Gemini grounding). They work, but they are metered the way
*models* are metered: tokens per minute. Measured on 2026-08-29, one Compound
search costs ~4 000 tokens against Groq's free 8 000 TPM ceiling, so a single
Groq account can serve about two searches a minute — which is why deep research
on one key came back mostly `partial`.

A dedicated search API is metered per *search*, and the quotas are an order of
magnitude larger: Exa refills $10 of credit every month, Linkup gives 4 000
queries at signup plus $5 monthly, Tavily 1 000 credits a month, Serper 2 500
once, Firecrawl 1 000 a month. None of the five asks for a card. Connecting one
of them does more for deep research than any amount of scheduling cleverness on
the LLM side, so these deliberately sit at the *front* of the searcher chain.

They also return better raw material. A Compound search returns prose that a
model wrote about the results; these return the results — title, url, snippet —
so the digest handed to the executor is closer to the actual sources and the
citation numbers line up with something real.

`read_pages()` at the bottom goes one step further and fetches the top hits in
full. That is the actual difference between this and a free research tool: a
snippet says a page exists, the page says what it contains.

`search_keyless()` is the floor under all of it. Every backend above needs a
key, so with none connected the honest answer to "what does deep research do?"
used to be "nothing". Wikipedia and arXiv publish official keyless APIs that
allow anonymous use, so the search stage now always has somewhere to go — see
the block above it for why that is a floor and not a searcher.

**No key ever passes through here in the clear beyond the request itself.** The
caller reads it from the OS credential store and hands it in; nothing in this
module logs it, stores it, or puts it in an error message.
"""

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_TIMEOUT = 20.0
_MAX_RESULTS = 6
_SNIPPET_CHARS = 400
_DIGEST_CHARS = 2500

# Sent on every keyless request. Deliberately carries nothing about the person
# using the app — no e-mail, no username, no machine name.
#
# Wikimedia additionally requires a *contact URI* in the User-Agent, and means
# it: measured 2026-08-31, this string alone gets a flat 403 from every
# Wikimedia endpoint and the same string plus a URL gets a 200. That URL is the
# app owner's public identity, so it is configuration (`settings.contact_url`),
# never a guess — see `wikipedia_available()`.
_UA = "Prizma/1.0 (local BYOK chat router; single-user)"

# --- page reading ---------------------------------------------------------
#
# Jina Reader turns a URL into markdown with no key and no signup. It is the
# difference between deep research that summarises six two-sentence snippets
# and deep research that has actually read the pages — which is exactly the
# complaint about free Perplexity.
#
# It is also a third-party service that sees which URLs Prizma fetches, so it
# is a *disclosed, switchable* dependency (see `app/settings_store.py`),
# never a silent one. Off means deep research falls back to snippets.
_READER = "https://r.jina.ai/"
READ_PAGES = 3  # top hits only — the tail is rarely worth the seconds
# With a Jina key the reader's ceiling goes from 20 requests/minute to 500, so
# the cautious keyless number stops being the binding constraint and deep mode
# can afford two more pages. Public, because `research.read_targets()` has to
# promise the UI the same number this module is about to attempt.
READ_PAGES_KEYED = 5
_READ_TIMEOUT = 8.0
_PAGE_CHARS = 3000


def read_limit(reader_key: str | None) -> int:
    """How many pages a read pass will attempt, given the key situation."""
    return READ_PAGES_KEYED if reader_key else READ_PAGES


@dataclass(frozen=True)
class Hit:
    title: str
    url: str
    snippet: str = ""
    #: Provider-supplied favicon URL, when there is one. Exa and Linkup return
    #: one; Tavily, Serper and Firecrawl do not. Never fetched from a
    #: third-party favicon service — that would leak browsing to a CDN.
    favicon: str = ""


@dataclass(frozen=True)
class Backend:
    """One search API. `parse` maps its JSON onto a common shape.

    Kept as data rather than three classes because the differences really are
    only the endpoint, the auth header and where the results array lives —
    subclassing that would be three files to say the same thing.
    """

    id: str
    endpoint: str
    auth: str  # "bearer" | "x-api-key"
    #: Builds the request payload. Takes (query, limit). Sent as a JSON body on
    #: POST and as the query string on GET.
    body: Any
    #: Maps the response JSON onto (answer, hits).
    parse: Any
    #: HTTP verb. Every API here was POST until ContextWire, which is GET with
    #: query parameters — one field beats a second dataclass.
    method: str = "POST"
    #: Extra request headers. Jina's response-shaping switches live here.
    headers: dict[str, str] = field(default_factory=dict)


def _clip(text: Any, limit: int = _SNIPPET_CHARS) -> str:
    return str(text or "").strip()[:limit]


def _hits(
    rows: Any,
    title_key: str,
    url_key: str,
    snippet_key: str,
    favicon_key: str = "",
) -> list[Hit]:
    out: list[Hit] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get(url_key)
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        favicon = row.get(favicon_key) if favicon_key else ""
        if not isinstance(favicon, str) or not favicon.startswith("https://"):
            favicon = ""  # http or garbage would only produce a broken image
        out.append(
            Hit(
                title=_clip(row.get(title_key) or url, 200),
                url=url,
                snippet=_clip(row.get(snippet_key)),
                favicon=favicon[:500],
            )
        )
    return out


BACKENDS: dict[str, Backend] = {
    # Verified against docs.jina.ai on 2026-08-31. One free key, no card,
    # carries 10M tokens; a search bills a 10 000-token minimum, so the grant is
    # worth roughly a thousand searches at 100 requests/minute.
    #
    # `X-Respond-With: no-content` is what keeps it at that floor. Without it
    # s.jina.ai returns the *full text* of every hit, which is both far more
    # tokens per search and more context than the executor can use — the page
    # reader below already exists for the two or three pages worth reading.
    "jina": Backend(
        id="jina",
        endpoint="https://s.jina.ai/",
        auth="bearer",
        body=lambda query, limit: {"q": query, "num": min(limit, 10)},
        headers={
            "Accept": "application/json",
            "X-Respond-With": "no-content",
            "X-Retain-Images": "none",
        },
        # Results arrive under `data`. `_hits` returns [] for anything that is
        # not a list of dicts, so a shape change degrades to "no results from
        # this searcher" and the chain moves on — never a 500.
        parse=lambda data: (
            "",
            _hits(data.get("data"), "title", "url", "description"),
        ),
    ),
    # Verified against contextwire.dev's documented API on 2026-08-31. 1 000
    # queries a month, recurring, no card, and it fans one query out over 100+
    # engines. Newer and smaller than the others in this block, which is why it
    # sits behind them rather than in front.
    "contextwire": Backend(
        id="contextwire",
        endpoint="https://contextwire.dev/api/search",
        auth="bearer",
        method="GET",
        body=lambda query, limit: {
            "q": query,
            "max_results": limit,
            "profile": "web",
        },
        parse=lambda data: (
            _clip(data.get("answer"), _DIGEST_CHARS),
            _hits(data.get("sources") or data.get("results"), "title", "url", "snippet"),
        ),
    ),
    # Verified against exa.ai/docs/reference/search on 2026-08-30. Exa is a
    # neural search index rather than a Google wrapper, so it finds pages the
    # keyword engines miss on conceptual queries — which is most of what deep
    # research asks. `contents.text` returns real page text instead of a
    # search-engine snippet; 800 characters is enough to be useful and small
    # enough that six of them still fit in the executor's context.
    "exa": Backend(
        id="exa",
        endpoint="https://api.exa.ai/search",
        auth="x-api-key",
        body=lambda query, limit: {
            "query": query,
            "numResults": limit,
            "contents": {"text": {"maxCharacters": 800}},
        },
        # No answer field on /search — the hits *are* the answer material.
        parse=lambda data: (
            "",
            _hits(data.get("results"), "title", "url", "text", "favicon"),
        ),
    ),
    # Verified against docs.linkup.so on 2026-08-30. `sourcedAnswer` gives both
    # halves in one call: a written answer *and* the sources behind it, which
    # is exactly the shape `digest_of` wants. Note `name`, not `title`.
    "linkup": Backend(
        id="linkup",
        endpoint="https://api.linkup.so/v1/search",
        auth="bearer",
        body=lambda query, limit: {
            "q": query,
            "depth": "standard",  # "deep" costs several credits per call
            "outputType": "sourcedAnswer",
            "maxResults": limit,
        },
        parse=lambda data: (
            _clip(data.get("answer"), _DIGEST_CHARS),
            _hits(data.get("sources"), "name", "url", "snippet", "favicon"),
        ),
    ),
    # Verified against docs.tavily.com/documentation/api-reference/endpoint/search
    # on 2026-08-30. `include_answer` costs nothing extra and gives the executor
    # a one-paragraph head start; `search_depth: basic` is 1 credit, advanced 2.
    "tavily": Backend(
        id="tavily",
        endpoint="https://api.tavily.com/search",
        auth="bearer",
        body=lambda query, limit: {
            "query": query,
            "max_results": limit,
            "include_answer": True,
            "search_depth": "basic",
            "topic": "general",
        },
        parse=lambda data: (
            _clip(data.get("answer"), _DIGEST_CHARS),
            _hits(data.get("results"), "title", "url", "content"),
        ),
    ),
    # Verified against serper.dev's documented shape on 2026-08-30. `num` stays
    # at or below 10: 11+ results costs two credits instead of one.
    "serper": Backend(
        id="serper",
        endpoint="https://google.serper.dev/search",
        auth="x-api-key",
        body=lambda query, limit: {"q": query, "num": min(limit, 10)},
        parse=lambda data: (
            _clip((data.get("answerBox") or {}).get("answer")
                  or (data.get("answerBox") or {}).get("snippet")
                  or (data.get("knowledgeGraph") or {}).get("description"), _DIGEST_CHARS),
            _hits(data.get("organic"), "title", "link", "snippet"),
        ),
    ),
    # Verified against docs.firecrawl.dev/api-reference/endpoint/search on
    # 2026-08-30. No `scrapeOptions`: scraping every hit would turn one credit
    # into several and put whole pages into the executor's context.
    "firecrawl": Backend(
        id="firecrawl",
        endpoint="https://api.firecrawl.dev/v2/search",
        auth="bearer",
        body=lambda query, limit: {"query": query[:500], "limit": limit},
        parse=lambda data: (
            "",
            _hits((data.get("data") or {}).get("web"), "title", "url", "description"),
        ),
    ),
}


def _headers(backend: Backend, api_key: str) -> dict[str, str]:
    if backend.auth == "bearer":
        auth = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    else:
        auth = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    return {**auth, **backend.headers}


def digest_of(answer: str, hits: list[Hit]) -> str:
    """The block the executor reads.

    Numbered to match the source list the caller builds from the same hits, so
    a `[2]` in the digest and a `[2]` in the UI's Zdroje are the same page.
    """
    parts: list[str] = []
    if answer:
        parts.append(answer)
    for i, hit in enumerate(hits, start=1):
        line = f"[{i}] {hit.title}"
        if hit.snippet:
            line += f" — {hit.snippet}"
        parts.append(line)
    return "\n".join(parts)[:_DIGEST_CHARS]


async def search(
    backend_id: str, api_key: str, query: str, limit: int = _MAX_RESULTS
) -> tuple[str, list[Hit]]:
    """One search. Raises — the caller decides what a failure costs.

    Deliberately mirrors `research._search_once`'s contract, so the chain in
    `research.searchers()` can hold both kinds without a branch at every use.
    """
    if backend_id == KEYLESS_BACKEND:
        # Not a `Backend` row: it fans out over three hosts and needs no key.
        return await search_keyless(query, limit)

    backend = BACKENDS[backend_id]
    payload = backend.body(query, limit)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if backend.method == "GET":
            response = await client.get(
                backend.endpoint,
                headers=_headers(backend, api_key),
                params=payload,
            )
        else:
            response = await client.post(
                backend.endpoint,
                headers=_headers(backend, api_key),
                json=payload,
            )
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, dict):
        return "", []
    answer, hits = backend.parse(data)
    return digest_of(answer, hits), hits


# --- the keyless floor ----------------------------------------------------
#
# Every searcher above needs a key, which means the honest answer to "what
# happens with no search key connected?" used to be "deep research aborts and
# the answer is written from model weights". Wikipedia and arXiv both publish
# official, documented, keyless APIs that explicitly allow anonymous use, so
# there is no reason for the search stage ever to come back empty.
#
# This is a *floor*, not a competitor to the real searchers: an encyclopedia
# and a preprint index answer "what is X" and "what does the literature say"
# well and "what happened this week" not at all. It sits last in the chain and
# only runs once everything with a key has failed.

KEYLESS_BACKEND = "wikipedia"

_WIKI_LANGS = ("cs", "en")  # the user writes Czech; sub-questions come out English
_WIKI_PER_LANG = 3
_ARXIV_RESULTS = 3
_KEYLESS_TIMEOUT = 10.0
_TAG_RE = re.compile(r"<[^>]+>")


def wikipedia_available() -> bool:
    """Whether the Wikipedia leg may run at all.

    False until a contact URI is configured. Skipping it is not a degradation
    dressed up as a feature: without the contact every request comes back 403,
    so running the leg would spend two round trips per search to produce
    nothing. arXiv has no such requirement and always runs.
    """
    return bool(settings.contact_url.strip())


def _keyless_ua() -> str:
    contact = settings.contact_url.strip()
    return f"Prizma/1.0 ({contact}; local BYOK chat router)" if contact else _UA


def _untag(html: str) -> str:
    """MediaWiki snippets arrive with <span class="searchmatch"> highlighting."""
    return _TAG_RE.sub("", html or "").replace("&quot;", '"').replace("&amp;", "&")


async def _wikipedia(client: httpx.AsyncClient, lang: str, query: str) -> list[Hit]:
    """One language edition. `srprop=snippet` gives the matching text in the
    same call, so this is one request per language rather than two."""
    response = await client.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": _WIKI_PER_LANG,
            "srprop": "snippet",
        },
    )
    response.raise_for_status()
    data = response.json()
    rows = ((data.get("query") or {}).get("search") or []) if isinstance(data, dict) else []
    out: list[Hit] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        out.append(
            Hit(
                title=f"{title} — Wikipedie" if lang == "cs" else f"{title} — Wikipedia",
                url=f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                snippet=_clip(_untag(str(row.get("snippet") or ""))),
            )
        )
    return out


async def _arxiv(client: httpx.AsyncClient, query: str) -> list[Hit]:
    """arXiv's Atom API. Stdlib XML parsing — no new dependency for one feed."""
    response = await client.get(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": f"all:{query}",
            "max_results": _ARXIV_RESULTS,
            "sortBy": "relevance",
        },
    )
    response.raise_for_status()
    root = ET.fromstring(response.text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out: list[Hit] = []
    for entry in root.findall("a:entry", ns):
        url = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
        # arXiv still publishes its entry ids as `http://arxiv.org/abs/…` even
        # though the host has been https-only for years. Left alone, every
        # result is dropped by the https filter — silently, because an empty
        # leg is a normal outcome here. Upgrade rather than reject.
        if url.startswith("http://arxiv.org/"):
            url = "https://" + url[len("http://") :]
        title = " ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split())
        if not url.startswith("https://arxiv.org/") or not title:
            continue
        summary = " ".join(
            (entry.findtext("a:summary", default="", namespaces=ns) or "").split()
        )
        out.append(Hit(title=f"{title} — arXiv", url=url, snippet=_clip(summary)))
    return out


async def search_keyless(query: str, limit: int = _MAX_RESULTS) -> tuple[str, list[Hit]]:
    """Wikipedia (cs + en) and arXiv, concurrently. Never raises.

    Unlike `search()` this swallows its failures: it is the last entry in the
    chain, so there is nothing after it for a raised exception to fall through
    to, and "the encyclopedia was down" should read as an empty result rather
    than as a research run that crashed.
    """
    query = query.strip()
    if not query:
        return "", []

    legs = [_arxiv]
    if wikipedia_available():
        legs = [
            *(lambda c, q, lang=lang: _wikipedia(c, lang, q) for lang in _WIKI_LANGS),
            *legs,
        ]

    async with httpx.AsyncClient(
        timeout=_KEYLESS_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _keyless_ua()},
    ) as client:
        settled = await asyncio.gather(
            *(leg(client, query) for leg in legs), return_exceptions=True
        )

    hits: list[Hit] = []
    seen: set[str] = set()
    for item in settled:
        if isinstance(item, BaseException):
            log.info("keyless search leg failed: %s", item)
            continue
        for hit in item:
            if hit.url in seen:
                continue
            seen.add(hit.url)
            hits.append(hit)

    hits = hits[:limit]
    if not hits:
        return "", []
    return digest_of("", hits), hits


def status_of(exc: Exception) -> int | None:
    """HTTP status behind a failed search, when there was one.

    Lets callers tell "this key is wrong" (401/403) from "this key is fine and
    out of credit" (429) without importing httpx everywhere.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None


def _reader_url(url: str) -> str:
    """`https://r.jina.ai/<original url>` — the whole API surface."""
    return _READER + url


async def _read_one(
    client: httpx.AsyncClient, url: str, reader_key: str | None = None
) -> tuple[str, str]:
    headers = {
        # Markdown, not the JSON envelope: it is smaller, and the executor
        # reads markdown natively anyway.
        "Accept": "text/plain",
        "X-Return-Format": "markdown",
    }
    if reader_key:
        # Same key as the `jina` searcher. Authenticated reads run at 500
        # requests/minute instead of the anonymous 20, which is what lets
        # `READ_PAGES_KEYED` be larger. Optional on purpose: the keyless path
        # is the default and must keep working exactly as before.
        headers["Authorization"] = f"Bearer {reader_key}"
    response = await client.get(_reader_url(url), headers=headers)
    response.raise_for_status()
    return url, response.text.strip()[:_PAGE_CHARS]


async def read_pages(
    urls: list[str], limit: int | None = None, reader_key: str | None = None
) -> dict[str, str]:
    """URL -> page text for the first few URLs. Never raises.

    A page that will not load is simply absent from the result, because a
    research run that dies on one dead link is worse than one that reads two
    pages instead of three. Concurrent, and each read is individually capped:
    one slow page must not spend the whole research budget.

    Filters *before* it slices. The other order looks identical and is not: one
    `http://` link among the top three quietly turned "read three pages" into
    "read two", while the UI had already promised three.
    """
    if limit is None:
        limit = read_limit(reader_key)
    targets = [u for u in urls if u.startswith("https://")][:limit]
    if not targets:
        return {}

    out: dict[str, str] = {}
    async with httpx.AsyncClient(
        timeout=_READ_TIMEOUT, follow_redirects=True
    ) as client:
        settled = await asyncio.gather(
            *(
                asyncio.wait_for(
                    _read_one(client, url, reader_key), timeout=_READ_TIMEOUT
                )
                for url in targets
            ),
            return_exceptions=True,
        )
    for item in settled:
        if isinstance(item, BaseException):
            log.info("page read failed: %s", item)
            continue
        url, text = item
        if text:
            out[url] = text
    return out
