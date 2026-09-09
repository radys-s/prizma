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

# Errors that mean "this provider is temporarily busy" — park it so the next
# request doesn't queue behind it again — and errors that mean "this exact
# model slug is gone", where the slug is parked and the provider is not.
#
# Both lists live in `providers/failures.py` since the fan-out in
# `routing/decompose.py` turned out to need the same distinction and got it
# wrong: it parked a slug for an hour over a timeout, and that mark is global.
from app.providers.failures import COOLDOWN_ERRORS as _COOLDOWN_ERRORS
from app.providers.failures import DEAD_MODEL_ERRORS as _DEAD_MODEL_ERRORS
from app.providers.registry import ProviderModel, by_id
from app.routing import decompose as decomposing
from app.routing import plan as planning
from app.routing import research as researching
from app.routing import selector
from app.routing.effort import EffortProfile
from app.tools import registry as tooling

_STREAM_TIMEOUT = 60
_REVIEW_TIMEOUT = 90

log = logging.getLogger(__name__)


@dataclass
class StreamEvent:
    # "meta"        provider + model chosen, answer is about to start
    # "reason"      a chunk of the model's own thinking, before the answer
    # "delta"       a chunk of answer text
    # "tool_call"   the model asked for a local tool; `tool` is its name and
    #               `content` the one-line summary the UI shows
    # "tool_result" that tool finished; `tool_ok` says whether it was allowed
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
    # Local tool events only: which tool, and whether it was allowed to run.
    tool: str = ""
    tool_ok: bool = True


@dataclass
class Attempt:
    provider: ProviderModel
    model: str
    error: Exception


def _short(provider: ProviderModel) -> str:
    """Display name without the model in parentheses — for one-line notes."""
    return provider.display_name.split(" (")[0]


def _meta(
    provider: ProviderModel, model: str, pinned: ProviderModel | None
) -> StreamEvent:
    """The "this model is answering" event, with the substitution note if any."""
    note = ""
    if pinned is not None and provider.id != pinned.id:
        note = f"{_short(pinned)} nedostupný, odpověděl {_short(provider)}"
    return StreamEvent(
        type="meta", provider_id=provider.id, model_name=model, note=note
    )


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
    # Anything litellm did not classify goes to the log, not to the browser.
    # Its stringified errors routinely carry the api_base, the full request URL
    # and the provider's raw JSON error body — which for several providers
    # includes account or organisation identifiers.
    log.warning("Neklasifikovaná chyba providera %s: %s", attempt.provider.id, text)
    return f"{name}: požadavek selhal (podrobnosti v logu serveru)."


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



def _accumulate_tool_calls(fragments, sink: dict[int, dict]) -> None:
    """Skládá volání nástroje z fragmentů streamu.

    Volání nechodí najednou. Provider posílá `index`, pak `id` a jméno funkce,
    a pak argumenty po kouscích — u delšího JSONu klidně po jednom znaku. Sink
    je proto slovník podle indexu a argumenty se zřetězují; teprve na konci
    streamu je z toho platný JSON.
    """
    for fragment in fragments or ():
        index = getattr(fragment, "index", 0) or 0
        slot = sink.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if getattr(fragment, "id", None):
            slot["id"] = fragment.id
        function = getattr(fragment, "function", None)
        if function is None:
            continue
        if getattr(function, "name", None):
            slot["name"] = function.name
        if getattr(function, "arguments", None):
            slot["arguments"] += function.arguments


# Jak model dodá své uvažování, závisí na rodině. Dvě cesty:
#
#   * vlastní pole v deltě — `reasoning_content` (DeepSeek, Groq gpt-oss)
#     nebo `reasoning` (OpenRouter). Přijde odděleně od odpovědi a stačí ho
#     přeposlat;
#   * `<think>…</think>` přímo v `content` (Qwen QwQ, R1 distily přes
#     poskytovatele, kteří pole nemají). Tohle je ta horší varianta: dokud se
#     blok nevyřízne, teče uvažování do bubliny odpovědi jako text — což byl
#     přesně ten stav, kdy odpověď „začala“ dřív, než model domyslel.
_OPEN_THINK = "<think>"
_CLOSE_THINK = "</think>"


def _tail_prefix(text: str, needle: str) -> int:
    """Délka konce `text`, který může být začátkem `needle`.

    Chunk streamu končí kde chce, klidně uprostřed `</thi|nk>`. Tenhle kus se
    nesmí poslat ven jako text — jinak se v odpovědi objeví kus značky a druhá
    půlka se pak nikdy nenajde.
    """
    for size in range(min(len(needle) - 1, len(text)), 0, -1):
        if text.endswith(needle[:size]):
            return size
    return 0


class _ThinkSplitter:
    """Rozděluje proud `content` na ("reason", …) a ("text", …).

    Odzbrojí se, jakmile projde první neprázdný text: `<think>` uprostřed už
    napsané odpovědi je citace nebo kus kódu, ne uvažování. Neuzavřený blok na
    konci streamu se počítá celý jako uvažování — model prostě nedomyslel a
    prázdná bublina je lepší než odpověď složená z jeho poznámek.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False
        self._armed = True

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self._buffer += chunk
        out: list[tuple[str, str]] = []
        while self._inside or self._armed:
            needle = _CLOSE_THINK if self._inside else _OPEN_THINK
            at = self._buffer.find(needle)
            if at == -1:
                break
            head = self._buffer[:at]
            self._buffer = self._buffer[at + len(needle) :]
            if head:
                out.append(("reason" if self._inside else "text", head))
                if not self._inside and head.strip():
                    self._armed = False
            self._inside = not self._inside

        if self._inside or self._armed:
            needle = _CLOSE_THINK if self._inside else _OPEN_THINK
            hold = _tail_prefix(self._buffer, needle)
        else:
            hold = 0
        emit = self._buffer[: len(self._buffer) - hold] if hold else self._buffer
        self._buffer = self._buffer[len(self._buffer) - hold :] if hold else ""
        if emit:
            out.append(("reason" if self._inside else "text", emit))
            if not self._inside and emit.strip():
                self._armed = False
        return out

    def flush(self) -> list[tuple[str, str]]:
        rest, self._buffer = self._buffer, ""
        if not rest:
            return []
        return [("reason" if self._inside else "text", rest)]


def _reasoning_of(delta) -> str:
    """Uvažování z vlastního pole delty, ať už mu provider říká jakkoli."""
    for field in ("reasoning_content", "reasoning"):
        value = getattr(delta, field, None)
        if isinstance(value, str) and value:
            return value
    return ""


async def _try_stream(
    provider: ProviderModel,
    model: str,
    messages: list[dict],
    profile: EffortProfile,
    usage: dict,
    tools: list[dict] | None = None,
    calls: dict[int, dict] | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """Yieldne ("text", …) pro odpověď a ("reason", …) pro uvažování modelu."""
    response = await litellm.acompletion(
        model=model,
        **keystore.endpoint(provider),
        messages=messages,
        stream=True,
        timeout=_STREAM_TIMEOUT,
        # Mimo `_supported_kwargs`: ten se ptá litellm, co slug umí, a u nástrojů
        # se to rozhoduje o úroveň výš (`_tools_for`) — tady už je jisté, že
        # model volání funkcí umí, a vynechat je by znamenalo tichou ztrátu
        # celé schopnosti.
        **({"tools": tools, "tool_choice": "auto"} if tools else {}),
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
    splitter = _ThinkSplitter()
    async for chunk in response:
        _collect_usage(chunk, usage)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if calls is not None:
            _accumulate_tool_calls(getattr(delta, "tool_calls", None), calls)
        thought = _reasoning_of(delta)
        if thought:
            yield ("reason", thought)
        if delta.content:
            for piece in splitter.feed(delta.content):
                yield piece
    for piece in splitter.flush():
        yield piece


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
        **keystore.endpoint(provider),
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
    # Recenzent uvažuje taky, a jeho uvažování nikoho nezajímá — návrh, který
    # komentuje, má uživatel před očima. Zajímá nás jen výsledný text, takže se
    # tady stejný rozdělovač používá naopak: uvažování se zahazuje.
    splitter = _ThinkSplitter()
    async for chunk in response:
        if usage is not None:
            _collect_usage(chunk, usage)
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            for kind, piece in splitter.feed(delta):
                if kind == "text":
                    yield piece
    for kind, piece in splitter.flush():
        if kind == "text":
            yield piece


# Základní pravidla odpovědi, která jedou před každým dotazem.
#
# Do téhle chvíle nejely žádná: když plánovač nedoběhl nebo byl vypnutý
# (tier Light) a uživatel neměl vlastní pokyny, dostal model holou konverzaci
# bez jediného systémového turnu. Na free tieru je to poznat okamžitě — model
# spadne do výchozího chování své rodiny: úvod, který převypráví otázku, tři
# odstavce opatrnosti, odrážky tam, kde stačí věta, a na konci otázka zpátky.
# Není to chyba modelu, je to absence zadání.
#
# Co je tady napsané, je proto vybrané podle toho, co malé modely kazí nejčastěji,
# ne podle toho, co zní hezky. Každý řádek je jedna konkrétní chyba:
#
#   * odpověď v jiném jazyce než dotaz — u českého dotazu velmi častá,
#   * převyprávění otázky a „Skvělá otázka!" místo odpovědi,
#   * vata a hedging tam, kde model odpověď zná,
#   * vymyšlené API, čísla a zdroje, protože přiznat neznalost je horší pocit,
#   * useknutý kód s `# zbytek doplň` — nespustitelný, a tedy k ničemu,
#   * protiotázka místo odpovědi u lehce nejednoznačného zadání.
#
# Držené krátké schválně. Dlouhý systémový prompt na modelu s 8k kontextem
# vytlačuje konverzaci, a čím víc pravidel, tím menší šance, že model dodrží
# to podstatné.
_HOUSE_RULES = (
    "Odpovídej ve stejném jazyce, jakým je psaný dotaz.\n\n"
    "Začni odpovědí, ne úvodem. Nepřevyprávěj otázku, nehodnoť ji a nedělej "
    "závěrečné shrnutí toho, co jsi právě napsal. Délku odpovědi řiď podle "
    "otázky: na krátkou otázku krátká odpověď.\n\n"
    "Když má otázka konkrétní výsledek (číslo, jméno, ano/ne, verdikt), napiš "
    "ho hned do prvního řádku a samotný výsledek dej tučně. Odvození patří "
    "až za něj.\n\n"
    "Strukturu použij, jen když obsah opravdu má části. Odrážky nejsou "
    "náhrada za souvislý text a nadpis nad třemi větami je navíc.\n\n"
    "Nevymýšlej si. Fakta, čísla, názvy funkcí a odkazy uváděj jen tehdy, "
    "když si jimi jsi jistý; jinak řekni, co nevíš. Odhad označ jako odhad.\n\n"
    "Kód piš celý a spustitelný, ve fenced bloku s názvem jazyka. Žádné "
    "„zbytek doplň sám\" uprostřed funkce.\n\n"
    "Když je zadání nejednoznačné, vyber nejpravděpodobnější výklad, napiš "
    "jednou větou, z čeho vycházíš, a odpověz. Ptej se zpátky jen tehdy, když "
    "by odpověď byla při špatném výkladu k ničemu."
)

_HOUSE_MESSAGE: dict = {"role": "system", "content": _HOUSE_RULES}


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


# Kolik kol nástrojů smí jedna odpověď spotřebovat. Šest stačí na „vypiš složku,
# přečti dva soubory, spusť testy, oprav to" a zároveň je to strop pro model,
# který se zacyklí na čtení téhož souboru dokola — což se na free tieru stává
# a bez stropu by to znamenalo požadavek běžící, dokud si toho někdo nevšimne.
MAX_TOOL_ROUNDS = 6

# Kontext k nástrojům. Bez něj model hádá — píše relativní cesty, které nemá
# k čemu vztáhnout, a ptá se na svolení, které už dostal.
_TOOLS_SYSTEM = (
    "Máš přístup k počítači uživatele (Windows) přes nástroje. Používej je, "
    "když se odpověď dá ověřit místo odhadu — přečti soubor, o kterém je řeč, "
    "než o něm začneš mluvit.\n\n"
    "Cesty piš vždy absolutní. Když nevíš, kde soubor je, najdi ho "
    "(`search_text` nebo `list_dir`), nehádej.\n\n"
    "Nekomentuj, co se chystáš udělat — zavolej nástroj. Až budeš mít výsledky, "
    "odpověz na původní otázku a opři se o to, cos viděl, ne o obecné znalosti.\n\n"
    "Když nástroj vrátí „Odmítnuto“, je to hranice nastavená uživatelem. "
    "Nezkoušej ji obejít jinou cestou; napiš, co bys potřeboval."
)

_TOOLS_MESSAGE: dict = {"role": "system", "content": _TOOLS_SYSTEM}


def _tools_for(model: str, tools: list[dict]) -> list[dict] | None:
    """Nástroje pro tenhle slug, nebo None, když je neumí.

    Poslat `tools` modelu, který volání funkcí nepodporuje, končí u části
    providerů chybou 400 a u zbytku tím, že se pole tiše zahodí. První případ
    by kvůli zapnutému nastavení shodil odpověď na modelu, který by jinak
    odpověděl v pohodě — proto se to ptá dopředu.
    """
    if not tools:
        return None
    try:
        if not litellm.supports_function_calling(model=model):
            return None
    except Exception:  # noqa: BLE001 - neznámý slug: radši bez nástrojů
        return None
    return tools


async def stream_chat(
    messages: list[dict],
    plan: planning.Plan,
    profile: EffortProfile,
    pinned: str | None = None,
    findings: researching.Findings | None = None,
    breakdown: decomposing.Breakdown | None = None,
    allow_commands: bool = False,
) -> AsyncIterator[StreamEvent]:
    providers = selector.candidates(plan.capability, plan.complexity, pinned=pinned)
    if not providers:
        raise selector.NoProviderAvailable(plan.capability)

    # The house rules, the user's standing instructions, the planner's
    # analysis, any web findings and the results of a fan-out ride in front as
    # system turns, in that order — how to write, then who to listen to, then
    # how to approach the task, then what is known about it.
    #
    # The house rules lead because they are the floor, not the ceiling: they
    # say how an answer should look when nobody said otherwise. Custom
    # instructions come straight after so the user can overrule any of them —
    # a later system turn is the one the model follows — while the planner,
    # further back still, cannot talk the model out of "always answer in
    # English". The subtask results sit last, closest to the question, because
    # they are the most specific material of the five.
    #
    # The user's own message stays exactly as they typed it, always; see the
    # docstrings of routing/plan.py, routing/research.py and
    # routing/decompose.py.
    tools = tooling.specs(allow_commands=allow_commands)
    preamble = [
        m
        for m in (
            _HOUSE_MESSAGE,
            _custom_instructions(),
            _TOOLS_MESSAGE if tools else None,
            planning.system_message(plan),
            researching.system_message(findings) if findings else None,
            decomposing.system_message(breakdown) if breakdown else None,
        )
        if m is not None
    ]
    outgoing = [*preamble, *messages]

    question = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    pinned_provider = by_id(pinned) if pinned else None
    attempts: list[Attempt] = []
    usage: dict = {}

    for provider in providers:
        for model in model_health.usable_models(provider.models):
            started = False
            # `started` říká „už jsme ohlásili model", `spoke` „přišel text
            # odpovědi". Od chvíle, kdy se streamuje i uvažování, to nejsou
            # totéž: model, který jen přemýšlel a pak spadl, nesmí projít jako
            # hotová odpověď.
            spoke = False
            draft = ""
            try:
                usable_tools = _tools_for(model, tools)
                # Vlastní kopie historie: do ní přibývají volání nástrojů a
                # jejich výsledky. `outgoing` musí zůstat nedotčené, aby další
                # model v řadě začínal od původní konverzace, ne od půlky
                # rozdělaného nástrojového dialogu předchozího modelu.
                history = list(outgoing)

                for round_number in range(MAX_TOOL_ROUNDS + 1):
                    calls: dict[int, dict] = {}
                    round_text = ""
                    async for kind, delta in _try_stream(
                        provider,
                        model,
                        history,
                        profile,
                        usage,
                        tools=usable_tools,
                        calls=calls if usable_tools else None,
                    ):
                        if not started:
                            # Announce the model only once it has actually
                            # produced output, so the UI never labels an answer
                            # with a model that turned out to be dead.
                            started = True
                            yield _meta(provider, model, pinned_provider)
                        if kind == "reason":
                            yield StreamEvent(
                                type="reason", provider_id=provider.id, content=delta
                            )
                            continue
                        spoke = True
                        round_text += delta
                        draft += delta
                        yield StreamEvent(
                            type="delta", provider_id=provider.id, content=delta
                        )

                    if not calls:
                        break

                    # Volání bez jediného tokenu textu je běžné — model rovnou
                    # sáhne po nástroji. Meta se tedy musí poslat i tady, jinak
                    # by UI ukazovalo nástroje pod hlavičkou předchozí odpovědi.
                    if not started:
                        started = True
                        yield _meta(provider, model, pinned_provider)

                    if round_number == MAX_TOOL_ROUNDS:
                        # Došla kola. Modelu se to řekne jako výsledek, ne
                        # mlčením: dostane ještě jedno kolo bez nástrojů, ve
                        # kterém má odpovědět z toho, co už zjistil.
                        history.append(
                            {
                                "role": "system",
                                "content": (
                                    "Vyčerpal jsi limit volání nástrojů. "
                                    "Odpověz z toho, co už víš."
                                ),
                            }
                        )
                        usable_tools = None
                        continue

                    ordered = [calls[i] for i in sorted(calls)]
                    history.append(
                        {
                            "role": "assistant",
                            "content": round_text,
                            "tool_calls": [
                                {
                                    "id": call["id"] or f"call_{index}",
                                    "type": "function",
                                    "function": {
                                        "name": call["name"],
                                        "arguments": call["arguments"] or "{}",
                                    },
                                }
                                for index, call in enumerate(ordered)
                            ],
                        }
                    )
                    for index, call in enumerate(ordered):
                        yield StreamEvent(
                            type="tool_call",
                            provider_id=provider.id,
                            tool=call["name"],
                            content=tooling.summarize(call["name"], call["arguments"]),
                        )
                        result = await tooling.run(
                            call["name"],
                            call["arguments"],
                            allow_commands=allow_commands,
                        )
                        yield StreamEvent(
                            type="tool_result",
                            provider_id=provider.id,
                            tool=call["name"],
                            tool_ok=not result.startswith("Odmítnuto:"),
                            content=result[:400],
                        )
                        history.append(
                            {
                                "role": "tool",
                                "tool_call_id": call["id"] or f"call_{index}",
                                "name": call["name"],
                                "content": result,
                            }
                        )

                if not spoke:
                    # Clean finish with zero content — treat as a failed attempt
                    # so we fall through instead of showing an empty answer.
                    # Platí i pro model, který jen přemýšlel: uvažování bez
                    # odpovědi je pro čtenáře pořád prázdná bublina.
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
                # `spoke`, ne `started`: pravidlo z docstringu modulu mluví o
                # rozepsané *odpovědi*. Model, který stihl jen zamyšlení a pak
                # spadl, nemá co splétat — další v řadě začne od začátku a UI
                # si při novém `meta` uvažování zahodí.
                if spoke:
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
