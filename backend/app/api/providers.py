"""Provider status, key management and a live key test.

Security model for the key-writing endpoints (see also keystore.py and
`app/auth/guard.py`, which is where the checks now live):

* locally the peer must be loopback; in public mode the `Origin` allowlist and
  the session take over that job (see `app/auth/guard.py`);
* the caller must present a valid session cookie, plus a matching CSRF token on
  every write — so another process running as the user can no longer reach
  these routes just by omitting `Origin`;
* nothing here ever returns a stored key. `configured` is a boolean, and the
  only way a key leaves the keystore is server-side, in client.py.
"""

import asyncio
import logging
import webbrowser

import litellm
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import crypto
from app.auth import ratelimit
from app.config import settings
from app.providers import keystore, local_llm, model_health
from app.providers.registry import ProviderModel, active, by_id
from app.routing import websearch

log = logging.getLogger(__name__)


def _limit(request: Request, key: str, *, limit: int, window: float) -> None:
    ip = request.client.host if request.client else "unknown"
    try:
        ratelimit.check(key, ip, limit=limit, window=window)
    except ratelimit.RateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail=f"Příliš mnoho pokusů za sebou. Zkus to za {exc.retry_after} s.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


def _opaque(exc: Exception, where: str) -> str:
    """Log the real error, hand the browser a sentence that leaks nothing.

    litellm and httpx stringify their failures with the api_base, the full
    request URL and the provider's raw JSON body attached. Truncating that to
    200 characters limits the blast radius; it does not make it safe to show.
    """
    log.warning("%s: %s", where, exc)
    return "Test selhal — podrobnosti jsou v logu serveru."

router = APIRouter(prefix="/api/providers", tags=["providers"])

_TEST_TIMEOUT_SECONDS = 20.0


def _provider_or_404(provider_id: str) -> ProviderModel:
    provider = by_id(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Neznámý provider: {provider_id}")
    return provider


class KeyPayload(BaseModel):
    api_key: str = Field(min_length=1, max_length=500)


@router.get("")
def list_providers():
    """Status only — never returns key values."""
    return [
        {
            "id": p.id,
            "display_name": p.display_name,
            "model": p.primary_model,
            "models": list(p.models),
            "capabilities": p.capabilities,
            "requires_card": p.requires_card,
            "notes": p.notes,
            "signup_url": p.signup_url,
            "key_prefix": p.key_prefix,
            "key_id": p.credential_id,
            "configured": keystore.has_key(p.credential_id),
            # True when the key comes from the environment. The dialog uses it
            # to say *where* the key lives instead of offering an edit that
            # would be accepted and then ignored.
            "from_env": keystore.is_shadowed(p.credential_id),
        }
        # The local endpoint is deliberately absent. This list drives the key
        # dialog, and a model on this machine has no key to paste, no signup
        # page and no card requirement — rendering it here would produce a
        # card whose every control is a lie. It is managed by `/local` below,
        # and it is still reachable everywhere it matters: the pin dropdown
        # reads `/api/routing/options`, and `POST /local/test` tests it.
        for p in active()
        if not p.local
    ]


def _reject_if_shadowed(credential_id: str) -> None:
    """A key set in the environment cannot be changed over HTTP.

    Silently writing the row would be the worst outcome: the write succeeds,
    the UI shows the new key as configured, and the app keeps using the old one
    from the environment forever. 409 says exactly what is happening.
    """
    if keystore.is_shadowed(credential_id):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Klíč {credential_id} je nastavený proměnnou prostředí "
                f"{keystore.ENV_PREFIX}{credential_id.upper()} a má přednost. "
                "Změň ji na serveru, nebo ji zruš."
            ),
        )


@router.put("/{provider_id}/key")
def set_provider_key(provider_id: str, payload: KeyPayload):
    provider = _provider_or_404(provider_id)
    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=422, detail="Prázdný klíč.")
    _reject_if_shadowed(provider.credential_id)
    try:
        keystore.set_key(provider.credential_id, api_key)
    except crypto.CryptoError as exc:
        # Configuration, not a bug: no master key means nothing can be stored.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"configured": True, "key_id": provider.credential_id}


@router.delete("/{provider_id}/key")
def delete_provider_key(provider_id: str):
    provider = _provider_or_404(provider_id)
    _reject_if_shadowed(provider.credential_id)
    keystore.delete_key(provider.credential_id)
    return {"configured": False, "key_id": provider.credential_id}


@router.post("/{provider_id}/open-signup")
async def open_signup(provider_id: str):
    """Opens the provider's API-key page — locally in the OS default browser.

    On a server there is no browser to open, and `webbrowser.open` there either
    returns False or spawns something into the void. So in public mode this
    hands the URL back and the frontend opens it in the user's own tab, which
    is where they actually are. The URL still comes from the registry and is
    never taken from the request, so neither path can open an arbitrary address.
    """
    provider = _provider_or_404(provider_id)
    if settings.public_mode:
        return {"opened": False, "url": provider.signup_url}
    opened = await run_in_threadpool(webbrowser.open, provider.signup_url)
    return {"opened": bool(opened), "url": provider.signup_url}


# ----------------------------------------------------- a model on this machine
#
# Routes live under `/local` rather than `/{provider_id}/...` so they cannot
# collide with a registry id, and every one of them refuses in public mode:
# they make the backend fetch a URL the caller typed, which on a laptop is the
# feature and on a shared server is a request-forgery hole pointed at the
# hosting provider's private network.


class LocalPayload(BaseModel):
    base_url: str = Field(min_length=1, max_length=300)
    model: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=80)


class ProbePayload(BaseModel):
    base_url: str = Field(min_length=1, max_length=300)


def _local_or_403() -> None:
    if not local_llm.available():
        raise HTTPException(
            status_code=403,
            detail="Vlastní model jde připojit jen v lokálním běhu, ne na serveru.",
        )


@router.get("/local")
def local_status():
    """Configuration plus what this machine can realistically run."""
    current = local_llm.config()
    return {
        "available": local_llm.available(),
        "configured": current.ready,
        "base_url": current.base_url,
        "model": current.model,
        "label": current.label,
        "hardware": local_llm.hardware(),
    }


@router.post("/local/probe")
async def local_probe(payload: ProbePayload):
    """Asks the endpoint which models it serves, so the name isn't guessed."""
    _local_or_403()
    try:
        models = await local_llm.probe(payload.base_url)
    except local_llm.LocalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"models": models}


@router.put("/local")
def local_save(payload: LocalPayload):
    _local_or_403()
    current = local_llm.save(payload.base_url, payload.model, payload.label)
    if not current.ready:
        raise HTTPException(status_code=422, detail="Chybí adresa nebo název modelu.")
    return {"configured": True, "base_url": current.base_url, "model": current.model}


@router.delete("/local")
def local_clear():
    _local_or_403()
    local_llm.clear()
    return {"configured": False}


def _classify_local(exc: Exception, model: str) -> tuple[bool, str, str]:
    """The same failures, for an endpoint on this machine.

    Every message in `_classify` is about a key, and a local endpoint has none
    — the placeholder `keystore.endpoint` sends is ignored by Ollama, LM Studio
    and llama.cpp alike. Reusing those strings here would answer a wrong base
    URL with "klíč prošel ověřením, ale model už provider nenabízí", which
    points at the one thing that cannot possibly be wrong.

    The two failures that actually happen are: the tool is not running, and the
    URL or the model name is not the one it serves. Both are said plainly.
    """
    # `openai/` is how litellm is told which protocol to speak; the user never
    # typed it and should not have to wonder whether it belongs in the field.
    model = model.removeprefix("openai/")
    if isinstance(exc, (litellm.APIConnectionError, litellm.Timeout, asyncio.TimeoutError)):
        return (
            False,
            "error",
            "Na té adrese nikdo neodpovídá. Zkontroluj, jestli nástroj s modelem běží.",
        )
    if isinstance(exc, litellm.NotFoundError):
        return (
            False,
            "error",
            f"Endpoint odpověděl, ale model {model} nezná. Zkontroluj název "
            "tlačítkem „Zjistit modely“ a jestli adresa končí na /v1.",
        )
    if isinstance(exc, (litellm.ServiceUnavailableError, litellm.InternalServerError)):
        return (
            False,
            "error",
            "Endpoint běží, ale požadavek mu spadl. Podívej se do jeho logu — "
            "typicky se model nevešel do paměti.",
        )
    return False, "error", _opaque(exc, "Test lokálního endpointu")


def _classify(exc: Exception, model: str) -> tuple[bool, str, str]:
    """Maps a provider error onto (key_is_valid, level, human message).

    The question this endpoint answers is "is *the key* good", which is not the
    same as "did the request succeed". Anything that got past authentication
    proves the key is fine, so a quota limit, a retired model or an unverified
    billing account are all warnings about the account or our own config — not
    a red error telling the user they pasted the key wrong when they didn't.
    """
    if isinstance(exc, litellm.AuthenticationError):
        return False, "error", "Klíč odmítnut — zkontroluj, jestli jsi zkopíroval celý."
    if isinstance(exc, (litellm.APIConnectionError, litellm.Timeout, asyncio.TimeoutError)):
        return False, "error", "Nepodařilo se spojit s providerem — zkontroluj připojení."
    if isinstance(exc, litellm.RateLimitError):
        return True, "warning", "Klíč funguje, jen je právě vyčerpaný limit. Router ho na chvíli přeskočí."
    if isinstance(exc, litellm.NotFoundError):
        return (
            True,
            "warning",
            f"Klíč prošel ověřením, ale model {model} už provider nenabízí. "
            "Router automaticky zkusí další model ze seznamu.",
        )
    if isinstance(exc, (litellm.ServiceUnavailableError, litellm.InternalServerError)):
        return True, "warning", "Klíč funguje, ale provider je momentálně přetížený."

    text = str(exc)
    if "payment" in text.lower() or "billing" in text.lower():
        return (
            True,
            "warning",
            "Klíč je platný, ale účet vyžaduje ověřenou platební kartu. "
            "Dokud ji nepřidáš, router tohoto providera přeskočí.",
        )
    return False, "error", _opaque(exc, "Test klíče providera")


def _classify_search(exc: Exception) -> dict:
    """Same contract as `_classify`, for the native search APIs.

    They are not litellm clients, so none of the exception types above ever
    appear — a failure arrives as an `httpx.HTTPStatusError` with a status
    code. Before this existed the test button ran `litellm.acompletion` against
    a label like "tavily:search", which threw for every key ever pasted and
    reported a perfectly good one as red.
    """
    status = websearch.status_of(exc)
    if status in (401, 403):
        return {
            "ok": False,
            "level": "error",
            "detail": "Klíč odmítnut — zkontroluj, jestli jsi zkopíroval celý.",
        }
    if status == 429:
        return {
            "ok": True,
            "level": "warning",
            "detail": "Klíč funguje, jen je vyčerpaný měsíční limit hledání.",
        }
    if status == 402:
        return {
            "ok": True,
            "level": "warning",
            "detail": "Klíč je platný, ale účet nemá kredit. Router tohoto providera přeskočí.",
        }
    if isinstance(exc, asyncio.TimeoutError):
        return {
            "ok": False,
            "level": "error",
            "detail": "Nepodařilo se spojit s providerem — zkontroluj připojení.",
        }
    return {"ok": False, "level": "error", "detail": _opaque(exc, "Test klíče hledání")}


@router.post("/{provider_id}/test")
async def test_provider_key(provider_id: str, request: Request):
    """Sends a real minimal request — the only proof a key actually works.

    Walks the provider's candidate slugs so one retired model doesn't make a
    perfectly good key look broken.
    """
    # Every call is at least one outbound request, and the walk can be several.
    _limit(request, "provider-test", limit=10, window=60.0)
    provider = _provider_or_404(provider_id)
    api_key = keystore.get_key(provider.credential_id)
    # A local endpoint has no key to test — what is worth testing there is
    # whether the thing is running and whether the slug is one it serves, and
    # the completion below answers both.
    if not api_key and not provider.local:
        return {"ok": False, "level": "error", "detail": "Pro tohoto providera není uložený žádný klíč."}

    if provider.search_backend:
        # A search API, not a model. `models` here is a display label and there
        # is nothing for litellm to call — see `ProviderModel.search_backend`.
        try:
            _, hits = await asyncio.wait_for(
                websearch.search(provider.search_backend, api_key, "prizma test", limit=3),
                timeout=_TEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - httpx raises its own family
            return _classify_search(exc)
        return {
            "ok": True,
            "level": "ok",
            "detail": f"Klíč funguje. Hledání vrátilo {len(hits)} výsledků.",
        }

    last: tuple[bool, str, str] | None = None
    for model in model_health.usable_models(provider.models):
        try:
            await asyncio.wait_for(
                litellm.acompletion(
                    model=model,
                    **keystore.endpoint(provider),
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                    timeout=15,
                ),
                timeout=_TEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # litellm raises a wide family of provider errors
            last = _classify_local(exc, model) if provider.local else _classify(exc, model)
            if isinstance(exc, litellm.NotFoundError) and not provider.local:
                # Deliberately not for a local endpoint: there is one slug, and
                # a 404 there means the URL or the name is wrong, not that a
                # provider retired the model. Blacklisting it would leave the
                # slug dead after the user fixed the actual mistake.
                model_health.mark_dead(model)
                continue  # a retired slug says nothing about the key — try the next
            break

        if provider.local:
            shown = model.removeprefix("openai/")  # litellm's prefix, not the user's
            return {"ok": True, "level": "ok", "detail": f"Endpoint běží a {shown} odpovídá."}
        return {"ok": True, "level": "ok", "detail": f"Klíč funguje. Odpovědělo {model}."}

    ok, level, detail = last or (False, "error", "Test selhal.")
    return {"ok": ok, "level": level, "detail": detail}
