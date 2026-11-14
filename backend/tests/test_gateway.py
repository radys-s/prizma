"""The OpenAI-compatible `/v1` endpoint (`app/api/gateway.py`)."""

import litellm
import pytest

TOKEN = "test-gateway-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
GROQ = "groq/openai/gpt-oss-120b"


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def model_dump(self) -> dict:
        return {
            "object": "chat.completion",
            "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": self.text}}
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }


@pytest.fixture()
def gateway(monkeypatch):
    from app.config import settings
    from app.providers import model_health
    from app.routing import selector

    monkeypatch.setattr(settings, "gateway_token", TOKEN)
    monkeypatch.setenv("PRIZMA_KEY_GROQ", "gsk-test")
    monkeypatch.setenv("PRIZMA_KEY_CEREBRAS", "csk-test")
    selector.reset_cooldowns()
    model_health._dead.clear()
    calls: list[dict] = []
    yield calls
    selector.reset_cooldowns()
    model_health._dead.clear()


def _fake(monkeypatch, calls, fail_on=()):
    async def acompletion(**kwargs):
        calls.append(kwargs)
        if kwargs["model"].split("/")[0] in fail_on:
            raise litellm.RateLimitError(
                message="Rate limit reached. Please try again in 20s.",
                llm_provider="groq",
                model=kwargs["model"],
            )
        return _Response(f"from {kwargs['model']}")

    monkeypatch.setattr("app.api.gateway.litellm.acompletion", acompletion)


def test_endpoint_is_absent_without_a_token(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "gateway_token", "")
    assert client.get("/v1/models", headers=AUTH).status_code == 404


def test_wrong_or_missing_token_is_refused(client, gateway):
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_only_this_machine_off_a_public_server(client, gateway, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "public_mode", False)
    # TestClient's peer is "testclient", which is not loopback.
    assert client.get("/v1/models", headers=AUTH).status_code == 403


def test_models_lists_auto_and_configured_slugs_only(client, gateway):
    ids = [m["id"] for m in client.get("/v1/models", headers=AUTH).json()["data"]]
    assert ids[0] == "auto"
    assert GROQ in ids
    assert "cerebras/gpt-oss-120b" in ids
    assert not any(i.startswith("gemini/") for i in ids)  # no key configured
    assert not any(":search" in i for i in ids)


def test_explicit_slug_is_called_once_with_tools(client, gateway, monkeypatch):
    _fake(monkeypatch, gateway)
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    r = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={"model": GROQ, "messages": [{"role": "user", "content": "hi"}], "tools": tools},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["model"] == GROQ and data["provider"] == "groq"
    assert data["choices"][0]["message"]["content"] == f"from {GROQ}"
    assert len(gateway) == 1
    assert gateway[0]["tools"] == tools and gateway[0]["tool_choice"] == "auto"
    assert gateway[0]["api_key"] == "gsk-test"


def test_explicit_slug_failure_is_not_substituted(client, gateway, monkeypatch):
    _fake(monkeypatch, gateway, fail_on={"groq"})
    r = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={"model": GROQ, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 429
    assert r.headers["retry-after"] == "20"
    assert "gsk-test" not in r.text
    assert len(gateway) == 1


def test_auto_falls_back_to_the_next_provider(client, gateway, monkeypatch):
    _fake(monkeypatch, gateway, fail_on={"groq"})
    r = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    assert r.json()["provider"] == "cerebras"


def test_unknown_model_and_streaming_are_refused(client, gateway):
    msg = [{"role": "user", "content": "hi"}]
    assert client.post("/v1/chat/completions", headers=AUTH, json={"model": "x/y", "messages": msg}).status_code == 404
    r = client.post("/v1/chat/completions", headers=AUTH, json={"model": GROQ, "messages": msg, "stream": True})
    assert r.status_code == 400
