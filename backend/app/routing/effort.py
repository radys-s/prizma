"""How hard the router is allowed to work on one request.

Four tiers. They are not cosmetic — each one changes three concrete things:
whether a planner model runs at all, what `reasoning_effort` is sent to the
executor, whether a hard task is split across several providers, and whether
the answer gets a second review pass.

`reasoning_effort` only reaches models that actually accept it. Probed against
litellm 1.98.0 on 2026-08-28, that is Groq's gpt-oss slugs, Gemini Flash and
Cerebras gpt-oss; everywhere else the parameter is filtered out by
`providers.client._supported_kwargs` and the tier degrades to prompt-level
control (the planner's directives get more demanding as the tier rises).

This table is the single source of truth: `GET /api/routing/options` serves it
straight to the UI, so the pickers can never drift from the behaviour.
"""

from dataclasses import dataclass

DEFAULT_EFFORT = "medium"


@dataclass(frozen=True)
class EffortProfile:
    id: str
    label: str
    description: str  # shown under the label in the picker, Czech
    use_planner: bool  # False -> skip the planner call, use the heuristic
    plan_detail: str  # "brief" | "full" — how much the planner writes
    reasoning_effort: str | None  # passed through only if the slug accepts it
    max_tokens: int
    review_pass: bool  # second call that critiques and rewrites the answer
    # Whether a task the planner rated "hard" may be split into independent
    # parts, each answered by a different provider and merged afterwards
    # (routing/decompose.py). Costs one request per part out of a free
    # daily quota, so only the two deliberate tiers get it.
    decompose: bool = False


PROFILES: tuple[EffortProfile, ...] = (
    EffortProfile(
        id="light",
        label="Light",
        description="Bez plánovače, rovnou odpověď. Nejrychlejší.",
        use_planner=False,
        plan_detail="brief",
        reasoning_effort="low",
        max_tokens=1024,
        review_pass=False,
        decompose=False,
    ),
    EffortProfile(
        id="medium",
        label="Medium",
        description="Plánovač krátce rozmyslí zadání. Výchozí volba.",
        use_planner=True,
        plan_detail="brief",
        reasoning_effort="medium",
        max_tokens=2048,
        review_pass=False,
        decompose=False,
    ),
    EffortProfile(
        id="pro",
        label="Pro",
        description="Podrobný plán a delší odpověď. Složitý úkol rozdělí mezi víc modelů.",
        use_planner=True,
        plan_detail="full",
        reasoning_effort="high",
        max_tokens=4096,
        review_pass=False,
        decompose=True,
    ),
    EffortProfile(
        id="max",
        label="Max",
        description="Rozdělení mezi modely → odpověď → revize. Nejlepší výsledek, trvá nejdéle.",
        use_planner=True,
        plan_detail="full",
        reasoning_effort="high",
        max_tokens=8192,
        review_pass=True,
        decompose=True,
    ),
)

BY_ID: dict[str, EffortProfile] = {p.id: p for p in PROFILES}


def resolve(effort_id: str | None) -> EffortProfile:
    """Never raises: an unknown id falls back to the default tier.

    The id arrives from the browser's localStorage, which can hold a value from
    an older build. A stale picker setting must not cost the user an answer.
    """
    return BY_ID.get(effort_id or "", BY_ID[DEFAULT_EFFORT])
