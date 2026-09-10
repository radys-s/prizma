"""How to react to a provider error, in one place.

Every failure in this app gets asked two different questions, and they have
two different answers: *is this model slug gone* — park the slug — and *is this
provider having a bad minute* — park the provider. `client.py` has always drawn
that line correctly. `decompose.py` did not, and the mistake was invisible
because both paths end the same way for the user: the answer still arrives.

The fan-out marked a slug dead on *any* exception — timeouts and exhausted
quotas included. Its parts run four at a time against free tiers with a
45-second ceiling, so a timeout there is routine rather than exceptional, and
`model_health.mark_dead()` lasts an hour and applies process-wide:
`client.stream_chat` walks the very same `usable_models()`. One slow subtask
therefore took a perfectly healthy model away from the main answer for an hour.

Hence one definition, imported by both. A third caller that gets this wrong is
now a missing import rather than a copied tuple that quietly drifts.
"""

import email.utils
import logging
import re
import time

import litellm

log = logging.getLogger(__name__)

# "This provider is busy right now." Park the provider and come back to it in a
# minute; none of these say anything about the model slug itself.
COOLDOWN_ERRORS: tuple[type[BaseException], ...] = (
    litellm.RateLimitError,
    litellm.APIConnectionError,
    litellm.Timeout,
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
)

# "This exact slug is gone." Only a 404 means that — everything else is about
# the moment, not the model.
DEAD_MODEL_ERRORS: tuple[type[BaseException], ...] = (litellm.NotFoundError,)


def is_dead_model(exc: BaseException) -> bool:
    """Whether the *slug* should be parked and the next candidate tried."""
    return isinstance(exc, DEAD_MODEL_ERRORS)


def is_busy(exc: BaseException) -> bool:
    """Whether the *provider* should be parked for a cooldown."""
    return isinstance(exc, COOLDOWN_ERRORS)


# Kolik nejmíň a nejvíc se dá věřit tomu, co provider o čekání tvrdí. Dolní
# hranice proto, že „zkus za 0 s" je odpověď, na kterou se nedá čekat; horní
# proto, že některé free tiery hlásí zbytek dne v sekundách a hodnota typu
# 82 000 by z krátkého výpadku udělala provider vyřazený až do rána.
_MIN_RETRY = 5.0
_MAX_RETRY = 24 * 3600.0

# „Please try again in 7m12.5s", „retry after 20s", „Please retry in 1.5s".
_TRY_AGAIN = re.compile(
    r"(?:try|retry)(?:\s+again)?\s+(?:in|after)\s+([0-9][0-9smhd.\s]*)", re.IGNORECASE
)
# Gemini posílá strojově čitelné `retry_delay { seconds: 27 }`.
_RETRY_DELAY = re.compile(r"retry_delay\s*\{\s*seconds:\s*(\d+)", re.IGNORECASE)
# Jednotlivé kusy trvání: „7m", „12.5s", „500ms", „1h".
_CHUNK = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?", re.IGNORECASE)
_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def _parse_duration(text: str) -> float | None:
    """„7m12.5s" -> 432.5. Vrací None, když v textu není nic měřitelného."""
    total = 0.0
    seen = False
    for number, unit in _CHUNK.findall(text):
        try:
            value = float(number)
        except ValueError:
            continue
        # Holé číslo bez jednotky je podle RFC 9110 sekunda.
        total += value * _UNITS.get((unit or "s").lower(), 1.0)
        seen = True
    return total if seen else None


def _headers(exc: BaseException) -> dict:
    """Hlavičky odpovědi, ať už si je litellm odložil kamkoli.

    Tři místa, protože litellm je za posledních pár verzí střídalo a tenhle
    kód nemá padat na tom, které z nich je zrovna to pravé.
    """
    for attr in ("litellm_response_headers", "response_headers", "headers"):
        value = getattr(exc, attr, None)
        if hasattr(value, "get"):
            return value
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if hasattr(headers, "get"):
        return headers
    return {}


def retry_after_seconds(exc: BaseException) -> float | None:
    """Za jak dlouho má smysl providera zkusit znovu, když to sám řekl.

    Uživatel se dosud dozvěděl jen „vyčerpaný limit." — což je ta méně
    zajímavá polovina zprávy. Zajímavé je, jestli je model zpátky za minutu,
    nebo až zítra: v prvním případě se počká, ve druhém se přepne jinam.
    Providery to skoro vždycky pošlou, jen pokaždé jinak — v hlavičce
    `Retry-After`, v `x-ratelimit-reset-*`, nebo prostě větou v těle chyby.

    Vrací None, když se nic důvěryhodného najít nedá. To není chyba: hláška
    bez času je pořád lepší než vymyšlené číslo.
    """
    headers = _headers(exc)

    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw:
        text = str(raw).strip()
        try:
            return _clamp(float(text))
        except ValueError:
            # Druhý povolený tvar podle RFC 9110 je HTTP datum.
            try:
                when = email.utils.parsedate_to_datetime(text)
            except (TypeError, ValueError):
                when = None
            if when is not None:
                return _clamp(when.timestamp() - time.time())

    for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        value = headers.get(key)
        if value:
            parsed = _parse_duration(str(value))
            if parsed:
                return _clamp(parsed)

    text = str(getattr(exc, "message", "") or "") + " " + str(exc)
    delay = _RETRY_DELAY.search(text)
    if delay:
        return _clamp(float(delay.group(1)))
    again = _TRY_AGAIN.search(text)
    if again:
        parsed = _parse_duration(again.group(1))
        if parsed:
            return _clamp(parsed)
    return None


def _clamp(seconds: float) -> float | None:
    if seconds <= 0:
        return None
    return min(max(seconds, _MIN_RETRY), _MAX_RETRY)


def humanize_wait(seconds: float | None) -> str:
    """„za 7 minut", „za 2 hodiny", nebo prázdno, když se to neví.

    Zaokrouhluje nahoru schválně. Napsat „za minutu" a nechat uživatele
    narazit na tentýž limit o pět sekund později je horší než říct minutu a
    půl — tohle číslo je slib, ne měření.
    """
    if not seconds or seconds <= 0:
        return ""
    if seconds < 90:
        return f"za {int(seconds) + 1} s"
    if seconds < 3600:
        return f"za {-(-int(seconds) // 60)} min"
    return f"za {-(-int(seconds) // 3600)} h"


def record(exc: BaseException, *, provider_id: str, model: str) -> bool:
    """Zapíše, co selhání znamená, a vrátí, jestli má smysl zkusit další slug.

    Vedlejší stanoviště modelů (`routing/titles.py`, `plan.py`, `compact.py`)
    dosud selhání jen zalogovala a šla dál. Tím se ale nikdo nic nedozvěděl:
    `model_health` i `selector` jsou procesové, takže mrtvý slug, na který
    narazí pojmenování konverzace, zůstal ve hře pro hlavní odpověď — a hlavně
    pro každé další pojmenování. V logu je to vidět jako pořád stejná sekvence:

        openrouter_free: 404 „This model is unavailable for free"
        nvidia_deepseek: 404 page not found
        mistral_general: 429 Rate limit exceeded

    U každé nové konverzace znovu, celá řada, než se najde model, který
    odpoví. Přitom první z těch chyb je definitivní odpověď na otázku
    „existuje ten slug?" — stačí si ji zapsat.

    Vrací True u mrtvého slugu, tedy „zkus u stejného providera další model".
    U zahlceného providera vrací False: další slug od téhož providera narazí
    na tentýž limit.
    """
    # Pozdní import: `selector` je v `routing`, tenhle modul v `providers`.
    # Nahoře by to byla zbytečná vazba mezi vrstvami kvůli jedné funkci.
    from app.providers import model_health
    from app.routing import selector

    if is_dead_model(exc):
        model_health.mark_dead(model)
        return True
    if is_busy(exc):
        # Když provider sám řekl, jak dlouho má smysl počkat, drž se toho.
        # Šedesátisekundový paušál je u vyčerpané denní kvóty k ničemu:
        # za minutu se na tentýž limit narazí znovu, a to pokaždé stojí jeden
        # request navíc z kvóty, která už došla.
        wait = retry_after_seconds(exc)
        if wait:
            log.info("provider %s hlásí limit, čekání %.0f s", provider_id, wait)
            selector.mark_cooldown(provider_id, wait)
        else:
            selector.mark_cooldown(provider_id)
    return False
