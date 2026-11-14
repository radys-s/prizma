"""OpenAI-compatible endpoint, so other tools can use the keys stored here.

AI Bridge Studio and the agents on the hub call `/v1/chat/completions` here
instead of holding provider keys themselves. The keys never leave this process:
they are used server-side, exactly as the chat uses them.

Two kinds of model id:

- **A registry slug** (`groq/openai/gpt-oss-120b`): that one model, called once,
  no fallback. The caller owns fallback, as Studio's router does, and a second
  owner here would hide which model actually answered.
- **`auto`**: this app's own chain, best configured provider first.

Access needs `GATEWAY_TOKEN`. With no token set the endpoint does not exist.
Off a public server it also answers only to this machine, like the rest of the
API (`auth/guard.py`, layer 1).
"""

import hmac
import time
import uuid

import litellm
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.auth.guard import LOOPBACK_HOSTS
from app.config import settings
from app.providers import failures, keystore, registry
from app.providers.registry import ProviderModel
from app.routing import selector

AUTO = "auto"


def require_token(request: Request) -> None:
    if not settings.gateway_token:
        raise HTTPException(404)
    if not settings.public_mode:
        peer = request.client.host if request.client else None
        if peer not in LOOPBACK_HOSTS:
            raise HTTPException(403, "The gateway answers only to this machine")
    sent = request.headers.get("authorization", "").encode()
    if not hmac.compare_digest(sent, f"Bearer {settings.gateway_token}".encode()):
        raise HTTPException(401, "Wrong or missing gateway token")


router = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])


def _answering() -> list[ProviderModel]:
    """Configured providers that are language models, not search APIs."""
    return [
        p
        for p in registry.active()
        if not p.search_backend
        and any(c != "search" for c in p.capabilities)
        and selector.usable(p)
    ]


def _targets(model: str) -> list[tuple[ProviderModel, str]]:
    if model == AUTO:
        return [
            (p, p.primary_model)
            for p in selector.candidates("general")
            if not p.search_backend
        ]
    return [(p, model) for p in _answering() if model in p.models][:1]


@router.get("/models")
def models() -> dict:
    ids = [AUTO, *dict.fromkeys(slug for p in _answering() for slug in p.models)]
    return {"object": "list", "data": [{"id": i, "object": "model", "owned_by": "refrakt"} for i in ids]}


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    messages: list[dict]
    tools: list[dict] | None = None
    tool_choice: str | dict | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    stream: bool = False


def _error(status: int, message: str, retry_after: float | None = None) -> JSONResponse:
    headers = {"Retry-After": str(int(retry_after))} if retry_after else None
    return JSONResponse(
        {"error": {"message": message, "type": "gateway_error"}},
        status_code=status,
        headers=headers,
    )


@router.post("/chat/completions")
async def chat_completions(body: ChatRequest):
    if body.stream:
        return _error(400, "Streaming is not supported; send stream=false")
    targets = _targets(body.model)
    if not targets:
        return _error(404, f"Model {body.model!r} is not configured here")

    extra: dict = {}
    if body.tools:
        extra["tools"] = body.tools
        extra["tool_choice"] = body.tool_choice or "auto"
    for name in ("max_tokens", "temperature"):
        if getattr(body, name) is not None:
            extra[name] = getattr(body, name)

    failure: tuple[ProviderModel, BaseException] | None = None
    for provider, slug in targets:
        try:
            response = await litellm.acompletion(
                model=slug, **keystore.endpoint(provider), messages=body.messages, **extra
            )
        except Exception as exc:  # noqa: BLE001 - every provider error is reported, none raised
            failures.record(exc, provider_id=provider.id, model=slug)
            failure = (provider, exc)
            continue
        data = response.model_dump()
        data.setdefault("id", f"chatcmpl-{uuid.uuid4().hex}")
        data.setdefault("created", int(time.time()))
        data["model"] = slug
        data["provider"] = provider.id
        return data

    assert failure is not None
    provider, exc = failure
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int) or not 400 <= status < 600:
        status = 502
    # The class name only: a provider's own message can echo part of the key.
    return _error(
        status,
        f"{provider.id}: {type(exc).__name__}",
        failures.retry_after_seconds(exc),
    )
