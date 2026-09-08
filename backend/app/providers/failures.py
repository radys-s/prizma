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

import litellm

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
        selector.mark_cooldown(provider_id)
    return False
