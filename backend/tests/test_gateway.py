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


def test_unknown_model_is_refused(client, gateway):
    msg = [{"role": "user", "content": "hi"}]
    assert client.post("/v1/chat/completions", headers=AUTH, json={"model": "x/y", "messages": msg}).status_code == 404


# ------------------------------------------------------------- streaming


class _Chunk:
    def __init__(self, text: str | None, finish: str | None = None) -> None:
        self.text, self.finish = text, finish

    def model_dump(self) -> dict:
        return {
            "id": "chatcmpl-x",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": self.text}, "finish_reason": self.finish}],
        }


def _fake_stream(monkeypatch, calls, fail_on=(), break_after_first=False):
    async def acompletion(**kwargs):
        calls.append(kwargs)
        if kwargs["model"].split("/")[0] in fail_on:
            raise litellm.RateLimitError(
                message="Rate limit reached. Please try again in 20s.",
                llm_provider="groq",
                model=kwargs["model"],
            )

        async def chunks():
            yield _Chunk("Hel")
            if break_after_first:
                raise litellm.APIConnectionError(
                    message="reset, key gsk-test", llm_provider="groq", model=kwargs["model"]
                )
            yield _Chunk("lo")
            yield _Chunk(None, "stop")

        return chunks()

    monkeypatch.setattr("app.api.gateway.litellm.acompletion", acompletion)


def _events(text: str) -> list[str]:
    return [line[len("data: "):] for line in text.splitlines() if line.startswith("data: ")]


def _stream(client, model):
    return client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={"model": model, "messages": [{"role": "user", "content": "hi"}], "stream": True},
    )


def test_streaming_sends_openai_chunks_and_done(client, gateway, monkeypatch):
    import json

    _fake_stream(monkeypatch, gateway)
    r = _stream(client, GROQ)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    assert "".join(c["choices"][0]["delta"]["content"] or "" for c in chunks) == "Hello"
    assert all(c["model"] == GROQ for c in chunks)
    assert gateway[0]["stream"] is True


def test_streaming_failure_before_the_first_chunk_is_a_plain_error(client, gateway, monkeypatch):
    _fake_stream(monkeypatch, gateway, fail_on={"groq"})
    r = _stream(client, GROQ)
    assert r.status_code == 429
    assert r.headers["retry-after"] == "20"
    assert len(gateway) == 1


def test_streaming_auto_falls_back_before_the_first_chunk(client, gateway, monkeypatch):
    _fake_stream(monkeypatch, gateway, fail_on={"groq"})
    r = _stream(client, "auto")
    assert r.status_code == 200
    assert _events(r.text)[-1] == "[DONE]"
    assert [c["model"].split("/")[0] for c in gateway] == ["groq", "cerebras"]


def test_streaming_break_mid_answer_ends_with_an_error_event_without_the_key(client, gateway, monkeypatch):
    import json

    _fake_stream(monkeypatch, gateway, break_after_first=True)
    r = _stream(client, GROQ)
    assert r.status_code == 200
    events = _events(r.text)
    assert json.loads(events[0])["choices"][0]["delta"]["content"] == "Hel"
    assert json.loads(events[-1])["error"]["message"] == "groq: APIConnectionError"
    assert "[DONE]" not in events
    assert "gsk-test" not in r.text


# ---------------------------------------------------------- token limits


def _limit_sent(client, calls, **limit) -> dict:
    r = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={"model": GROQ, "messages": [{"role": "user", "content": "hi"}], **limit},
    )
    assert r.status_code == 200
    return {k: v for k, v in calls[-1].items() if k in ("max_tokens", "max_completion_tokens")}


def test_small_max_tokens_leaves_a_reasoning_model_room_to_answer(client, gateway, monkeypatch):
    # Found 2026-11-15: `max_tokens: 10` on gpt-oss-120b came back 200 with empty
    # content, because the model spent all ten tokens reasoning.
    from app.api.gateway import REASONING_MIN_TOKENS

    _fake(monkeypatch, gateway)
    monkeypatch.setattr("app.api.gateway.litellm.supports_reasoning", lambda model: True)
    assert _limit_sent(client, gateway, max_tokens=10) == {"max_tokens": REASONING_MIN_TOKENS}
    assert _limit_sent(client, gateway, max_tokens=5000) == {"max_tokens": 5000}
    assert _limit_sent(client, gateway) == {}


def test_max_tokens_is_untouched_on_a_model_that_does_not_reason(client, gateway, monkeypatch):
    _fake(monkeypatch, gateway)
    monkeypatch.setattr("app.api.gateway.litellm.supports_reasoning", lambda model: False)
    assert _limit_sent(client, gateway, max_tokens=10) == {"max_tokens": 10}


def test_max_completion_tokens_is_a_hard_limit_even_when_reasoning(client, gateway, monkeypatch):
    _fake(monkeypatch, gateway)
    monkeypatch.setattr("app.api.gateway.litellm.supports_reasoning", lambda model: True)
    assert _limit_sent(client, gateway, max_completion_tokens=10) == {"max_tokens": 10}
    assert _limit_sent(client, gateway, max_completion_tokens=10, max_tokens=50) == {"max_tokens": 10}


def test_streaming_gets_the_same_room(client, gateway, monkeypatch):
    from app.api.gateway import REASONING_MIN_TOKENS

    _fake_stream(monkeypatch, gateway)
    monkeypatch.setattr("app.api.gateway.litellm.supports_reasoning", lambda model: True)
    r = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={"model": GROQ, "messages": [{"role": "user", "content": "hi"}], "stream": True, "max_tokens": 10},
    )
    assert r.status_code == 200
    assert gateway[0]["max_tokens"] == REASONING_MIN_TOKENS


def test_a_reasoning_model_litellm_does_not_know_still_gets_room():
    # Found by tools/probe_gateway.py on 2026-11-16: glm-4.5-flash thinks before it
    # answers, litellm 1.98.0 says it does not, and `max_tokens: 32` came back empty.
    from app.api.gateway import _reasons

    assert _reasons("zai/glm-4.5-flash")
    assert not _reasons("mistral/codestral-latest")
