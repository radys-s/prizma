"""Heuristic request classifier.

v1 on purpose: keyword/shape rules, not a trained model. It picks one
capability tag (see providers.registry.CAPABILITIES) per incoming message.
Swap this for a small trained classifier later without touching the
selector or the API layer — the contract is just str -> str.

Since 2026-08-28 this is also the fallback for `routing/plan.py` and the whole
of the `light` effort tier, so its misses are visible rather than academic:
"Napiš mi v Pythonu funkci…" used to land on "fast" because the old pattern
required "napiš" and "funkci" to be adjacent.

It never returns "search", even though that is a valid capability tag in the
registry. Deciding that a question needs the live internet takes reading it —
a keyword like "aktuální" appears in plenty of questions a model can answer
from weights, and a web round-trip costs both latency and a daily quota. Only
the planner may ask for the web, which is also what keeps the `light` tier
(heuristic only) predictably offline.
"""

import re

_CODE_MARKERS = re.compile(
    r"```|\bdef \b|\bclass \b|\bfunction\b|\bimport \b"
    # "napiš …" and the noun, with room for a few words in between, but not
    # across sentence boundaries.
    r"|napi[šs][^.?!\n]{0,40}\b(kód|funkci|třídu|metodu|skript|program|dotaz)"
    r"|\b(python|javascript|typescript|java|kotlin|rust|golang|sql|bash|"
    r"powershell|react|fastapi|docker)\b"
    r"|debug|traceback|exception|stack ?trace|refaktoruj|regex|API endpoint",
    re.IGNORECASE,
)
_CREATIVE_MARKERS = re.compile(
    r"báseň|povídk|esej|slogan|příběh|creative|story|poem|marketing text|scénář",
    re.IGNORECASE,
)
_LONG_CONTEXT_THRESHOLD_CHARS = 4000


def classify(message: str, history_chars: int = 0) -> str:
    if _CODE_MARKERS.search(message):
        return "code"
    if len(message) + history_chars > _LONG_CONTEXT_THRESHOLD_CHARS:
        return "long_context"
    if _CREATIVE_MARKERS.search(message):
        return "creative"
    if len(message) < 200:
        return "fast"
    return "general"
