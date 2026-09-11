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
    # Kolik kol volání nástrojů smí jedna odpověď spotřebovat, když má model
    # přístup k počítači. Dřív to byla jedna konstanta (6) pro všechny tiery —
    # a to je současně moc i málo: na „přečti soubor a shrň ho" je šest kol
    # zbytečný strop, na „najdi, kde se to počítá, oprav to a spusť testy" jich
    # je potřeba spíš dvacet. Reálná změna v repu se do šesti kol nevejde,
    # takže model odpovídal z poloviny přečteného kódu.
    #
    # Strop musí zůstat, jen výš: model na free tieru se umí zacyklit na čtení
    # téhož souboru dokola a bez stropu by požadavek běžel, dokud si toho někdo
    # nevšimne. Každé kolo je navíc jeden request z denní kvóty.
    tool_rounds: int = 6

    # Kolik znaků smí historie nástrojového dialogu zabrat, než se starší
    # výsledky nahradí poznámkou o jejich délce. Vlastní číslo vedle
    # `tool_rounds`, protože strop kol a strop kontextu jsou dvě různé věci:
    # `compact.py` srovná konverzaci pod 12 000 znaků a pak do ní smyčka
    # přisype až 20 000 znaků za každý výsledek nástroje. Na free tieru je okno
    # 8k–32k tokenů, takže kolem pátého kola začne provider vracet 4xx uprostřed
    # rozepsané odpovědi — a protože model už něco „řekl", nepřepne se ani na
    # náhradní. Uživatel dostane surovou chybu místo odpovědi.
    tool_context_chars: int = 24_000


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
        tool_rounds=4,
        tool_context_chars=12_000,
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
        tool_rounds=10,
        tool_context_chars=24_000,
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
        tool_rounds=20,
        tool_context_chars=40_000,
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
        tool_rounds=30,
        tool_context_chars=60_000,
    ),
)

BY_ID: dict[str, EffortProfile] = {p.id: p for p in PROFILES}


def resolve(effort_id: str | None) -> EffortProfile:
    """Never raises: an unknown id falls back to the default tier.

    The id arrives from the browser's localStorage, which can hold a value from
    an older build. A stale picker setting must not cost the user an answer.
    """
    return BY_ID.get(effort_id or "", BY_ID[DEFAULT_EFFORT])
