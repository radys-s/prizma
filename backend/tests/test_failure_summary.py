"""The "nobody answered" message has to end with what to do next.

It used to list each provider's failure and stop there, so a user who hit two
rate limits, or one rejected key, was left to guess whether to wait, retry or
fix a key. The last line now says which.
"""

import litellm

from app.providers.client import Attempt, _summarize
from app.providers.registry import BY_ID


def _attempt(provider_id: str, exc: Exception) -> Attempt:
    provider = BY_ID[provider_id]
    return Attempt(provider=provider, model=provider.models[0], error=exc)


def _rate_limited(provider_id: str) -> Attempt:
    return _attempt(
        provider_id,
        litellm.RateLimitError(message="limit", llm_provider=provider_id, model="m"),
    )


def _rejected(provider_id: str) -> Attempt:
    return _attempt(
        provider_id,
        litellm.AuthenticationError(message="bad key", llm_provider=provider_id, model="m"),
    )


def test_rate_limits_say_wait_or_add_a_key():
    text = _summarize([_rate_limited("groq"), _rate_limited("cerebras")])
    last = text.strip().splitlines()[-1]
    assert "za chvíli" in last
    assert "API klíče" in last


def test_a_rejected_key_says_to_fix_that_key():
    text = _summarize([_rate_limited("groq"), _rejected("cerebras")])
    last = text.strip().splitlines()[-1]
    assert "Cerebras" in last
    assert "API klíče" in last


def test_each_failure_stays_on_its_own_line():
    text = _summarize([_rate_limited("groq"), _rejected("cerebras")])
    lines = text.splitlines()
    assert any(line.startswith("- Groq") for line in lines)
    assert any(line.startswith("- Cerebras") for line in lines)
