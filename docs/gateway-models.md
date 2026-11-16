# Gateway models: what answers

Every model `/v1/models` listed on hub2, probed on 2026-11-16 (11:07 to 11:19 CET) with one
request each: `max_tokens: 32`, prompt "Reply with the single word: ready", one model at a
time with a 4 second pause. Made with `python tools/probe_gateway.py`; run it again for a
fresh table.

- **works**: an answer with text in it.
- **empty**: HTTP 200 but no text.
- **not found**: the provider answered 404 or 410.
- **error**: anything else. The note has the status and the provider's error class.

35 models: 14 work, 1 empty, 6 not found, 14 error.

| Model | Result | Note | Seconds |
|---|---|---|---|
| `auto` | works | finish `stop`, answered by `groq/openai/gpt-oss-120b` | 0.6 |
| `groq/openai/gpt-oss-120b` | works | finish `stop` | 0.4 |
| `groq/qwen/qwen3.8-27b` | works | finish `stop` | 0.1 |
| `groq/openai/gpt-oss-20b` | works | finish `stop` | 0.2 |
| `gemini/gemini-3.5-flash` | works | finish `stop` | 1.5 |
| `gemini/gemini-flash-latest` | works | finish `stop` | 118.7 |
| `gemini/gemini-3.7-flash` | error | no response `TimeoutError` | 120.0 |
| `mistral/codestral-latest` | works | finish `stop` | 0.4 |
| `mistral/devstral-medium-latest` | error | 429 `RateLimitError` | 0.2 |
| `mistral/mistral-code-latest` | works | finish `stop` | 0.9 |
| `mistral/mistral-small-latest` | error | 429 `RateLimitError` | 0.2 |
| `mistral/mistral-medium-latest` | error | 429 `RateLimitError` | 0.2 |
| `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` | not found | 404 `NotFoundError` | 0.3 |
| `openrouter/nvidia/nemotron-3.5-lightning:free` | not found | 404 `NotFoundError` | 0.7 |
| `openrouter/google/gemma-4-31b-it:free` | error | 429 `RateLimitError` | 0.3 |
| `cerebras/gpt-oss-120b` | error | 402 `APIError` | 0.8 |
| `cerebras/gemma-4-31b` | not found | 404 `NotFoundError` | 0.3 |
| `github/gpt-4.1` | error | 500 `InternalServerError` | 1.5 |
| `github/gpt-4o` | error | 500 `InternalServerError` | 1.4 |
| `github/o4-mini` | error | 500 `InternalServerError` | 1.5 |
| `github/Llama-4-Scout-17B-16E-Instruct` | error | 500 `InternalServerError` | 1.6 |
| `nvidia_nim/moonshotai/kimi-k2.6` | not found | 410 `APIError` | 0.2 |
| `nvidia_nim/deepseek-ai/deepseek-v4.1-flash` | error | no response `TimeoutError` | 120.0 |
| `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` | works | finish `stop` | 1.1 |
| `nvidia_nim/nvidia/nemotron-3-super-120b-a12b` | not found | 410 `APIError` | 0.1 |
| `nvidia_nim/nvidia/llama-3.1-nemotron-ultra-253b-v1` | not found | 410 `APIError` | 0.1 |
| `zai/glm-4.7-flash` | works | finish `stop` | 36.8 |
| `zai/glm-4.5-flash` | empty | finish `length` | 5.3 |
| `cohere_chat/command-a-plus-05-2026` | works | finish `stop` | 0.5 |
| `cohere_chat/command-a-03-2025` | works | finish `stop` | 0.3 |
| `cohere_chat/command-r-plus-08-2024` | works | finish `stop` | 0.8 |
| `cohere_chat/command-r7b-12-2024` | works | finish `stop` | 0.3 |
| `huggingface/deepseek-ai/DeepSeek-V3.2` | error | 402 `APIError` | 0.7 |
| `huggingface/Qwen/Qwen3.5-235B-A22B-Instruct` | error | 400 `BadRequestError` | 0.6 |
| `huggingface/meta-llama/Llama-4-Scout-17B-16E-Instruct` | error | 402 `APIError` | 0.6 |

## Second try

One failed request says little, so the models that failed with a rate limit, a timeout or a
server error got a second request ten minutes later. None recovered:

| Model | Result | Note | Seconds |
|---|---|---|---|
| `mistral/devstral-medium-latest` | error | 429 `RateLimitError` | 0.2 |
| `mistral/mistral-small-latest` | error | 429 `RateLimitError` | 0.2 |
| `mistral/mistral-medium-latest` | error | 429 `RateLimitError` | 0.8 |
| `openrouter/google/gemma-4-31b-it:free` | error | 429 `RateLimitError` | 0.3 |
| `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` | not found | 404 `NotFoundError` | 0.1 |
| `gemini/gemini-3.7-flash` | error | no response `TimeoutError` | 120.1 |
| `nvidia_nim/deepseek-ai/deepseek-v4.1-flash` | error | no response `TimeoutError` | 120.1 |
| `github/gpt-4.1` | error | 500 `InternalServerError` | 1.5 |
| `huggingface/Qwen/Qwen3.5-235B-A22B-Instruct` | error | 400 `BadRequestError` | 0.7 |

## What was done about it

**Removed from the registry: four retired models.** Each answered 404 or 410 and is also
missing from its provider's public model list, so this is not a bad minute:

| Model | Provider said | Provider's list |
|---|---|---|
| `cerebras/gemma-4-31b` | 404 | not in api.cerebras.ai/public/v1/models |
| `nvidia_nim/moonshotai/kimi-k2.6` | 410 | not in integrate.api.nvidia.com/v1/models, and no other Kimi model is |
| `nvidia_nim/nvidia/nemotron-3-super-120b-a12b` | 410 | not in integrate.api.nvidia.com/v1/models |
| `nvidia_nim/nvidia/llama-3.1-nemotron-ultra-253b-v1` | 410 | not in integrate.api.nvidia.com/v1/models |

Kimi was the only model of the "NVIDIA · Kimi" entry, so that entry is gone too. The other
NVIDIA entries keep working with the same key.

**Fixed: the one empty answer.** `zai/glm-4.5-flash` reasons before it answers, but litellm
1.98.0 does not list it as a reasoning model, so the gateway did not raise its small
`max_tokens`. It is now named in the gateway (`_REASONS_UNLISTED`). With `max_tokens: 1024`
it answered "ready" in 136 tokens.

**Left alone: everything else.** These failed, but nothing says the model is gone:

- `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` and `nemotron-3.5-lightning:free`
  answer 404 although OpenRouter's public list still has both. OpenRouter answers 404 for a
  listed model when the account's privacy settings exclude every endpoint that serves it,
  which is the likely cause here. Check openrouter.ai/settings/privacy.
- `mistral/mistral-small-latest`, `mistral-medium-latest`, `devstral-medium-latest`: 429 on
  both tries while `codestral-latest` and `mistral-code-latest` work. A used-up quota on the
  general tier, not a missing model.
- `openrouter/google/gemma-4-31b-it:free`: 429 on both tries (OpenRouter's daily free limit).
- `cerebras/gpt-oss-120b`: 402. Cerebras wants a verified card, as the registry already notes.
- `huggingface/*`: 402 on two models (the monthly free credits are used up) and 400 on
  `Qwen3.5-235B-A22B-Instruct`.
- `github/*`: 500 on all four models, so it is the account or the token, not one model. The
  registry marks these slugs as never verified.
- `gemini/gemini-3.7-flash` and `nvidia_nim/deepseek-ai/deepseek-v4.1-flash`: no answer within
  120 seconds, twice. Both are in their provider's list. `gemini/gemini-flash-latest` did
  answer, after 119 seconds.

The slow ones matter to callers: the gateway sets no time limit of its own on a provider call,
so a client without a timeout waits as long as the provider keeps the connection open.
