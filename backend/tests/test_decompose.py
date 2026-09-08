"""The fan-out stage: split a hard task, one model per part, merge.

The tests that matter here are the negative ones. `decompose.run()` promises it
never raises and never blocks past its budget, and everything downstream —
including the user getting an answer at all — rests on that promise holding
when the splitter returns garbage, when providers die, and when the whole
stage runs long.
"""

import asyncio

import pytest

from app.providers.registry import ProviderModel
from app.routing import decompose, effort
from app.routing.plan import Plan

HARD = Plan(
    capability="general",
    complexity="hard",
    goal="cíl",
    directives="pokyny",
    source="model",
)
MODERATE = Plan(
    capability="general",
    complexity="moderate",
    goal="",
    directives="",
    source="heuristic",
)
MESSAGES = [{"role": "user", "content": "Navrhni architekturu a projdi rizika."}]


def provider(name: str) -> ProviderModel:
    return ProviderModel(
        id=name,
        display_name=name.title(),
        models=(f"{name}/model",),
        capabilities=("general", "code"),
        signup_url="https://example.invalid",
    )


def subtask(index: int) -> decompose.Subtask:
    return decompose.Subtask(
        index=index, title=f"Část {index}", prompt="x" * 40, capability="general"
    )


def drain(agen) -> list[decompose.Event]:
    async def collect():
        return [event async for event in agen]

    return asyncio.run(collect())


# ------------------------------------------------------------------ gating


@pytest.mark.parametrize(
    "tier,expected", [("light", False), ("medium", False), ("pro", True), ("max", True)]
)
def test_only_the_deliberate_tiers_split(tier, expected):
    assert decompose.should_split(HARD, effort.resolve(tier)) is expected


def test_a_moderate_task_is_never_split():
    """The heuristic plan is always "moderate", so a planner outage disables
    the stage rather than fanning out on a guess."""
    for tier in ("light", "medium", "pro", "max"):
        assert decompose.should_split(MODERATE, effort.resolve(tier)) is False


def test_a_disabled_tier_yields_no_events_at_all():
    assert drain(decompose.run(MESSAGES, HARD, effort.resolve("light"))) == []


# ------------------------------------------------- validating the splitter


def test_bad_capability_falls_back_to_general():
    parts = decompose._build_subtasks(
        {
            "parts": [
                {"title": "A", "prompt": "y" * 40, "capability": "nonsense"},
                {"title": "B", "prompt": "y" * 40, "capability": "code"},
            ]
        }
    )
    assert [p.capability for p in parts] == ["general", "code"]


def test_a_title_returned_as_an_array_is_flattened():
    """Models hand back arrays where a string was asked for. Without this the
    UI would show Python list syntax as the step label."""
    parts = decompose._build_subtasks(
        {
            "parts": [
                {"title": ["Bezpečnost", "API"], "prompt": "y" * 40},
                {"title": "B", "prompt": "y" * 40},
            ]
        }
    )
    assert parts[0].title == "Bezpečnost API"


def test_an_unactionable_part_is_dropped_and_takes_the_split_with_it():
    parts = decompose._build_subtasks(
        {"parts": [{"title": "A", "prompt": "krátké"}, {"title": "B", "prompt": "y" * 40}]}
    )
    assert parts == ()  # one survivor is not a decomposition


@pytest.mark.parametrize(
    "payload", [{}, {"parts": []}, {"parts": "nope"}, {"parts": [1, 2]}, {"parts": [{}]}]
)
def test_garbage_from_the_splitter_produces_no_parts(payload):
    assert decompose._build_subtasks(payload) == ()


def test_more_parts_than_the_cap_are_truncated():
    raw = [{"title": f"T{i}", "prompt": "y" * 40} for i in range(9)]
    assert len(decompose._build_subtasks({"parts": raw})) == decompose.MAX_PARTS


# ------------------------------------------------------ provider assignment


def test_each_part_goes_to_a_different_provider(monkeypatch):
    pool = [provider("alfa"), provider("beta"), provider("gama")]
    monkeypatch.setattr(decompose.selector, "candidates", lambda *a, **k: pool)

    pairs = decompose._assign((subtask(1), subtask(2), subtask(3)))
    assert [p.id for _, p in pairs] == ["alfa", "beta", "gama"]


def test_fewer_providers_than_parts_reuses_rather_than_dropping_a_part(monkeypatch):
    """A duplicate model still answers a different question. An unanswered
    part is a hole in the merged answer."""
    pool = [provider("alfa"), provider("beta")]
    monkeypatch.setattr(decompose.selector, "candidates", lambda *a, **k: pool)

    pairs = decompose._assign((subtask(1), subtask(2), subtask(3)))
    assert len(pairs) == 3
    assert [p.id for _, p in pairs] == ["alfa", "beta", "alfa"]


def test_no_providers_at_all_assigns_nothing(monkeypatch):
    monkeypatch.setattr(decompose.selector, "candidates", lambda *a, **k: [])
    assert decompose._assign((subtask(1), subtask(2))) == []


# ------------------------------------------------------------- the pipeline


def wire(monkeypatch, *, parts, runner, pool=None):
    pool = pool or [provider("alfa"), provider("beta"), provider("gama")]

    async def splitter(messages, plan):
        return parts, "splitter/model"

    monkeypatch.setattr(decompose, "_ask_splitter", splitter)
    monkeypatch.setattr(decompose.selector, "candidates", lambda *a, **k: pool)
    monkeypatch.setattr(decompose, "_run_part", runner)


def ok_result(sub, prov, text="odpověď") -> decompose.PartResult:
    return decompose.PartResult(
        subtask=sub,
        provider_id=prov.id,
        display_name=prov.display_name,
        model=prov.models[0],
        text=text,
        error="",
        ms=10,
    )


def test_a_full_run_reports_every_part_and_ends_with_a_breakdown(monkeypatch):
    async def runner(sub, prov, profile):
        # Reverse order on purpose: results must be usable as they land, and
        # the breakdown must still come out in the splitter's order.
        await asyncio.sleep(0.01 * (4 - sub.index))
        return ok_result(sub, prov, f"text {sub.index}")

    wire(monkeypatch, parts=(subtask(1), subtask(2), subtask(3)), runner=runner)
    events = drain(decompose.run(MESSAGES, HARD, effort.resolve("pro")))

    assert [e.type for e in events] == ["split", "part_done", "part_done", "part_done", "done"]
    breakdown = events[-1].breakdown
    assert [r.subtask.index for r in breakdown.results] == [1, 2, 3]
    assert len({r.provider_id for r in breakdown.results}) == 3
    assert breakdown.usable


def test_one_dead_part_does_not_kill_the_others(monkeypatch):
    async def runner(sub, prov, profile):
        if sub.index == 2:
            return decompose.PartResult(sub, prov.id, prov.display_name, "", "", "boom", 5)
        return ok_result(sub, prov)

    wire(monkeypatch, parts=(subtask(1), subtask(2), subtask(3)), runner=runner)
    breakdown = drain(decompose.run(MESSAGES, HARD, effort.resolve("max")))[-1].breakdown

    assert [r.subtask.index for r in breakdown.done] == [1, 3]
    assert [r.subtask.index for r in breakdown.failed] == [2]
    assert breakdown.usable


def test_too_few_surviving_parts_aborts_instead_of_merging_one_answer(monkeypatch):
    async def runner(sub, prov, profile):
        if sub.index == 1:
            return ok_result(sub, prov)
        return decompose.PartResult(sub, prov.id, prov.display_name, "", "", "boom", 5)

    wire(monkeypatch, parts=(subtask(1), subtask(2)), runner=runner)
    events = drain(decompose.run(MESSAGES, HARD, effort.resolve("pro")))

    assert events[-1].type == "abort"
    assert all(e.breakdown is None for e in events)


def test_a_splitter_that_declines_aborts_cleanly(monkeypatch):
    wire(monkeypatch, parts=(), runner=None)
    events = drain(decompose.run(MESSAGES, HARD, effort.resolve("pro")))
    assert [e.type for e in events] == ["abort"]


def test_a_part_that_raises_never_escapes_the_stage(monkeypatch):
    async def runner(sub, prov, profile):
        raise RuntimeError("provider exploded")

    wire(monkeypatch, parts=(subtask(1), subtask(2)), runner=runner)
    events = drain(decompose.run(MESSAGES, HARD, effort.resolve("pro")))
    assert events[-1].type == "abort"


def test_the_stage_gives_up_at_its_budget_and_cancels_the_parts(monkeypatch):
    """A hung provider must cost the budget, not the answer — and the parts
    must actually stop, or they keep spending the user's quota."""
    started: list[int] = []
    cancelled: list[int] = []

    async def runner(sub, prov, profile):
        started.append(sub.index)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(sub.index)
            raise
        return ok_result(sub, prov)

    wire(monkeypatch, parts=(subtask(1), subtask(2)), runner=runner)
    monkeypatch.setattr(decompose, "_STAGE_BUDGET", 0.2)

    events = drain(decompose.run(MESSAGES, HARD, effort.resolve("pro")))
    assert events[-1].type == "abort"
    assert sorted(cancelled) == sorted(started)


# --------------------------------------------------------- the merge prompt


def breakdown_of(results) -> decompose.Breakdown:
    return decompose.Breakdown(
        subtasks=tuple(r.subtask for r in results),
        results=tuple(results),
        splitter_model="splitter/model",
    )


def test_the_merge_prompt_is_a_system_turn_carrying_every_part():
    pool = [provider("alfa"), provider("beta")]
    message = decompose.system_message(
        breakdown_of([ok_result(subtask(i + 1), p, f"zjištění {i + 1}") for i, p in enumerate(pool)])
    )

    assert message["role"] == "system"  # the user's own turn is never touched
    assert "zjištění 1" in message["content"]
    assert "zjištění 2" in message["content"]
    assert "Alfa" in message["content"] and "Beta" in message["content"]
    # The user asked a question, not for a progress report.
    assert "nezmiňuj" in message["content"]


def test_the_merge_prompt_names_the_parts_that_failed():
    alfa, beta, gama = provider("alfa"), provider("beta"), provider("gama")
    failed = decompose.PartResult(subtask(3), gama.id, gama.display_name, "", "", "boom", 5)
    message = decompose.system_message(
        breakdown_of([ok_result(subtask(1), alfa), ok_result(subtask(2), beta), failed])
    )
    assert "Část 3" in message["content"]
    assert "nepředstírej" in message["content"]


def test_one_usable_part_produces_no_merge_prompt():
    alfa = provider("alfa")
    assert decompose.system_message(breakdown_of([ok_result(subtask(1), alfa)])) is None


def test_a_long_part_is_truncated_before_it_reaches_the_executor():
    """Four parts at full length would be more system prompt than some
    free-tier context windows have room for."""
    alfa, beta = provider("alfa"), provider("beta")
    huge = "x" * (decompose._RESULT_CHARS * 3)
    message = decompose.system_message(
        breakdown_of([ok_result(subtask(1), alfa, huge), ok_result(subtask(2), beta, huge)])
    )
    assert len(message["content"]) < decompose._RESULT_CHARS * 3
    assert "(zkráceno)" in message["content"]
