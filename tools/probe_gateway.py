#!/usr/bin/env python3
"""Send one tiny request to every model the gateway lists and print a Markdown table.

    python tools/probe_gateway.py > table.md
    python tools/probe_gateway.py mistral/mistral-small-latest zai/glm-4.5-flash

With no arguments it probes everything in `/v1/models`; with arguments, those models.

One request per model, one at a time, with a pause between them: the free tiers
behind the gateway are rate-limited per provider. A 429 is retried once after the
provider's `Retry-After`. The token comes from REFRAKT_GATEWAY_TOKEN or from
~/.config/refrakt/gateway.token and is never printed.

Results:
  works      an answer with text in it
  empty      HTTP 200 but no text (the finish reason is in the note)
  not found  the provider answered 404 or 410. Often a retired model, but check the
             provider's own model list before removing it: OpenRouter also answers
             404 for a listed model that the account's privacy settings exclude.
  error      anything else; the note has the status and the provider's error class
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("REFRAKT_GATEWAY_URL", "http://127.0.0.1:8000")
PAUSE_SECONDS = 4
MAX_RETRY_WAIT = 60
# Small on purpose: this is the kind of budget an agent sets for a title or a
# yes/no check, and the one that used to come back empty from reasoning models.
MAX_TOKENS = 32
PROMPT = "Reply with the single word: ready"


def _token() -> str:
    token = os.environ.get("REFRAKT_GATEWAY_TOKEN", "").strip()
    return token or (Path.home() / ".config/refrakt/gateway.token").read_text().strip()


def _call(path: str, token: str, body: dict | None = None) -> tuple[int, dict, dict]:
    request = urllib.request.Request(
        BASE + path,
        json.dumps(body).encode() if body else None,
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.load(response), dict(response.headers)
    except urllib.error.HTTPError as exc:
        try:
            data = json.load(exc)
        except ValueError:
            data = {}
        return exc.code, data, dict(exc.headers)
    except (OSError, ValueError) as exc:  # timeout, refused connection, not JSON
        return 0, {"error": {"message": type(exc).__name__}}, {}


def probe(model: str, token: str) -> tuple[str, str, float]:
    body = {"model": model, "max_tokens": MAX_TOKENS, "messages": [{"role": "user", "content": PROMPT}]}
    for attempt in (1, 2):
        started = time.monotonic()
        status, data, headers = _call("/v1/chat/completions", token, body)
        seconds = time.monotonic() - started
        if status != 429 or attempt == 2:
            break
        time.sleep(min(float(headers.get("Retry-After") or 20), MAX_RETRY_WAIT))

    if status == 200:
        choice = data["choices"][0]
        message = choice["message"]
        answered_by = f", answered by `{data['model']}`" if data.get("model") != model else ""
        if (message.get("content") or "").strip() or message.get("tool_calls"):
            return "works", f"finish `{choice['finish_reason']}`{answered_by}", seconds
        return "empty", f"finish `{choice['finish_reason']}`{answered_by}", seconds
    error = (data.get("error") or {}).get("message", "")
    # "provider: ErrorClass" from the gateway; the class is the useful part.
    note = f"{status or 'no response'} `{error.split(': ')[-1]}`"
    return ("not found" if status in (404, 410) else "error"), note, seconds


def main() -> None:
    token = _token()
    status, data, _ = _call("/v1/models", token)
    if status != 200:
        raise SystemExit(f"/v1/models answered {status or 'nothing'}; is the gateway running at {BASE}?")
    models = sys.argv[1:] or [m["id"] for m in data["data"]]

    print("| Model | Result | Note | Seconds |")
    print("|---|---|---|---|")
    counts: dict[str, int] = {}
    for model in models:
        result, note, seconds = probe(model, token)
        counts[result] = counts.get(result, 0) + 1
        print(f"| `{model}` | {result} | {note} | {seconds:.1f} |", flush=True)
        print(f"{model}: {result}", file=sys.stderr)
        time.sleep(PAUSE_SECONDS)
    summary = ", ".join(f"{n} {result}" for result, n in sorted(counts.items()))
    print(f"\n{len(models)} models probed with `max_tokens: {MAX_TOKENS}`: {summary}.")


if __name__ == "__main__":
    main()
