"""Static registry of free-tier LLM providers wired through LiteLLM.

Model slugs rot fast. Between 05/2026 and 08/2026 Groq, Gemini, OpenRouter and
Cerebras *all* retired the model this app was pinned to, which turned every
chat into a raw `NotFoundError`. So each provider carries an ordered list of
candidate slugs instead of one: `app/providers/model_health.py` parks a slug
that 404s and the next one is used automatically.

Every slug marked "verified" below was confirmed with a real completion on
2026-08-28, not just by appearing in the provider's /models listing — those
lists include embedding and audio models that cannot chat at all. The entries
at the bottom are explicitly *not* verified — see the note above them.

`signup_url` deep-links straight at the provider's API-key page, not its
homepage: the settings dialog opens it in the OS default browser so the user
can copy a key without hunting through a console. NVIDIA is the cautionary
tale — its model pages hand out a key scoped to that one model, and only
build.nvidia.com/settings/api-keys issues the personal key that opens the
whole catalogue.

**Not here, and why** (checked 2026-08-29 so nobody re-researches it):

* *Cloudflare Workers AI* needs an account ID **and** a token. Every other
  provider here is one secret in one credential slot, and `keystore` has no
  shape for two. Adding it would mean a second field in the settings dialog
  for exactly one provider.
* *SambaNova* asks for a card on the tier that matters.
* *DeepSeek* and *Moonshot (Kimi)* have no standing free tier — DeepSeek's
  grant is one-time and expires in 30 days, Moonshot has none at all. Both are
  permanently free through NVIDIA NIM instead, which is how they appear below.

**Search APIs not here, and why** (re-checked 2026-08-30):

* *Brave Search API* retired its free tier in February 2026; every developer is
  on metered billing now, and the "free" $5 is prepaid credit on a saved card.
* *Mistral's* web-search connector is $30 per 1 000 calls even though the chat
  models are free — a paid tool behind a free key is the most confusing
  possible failure.
* *DuckDuckGo* via the `ddgs` package needs no key at all, which is tempting,
  but there is no official API, it is blocked by IP without warning, and
  scraping it is against their terms. A searcher that works on the developer's
  machine and not the user's is not a feature; one that breaks a ToS is not
  shippable at all.
* *Public SearXNG instances* mostly disable the JSON output format, and the
  ones that allow it rate-limit by IP within a handful of queries.
* *Perplexity Sonar* is paid outright.
* *Parallel.ai* renews $5 of credit monthly but wants a card on file first.
* *SearchApi.io* and *SerpAPI* grant 100 searches once, not monthly.
* *Marginalia* issues personal keys by e-mail; the shared `public` key it
  documents is rate-limited to the point of being a demo.
* *LLM7.io*, *Chutes* and the other anonymous free-LLM relays would see every
  prompt. The whole premise here is that keys and conversations stay on this
  machine — routing prompts through an unaccountable third party to save the
  user a signup contradicts the one thing this app promises.

*Exa* was in this list until 2026-08-30 on the grounds that its grant was a
one-time $20. That is no longer true: the free tier refills monthly and was
raised in July 2026, so it is now in the block below.

*Jina's `s.jina.ai`* was in this list until 2026-08-31, described as "paid,
Brave-backed, card-gated one layer up". That was wrong on all three counts:
the free key needs no card, carries 10M tokens, and the same key also raises
the page reader this app already uses from 20 to 500 requests a minute. It is
now the first search entry.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderModel:
    id: str  # internal id, also the default credential-store entry name
    display_name: str
    models: tuple[str, ...]  # ordered litellm slugs, first that works wins
    capabilities: tuple[str, ...]  # subset of CAPABILITIES
    signup_url: str  # direct link to this provider's API-key page
    key_prefix: str = ""  # expected key prefix, used only for a typo warning
    key_id: str = ""  # credential-store entry; empty means "use id"
    requires_card: bool = False
    notes: str = ""
    # Rough capability class, 1-3. Used when the planner rates a task "hard":
    # the selector then prefers the biggest models it has a key for, instead of
    # the fastest. Not a benchmark score — just "small / solid / frontier-ish".
    strength: int = 2
    # Order in which providers are asked to *be* the planner (lower first).
    # Latency dominates here: the planner call sits in front of every answer,
    # so Groq's LPUs lead and card-gated providers sit at the back.
    planner_rank: int = 50
    # Whether this entry appears in the model picker. False for entries that
    # exist to serve one internal stage — the search providers are chosen by
    # the globe control, and offering them as an answering model would only
    # invite pinning a websearch agent to write a poem.
    selectable: bool = True
    # Set for entries that are a *search API*, not a language model: the id of
    # a backend in `routing/websearch.py`. Those never go through litellm, so
    # `models` below is a display label rather than a slug, and every code path
    # that would call a model has to check this first.
    search_backend: str = ""
    # True for entries that need no credential at all. One flag rather than a
    # special case in five places: `selector._configured()` admits these
    # without a stored key and everything downstream works unchanged. It does
    # NOT make them count toward `MIN_CONFIGURED_SLOTS` — that check filters on
    # "can answer a question", and a keyless searcher cannot.
    keyless: bool = False
    # Overrides where the request is sent. Empty for everything hosted —
    # litellm knows those endpoints from the slug. Set only by the local
    # endpoint in `providers/local_llm.py`, which is a URL the user typed.
    api_base: str = ""
    # True for an endpoint running on this machine. The UI uses it to say so
    # rather than offering a signup link, and nothing here ever leaves the
    # device — which is the reason someone sets one up.
    local: bool = False

    @property
    def credential_id(self) -> str:
        """Key name in the keystore — a row id, and an env var name.

        Providers that authenticate with the same account key (both Mistral
        models) share one entry, so the key is pasted once and unlocks both.
        """
        return self.key_id or self.id

    @property
    def primary_model(self) -> str:
        """Preferred slug — what the UI shows before a request is attempted."""
        return self.models[0]


# Capability tags the router assigns to a request.
#
# "search" is the odd one out: `classifier.classify()` never returns it, only
# `routing/plan.py` can. Reaching the internet is a decision a model makes
# after reading the request, not something a regex should trigger — which also
# keeps the `light` tier (heuristic-only) reliably offline.
CAPABILITIES = ("fast", "general", "code", "creative", "long_context", "search")

# Order matters: for a given capability, earlier entries are tried first
# (assuming the user configured a key and the provider isn't in cooldown).
REGISTRY: list[ProviderModel] = [
    ProviderModel(
        id="groq",
        display_name="Groq (GPT-OSS 120B)",
        models=(
            "groq/openai/gpt-oss-120b",  # verified
            "groq/qwen/qwen3.8-27b",  # verified
            "groq/openai/gpt-oss-20b",  # verified
        ),
        capabilities=("fast", "general", "code"),
        signup_url="https://console.groq.com/keys",
        key_prefix="gsk_",
        notes="Nejrychlejší odezva (LPU hardware), žádná karta. Nejlepší první klíč.",
        strength=3,
        planner_rank=0,
    ),
    # --- dedicated search APIs ------------------------------------------------
    #
    # Ahead of the two LLM searchers on purpose. These are metered per search
    # instead of per token, so they do not collide with the answer's own quota
    # the way a Compound call does — see the module docstring in
    # routing/websearch.py. None of the five asks for a card.
    #
    # Within the block: recurring monthly quotas first (Jina, Exa, Linkup,
    # Tavily, ContextWire, Firecrawl), one-time grants last (Serper), because a
    # searcher that silently runs dry after 2 500 queries should not be the one
    # holding the front of the chain. The keyless floor is last of all.
    ProviderModel(
        id="jina",
        display_name="Jina · Search + Reader",
        models=("jina:search",),
        capabilities=("search",),
        signup_url="https://jina.ai/api-dashboard/key-manager",
        key_prefix="jina_",
        notes="10M tokenů zdarma, bez karty — jedno hledání stojí 10 000, "
        "takže vystačí zhruba na 1 000 dotazů. Stejný klíč navíc zrychlí "
        "čtení stránek v deep režimu z 20 na 500 požadavků za minutu.",
        strength=3,
        planner_rank=99,
        selectable=False,
        search_backend="jina",
    ),
    ProviderModel(
        id="exa",
        display_name="Exa · Neural search",
        models=("exa:search",),  # label, not a slug — see `search_backend`
        capabilities=("search",),
        signup_url="https://dashboard.exa.ai/api-keys",
        notes="$10 kreditu každý měsíc, bez karty. Sémantické vyhledávání — "
        "u odborných dotazů najde stránky, které klíčová slova minou. "
        "Nejlepší jediný klíč pro deep research.",
        strength=3,
        planner_rank=99,
        selectable=False,
        search_backend="exa",
    ),
    ProviderModel(
        id="linkup",
        display_name="Linkup · Web search",
        models=("linkup:search",),
        capabilities=("search",),
        signup_url="https://app.linkup.so",
        notes="4 000 dotazů při registraci a $5 kreditu měsíčně, bez karty. "
        "Vrací rovnou i shrnutí odpovědi, nejen odkazy.",
        strength=3,
        planner_rank=99,
        selectable=False,
        search_backend="linkup",
    ),
    ProviderModel(
        id="tavily",
        display_name="Tavily · Web search",
        models=("tavily:search",),  # label, not a slug — see `search_backend`
        capabilities=("search",),
        signup_url="https://app.tavily.com/home",
        key_prefix="tvly-",
        notes="1 000 hledání měsíčně zdarma, obnovuje se, bez karty. "
        "Nejlepší jediný klíč, který můžeš pro deep research přidat.",
        strength=3,
        planner_rank=99,
        selectable=False,
        search_backend="tavily",
    ),
    ProviderModel(
        id="contextwire",
        display_name="ContextWire · Meta search",
        models=("contextwire:search",),
        capabilities=("search",),
        signup_url="https://contextwire.dev",
        notes="1 000 dotazů měsíčně zdarma, obnovuje se, bez karty. Jeden "
        "dotaz rozešle do 100+ vyhledávačů. Menší a novější služba než "
        "ostatní v seznamu, proto je v pořadí za nimi.",
        strength=2,
        planner_rank=99,
        selectable=False,
        search_backend="contextwire",
    ),
    ProviderModel(
        id="firecrawl",
        display_name="Firecrawl · Web search",
        models=("firecrawl:search",),
        capabilities=("search",),
        signup_url="https://www.firecrawl.dev/app/api-keys",
        key_prefix="fc-",
        notes="1 000 kreditů měsíčně zdarma, bez karty. Prizma nescrapuje "
        "stránky, jen hledá — jedno hledání je jeden kredit.",
        strength=2,
        planner_rank=99,
        selectable=False,
        search_backend="firecrawl",
    ),
    ProviderModel(
        id="serper",
        display_name="Serper · Google",
        models=("serper:google",),
        capabilities=("search",),
        signup_url="https://serper.dev/api-key",
        notes="2 500 hledání zdarma — jednorázově při registraci, ne měsíčně. "
        "Výsledky jsou přímo z Googlu.",
        strength=3,
        planner_rank=99,
        selectable=False,
        search_backend="serper",
    ),
    ProviderModel(
        id="groq_search",
        display_name="Groq · Web (Compound)",
        models=(
            # Compound systems run web search and code execution server-side and
            # decide for themselves when to reach for them — there are no tools
            # to wire up, the slug *is* the feature. mini first: one tool call,
            # ~3x lower latency, and a single search is what "web" mode wants.
            # Doubled prefix is correct and verified: litellm strips the first
            # "groq/" as the provider, and the model id at Groq is itself
            # "groq/compound-mini". Same shape as "groq/openai/gpt-oss-120b".
            "groq/groq/compound-mini",  # verified 2026-08-29
            "groq/groq/compound",  # verified 2026-08-29
        ),
        capabilities=("search",),
        signup_url="https://console.groq.com/keys",
        key_prefix="gsk_",
        key_id="groq",  # same account key as the Groq answering entry
        notes="Vyhledávání na webu zdarma (Tavily), 250 dotazů denně.",
        strength=2,
        # Never the planner: its whole job is to burn a search on every call.
        planner_rank=99,
        selectable=False,
    ),
    ProviderModel(
        id="gemini",
        display_name="Google Gemini (3.5 Flash)",
        models=(
            "gemini/gemini-3.5-flash",  # verified
            "gemini/gemini-flash-latest",  # verified
            "gemini/gemini-3.7-flash",  # verified present, 503 under load
        ),
        # "search" is the grounding fallback: litellm turns `web_search_options`
        # into Google Search grounding for the Gemini 3.x family (5 000 grounded
        # prompts/month free). Used when Groq Compound is missing or rate-limited.
        capabilities=("general", "long_context", "creative", "search"),
        signup_url="https://aistudio.google.com/apikey",
        key_prefix="AIza",
        notes="Velký kontext, žádná karta dokud je billing vypnutý. Umí i hledat na webu.",
        strength=3,
        planner_rank=1,
    ),
    ProviderModel(
        id="mistral_code",
        display_name="Mistral (Codestral)",
        models=(
            "mistral/codestral-latest",  # verified
            "mistral/devstral-medium-latest",
            "mistral/mistral-code-latest",
        ),
        capabilities=("code",),
        signup_url="https://console.mistral.ai/api-keys",
        key_id="mistral",
        notes="Specializovaný na kód.",
        strength=2,
        planner_rank=7,
    ),
    ProviderModel(
        id="mistral_general",
        display_name="Mistral (Small)",
        models=(
            "mistral/mistral-small-latest",  # verified
            "mistral/mistral-medium-latest",  # verified
        ),
        capabilities=("general", "creative"),
        signup_url="https://console.mistral.ai/api-keys",
        key_id="mistral",
        notes="Stejný klíč jako Codestral — vkládá se jednou.",
        strength=2,
        planner_rank=6,
    ),
    ProviderModel(
        id="openrouter_free",
        display_name="OpenRouter (free model pool)",
        models=(
            "openrouter/minimax/minimax-m3:free",  # verified
            "openrouter/z-ai/glm-5.2:free",  # verified present, often rate-limited
            "openrouter/nvidia/nemotron-3-super-120b-a12b:free",  # verified
        ),
        capabilities=("general", "code", "creative"),
        signup_url="https://openrouter.ai/settings/keys",
        key_prefix="sk-or-",
        notes="Záložní pool, modely se v čase mění — ověř openrouter.ai/models?max_price=0",
        strength=2,
        planner_rank=5,
    ),
    ProviderModel(
        id="cerebras",
        display_name="Cerebras (GPT-OSS 120B)",
        models=(
            "cerebras/gpt-oss-120b",
            "cerebras/gemma-4-31b",
        ),
        capabilities=("fast",),
        signup_url="https://cloud.cerebras.ai",
        key_prefix="csk-",
        requires_card=True,
        notes=(
            "Bez ověřené karty vrací i s platným klíčem „Payment required“ "
            "(ověřeno 2026-08-28) — router ho pak automaticky přeskočí."
        ),
        strength=3,
        # Deliberately last for planner duty: the planner call sits in front of
        # every single answer, and burning it on a 402 would add latency to all
        # of them. As an *executor* it stays wherever its capabilities land it.
        planner_rank=90,
    ),
    # ---------------------------------------------------------------------
    # Everything below is NOT verified with a real completion — there is no
    # key for these yet. They ship as ordered candidates and get confirmed by
    # the built-in test button (`POST /api/providers/{id}/test`) the moment a
    # key is pasted; whatever 404s is parked by model_health and should then be
    # deleted from these tuples rather than left as decoration.
    #
    # The first draft of this block (2026-08-28) shipped two entries that could
    # never have worked, which is what "unverified" is supposed to mean and why
    # it is written on every one of them. Repaired 2026-08-29 from provider
    # docs; still unverified, because a doc is not a completion.
    # ---------------------------------------------------------------------
    ProviderModel(
        id="github_models",
        display_name="GitHub Models (GPT-4.1)",
        models=(
            "github/gpt-4.1",
            "github/gpt-4o",
            "github/o4-mini",
            "github/Llama-4-Scout-17B-16E-Instruct",
        ),
        capabilities=("general", "code"),
        signup_url="https://github.com/settings/personal-access-tokens",
        key_prefix="github_pat_",
        notes=(
            "Token musí mít Account permissions → Models → Read-only, jinak "
            "vrací 401 i když jinak funguje. Slugy zatím neověřené — otestuj."
        ),
        strength=3,
        planner_rank=2,
    ),
    # NVIDIA NIM: one `nvapi-` key, three picker entries. Same trick as the two
    # Mistral rows — `key_id` collapses them into a single credential slot, so
    # the key is pasted once and a specific model can still be pinned. Without
    # the split, "NVIDIA" would mean whichever slug happened to answer first.
    ProviderModel(
        id="nvidia_kimi",
        display_name="NVIDIA · Kimi K3",
        models=(
            "nvidia_nim/moonshotai/kimi-k3",
            "nvidia_nim/moonshotai/kimi-k2.6",
        ),
        capabilities=("code", "general", "long_context"),
        signup_url="https://build.nvidia.com/settings/api-keys",
        key_prefix="nvapi-",
        key_id="nvidia",
        notes=(
            "Kimi K3 zdarma přes NVIDIA. Moonshot vlastní free API nemá — "
            "tudy je to jediná bezplatná cesta. Neověřeno — otestuj."
        ),
        strength=3,
        planner_rank=3,
    ),
    ProviderModel(
        id="nvidia_deepseek",
        display_name="NVIDIA · DeepSeek",
        models=(
            "nvidia_nim/deepseek-ai/deepseek-r1",
            "nvidia_nim/deepseek-ai/deepseek-v4",
        ),
        capabilities=("general", "code"),
        signup_url="https://build.nvidia.com/settings/api-keys",
        key_prefix="nvapi-",
        key_id="nvidia",
        notes=(
            "DeepSeek zdarma přes NVIDIA. Vlastní free tier DeepSeeku je "
            "jednorázový a po 30 dnech propadá. Neověřeno — otestuj."
        ),
        strength=3,
        planner_rank=4,
    ),
    ProviderModel(
        id="nvidia_nemotron",
        display_name="NVIDIA · Nemotron / Qwen",
        models=(
            "nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b",
            "nvidia_nim/qwen/qwen3.5-397b-a17b",
            "nvidia_nim/meta/llama-3.1-70b-instruct",
        ),
        capabilities=("general", "long_context", "creative"),
        signup_url="https://build.nvidia.com/settings/api-keys",
        key_prefix="nvapi-",
        key_id="nvidia",
        notes=(
            "Největší modely katalogu, jeden klíč se všemi NVIDIA položkami. "
            "Neověřeno — otestuj."
        ),
        strength=3,
        planner_rank=8,
    ),
    ProviderModel(
        id="zai_glm",
        display_name="Z.AI (GLM Flash)",
        models=(
            "zai/glm-4.7-flash",
            "zai/glm-4.5-flash",
        ),
        capabilities=("fast", "general", "creative"),
        signup_url="https://z.ai/manage-apikey/apikey-list",
        notes=(
            "Flash modely jsou zdarma bez expirace. Neumí JSON mode, takže "
            "jen vykonavatel, nikdy plánovač. Neověřeno — otestuj."
        ),
        strength=2,
        planner_rank=96,
    ),
    ProviderModel(
        id="cohere",
        display_name="Cohere (Command A)",
        models=(
            # `cohere/` is litellm's embed/rerank route — chat needs
            # `cohere_chat/`, which speaks v2 /chat. And the unversioned
            # aliases are gone: Cohere removed command-r-plus on 2025-09-15
            # and deprecated the /v1/generate endpoint the month before, so
            # the original tuple was wrong twice over.
            "cohere_chat/command-a-plus-05-2026",
            "cohere_chat/command-a-03-2025",
            "cohere_chat/command-r-plus-08-2024",
            "cohere_chat/command-r7b-12-2024",
        ),
        capabilities=("general", "creative", "long_context"),
        signup_url="https://dashboard.cohere.com/api-keys",
        notes=(
            "Trial klíč zdarma, ~1000 volání měsíčně a jen nekomerční použití. "
            "Neumí JSON mode, takže ho router nikdy nepoužije jako plánovač — "
            "jen jako vykonavatele. Neověřeno — otestuj."
        ),
        strength=2,
        # Excluded from planner duty by the JSON-mode check in routing/plan.py,
        # not by this number — the number is only a tiebreaker.
        planner_rank=95,
    ),
    ProviderModel(
        id="huggingface",
        display_name="Hugging Face Router",
        models=(
            "huggingface/deepseek-ai/DeepSeek-V3.2",
            "huggingface/Qwen/Qwen3.5-235B-A22B-Instruct",
            "huggingface/meta-llama/Llama-4-Scout-17B-16E-Instruct",
        ),
        capabilities=("general", "code", "creative"),
        signup_url="https://huggingface.co/settings/tokens",
        key_prefix="hf_",
        notes=(
            "Jeden token otevře router přes několik poskytovatelů naráz, "
            "měsíční kredit zdarma bez karty. Token musí mít oprávnění "
            "„Make calls to Inference Providers“. Neověřeno — otestuj."
        ),
        strength=3,
        planner_rank=9,
    ),
    # --- the keyless floor ---------------------------------------------------
    #
    # Physically last in the file, and that is load-bearing: `searchers()` walks
    # the registry in order, so anything below this line would be tried after a
    # searcher that exists only because everything else already failed.
    #
    # Not selectable, not an answering model, and explicitly not counted toward
    # MIN_CONFIGURED_SLOTS — `selector.configured_slots()` filters on "can
    # answer", which this cannot. It exists so the honest answer to "what does
    # the globe do with no search key?" stops being "nothing".
    ProviderModel(
        id="wikipedia",
        display_name="Wikipedia + arXiv · bez klíče",
        models=("wikipedia:search",),
        capabilities=("search",),
        signup_url="https://www.mediawiki.org/wiki/API:Etiquette",
        notes="Vždy k dispozici, žádný klíč ani registrace — oficiální API "
        "arXivu a Wikipedie. Použije se, až když žádný jiný vyhledávač "
        "neodpoví; encyklopedie zná pojmy, ne dnešní zprávy. Wikipedie "
        "navíc vyžaduje kontaktní URL (CONTACT_URL v backend/.env), bez "
        "něj běží jen arXiv.",
        strength=1,
        planner_rank=99,
        selectable=False,
        search_backend="wikipedia",
        keyless=True,
    ),
]

BY_ID: dict[str, ProviderModel] = {p.id: p for p in REGISTRY}


def active() -> list[ProviderModel]:
    """The static registry plus whatever the user configured at runtime.

    Only the local endpoint is dynamic, and it has to be: its URL and model
    name are typed by the user, so it cannot exist as a frozen row above. Every
    caller that asks "what providers are there" goes through here rather than
    reading `REGISTRY`, otherwise a locally hosted model would be invisible to
    exactly the parts that need to see it — the selector, the model picker and
    the settings dialog.

    Imported inside the function because `local_llm` imports `ProviderModel`
    from this module; at call time both are loaded and there is no cycle.
    """
    from app.providers import local_llm

    extra = local_llm.entry()
    return [*REGISTRY, extra] if extra is not None else list(REGISTRY)


def by_id(provider_id: str) -> ProviderModel | None:
    """Lookup across `active()`, so a local endpoint is findable by id too."""
    if provider_id in BY_ID:
        return BY_ID[provider_id]
    return next((p for p in active() if p.id == provider_id), None)


def providers_for_capability(capability: str) -> list[ProviderModel]:
    return [p for p in active() if capability in p.capabilities]


def providers_sharing_key(credential_id: str) -> list[ProviderModel]:
    """Every registry entry unlocked by one credential-store entry."""
    return [p for p in active() if p.credential_id == credential_id]
