"""Smyčka, ve které model sahá na počítač.

Vlastnosti, o kterých se z kódu nedá přečíst, jestli platí, protože je nese
souhra `range()`, `continue` a `break`:

1. Když dojdou kola, proběhne ještě jedno **bez nástrojů**. Bez něj skončí
   požadavek, který volal nástroj až do konce, jako „prázdná odpověď" — tedy
   zahozením celé práce a přechodem na další model.
2. Historie nepřeroste okno modelu. Jeden výsledek nástroje má strop 20 000
   znaků a kol je u `max` třicet.
3. Selhání nástroje je v UI vidět jako selhání.
"""

import asyncio
import time

import pytest

from app.providers import client
from app.providers.registry import ProviderModel
from app.routing import effort as effort_tiers
from app.routing import plan as planning
from app.tools import registry as tooling


def _plan() -> planning.Plan:
    return planning.Plan(
        capability="general",
        complexity="simple",
        goal="",
        directives="",
        source="heuristika",
        planner_model=None,
        needs_search=False,
    )


class _Recorder:
    """Falešný provider: dokud smí, volá nástroj; bez nástrojů odpoví."""

    def __init__(self) -> None:
        self.rounds: list[list[dict] | None] = []
        self.histories: list[list[dict]] = []

    async def stream(
        self, provider, model, history, profile, usage, tools=None, calls=None
    ):
        self.rounds.append(tools)
        self.histories.append([dict(m) for m in history])
        if tools is None:
            yield ("text", "hotová odpověď")
            return
        calls[0] = {
            "id": f"call_{len(self.rounds)}",
            "name": "read_file",
            "arguments": '{"path": "x"}',
        }
        yield ("text", "podívám se")


@pytest.fixture()
def loop(monkeypatch):
    """Jeden provider, jeden model, nástroje zapnuté, nic skutečného."""
    recorder = _Recorder()
    provider = ProviderModel(
        id="test",
        display_name="Test",
        models=("test/model",),
        capabilities=("general",),
        signup_url="",
    )

    monkeypatch.setattr(client, "_try_stream", recorder.stream)
    monkeypatch.setattr(client, "_tools_for", lambda model, tools: tools or None)
    monkeypatch.setattr(client.selector, "candidates", lambda *a, **k: [provider])
    monkeypatch.setattr(
        client.model_health, "usable_models", lambda models: list(models)
    )
    monkeypatch.setattr(
        tooling, "specs", lambda *, allow_commands: [{"type": "function"}]
    )
    monkeypatch.setattr(tooling, "system_message", lambda *, allow_commands: None)
    monkeypatch.setattr(tooling, "summarize", lambda name, arguments: name)
    return recorder


def _collect(monkeypatch, profile_id="light", *, tool_text="obsah souboru"):
    async def fake_run(name, arguments, *, allow_commands):
        return tooling.ToolResult(tool_text)

    monkeypatch.setattr(tooling, "run", fake_run)

    async def drain():
        return [
            event
            async for event in client.stream_chat(
                [{"role": "user", "content": "co je v tom souboru?"}],
                _plan(),
                effort_tiers.resolve(profile_id),
                use_tools=True,
            )
        ]

    return asyncio.run(drain())


# ----------------------------------------------------- kolo navíc bez nástrojů


def test_the_last_round_runs_without_tools_and_answers(loop, monkeypatch):
    """Jádro celé smyčky: rozpočet dojde, ale odpověď přesto vznikne."""
    events = _collect(monkeypatch)

    budget = effort_tiers.resolve("light").tool_rounds
    assert loop.rounds[-1] is None
    assert loop.rounds.count(None) == 1
    assert len(loop.rounds) == budget + 2

    text = "".join(e.content for e in events if e.type == "delta")
    assert "hotová odpověď" in text
    assert not any(e.type == "error" for e in events)


def test_the_model_is_told_why_the_tools_disappeared(loop, monkeypatch):
    _collect(monkeypatch)
    last = loop.histories[-1]
    assert any(
        m["role"] == "system" and "limit volání nástrojů" in m["content"] for m in last
    )


def test_rounds_do_not_glue_their_narration_together(loop, monkeypatch):
    """Uložená odpověď byla „Podívám se…Ještě ověřím…" bez jediné mezery."""
    events = _collect(monkeypatch)
    text = "".join(e.content for e in events if e.type == "delta")
    assert "podívám sepodívám se" not in text
    assert "podívám se\n\n" in text


# ------------------------------------------------------------ strop na kontext


def test_the_history_stays_under_the_context_budget(loop, monkeypatch):
    """Bez ořezu naroste historie o 19 kB za kolo, dokud provider nevrátí 4xx.

    Strop není absolutní: poslední dvě kola zůstávají celá, protože výsledek,
    podle kterého model právě odpovídá, se zkrátit nesmí. Zaručuje se tedy
    „rozpočet + dva nejnovější výsledky", ne rozpočet samotný.
    """
    profile = effort_tiers.resolve("light")
    huge = "x" * 19_000
    _collect(monkeypatch, tool_text=huge)

    biggest = max(
        sum(len(str(m.get("content") or "")) for m in history)
        for history in loop.histories
    )
    assert biggest <= profile.tool_context_chars + 2 * len(huge)
    assert any(
        "zkrácen" in str(m.get("content") or "")
        for history in loop.histories
        for m in history
    )


def test_the_two_most_recent_tool_results_survive_trimming():
    history = [
        {"role": "system", "content": "pravidla"},
        {"role": "user", "content": "otázka"},
        {"role": "tool", "content": "a" * 10_000},
        {"role": "tool", "content": "b" * 10_000},
        {"role": "tool", "content": "c" * 10_000},
    ]
    client._trim_tool_history(history, 12_000)
    assert history[2]["content"].startswith("[výsledek nástroje zkrácen")
    assert history[3]["content"] == "b" * 10_000
    assert history[4]["content"] == "c" * 10_000


def test_a_short_history_is_left_alone():
    history = [{"role": "tool", "content": "krátký výsledek"}]
    client._trim_tool_history(history, 12_000)
    assert history[0]["content"] == "krátký výsledek"


# ------------------------------------------------- selhání nástroje je selhání


def test_a_failed_tool_call_is_reported_as_failed(loop, monkeypatch):
    """`tool_ok` se dřív hádalo z prefixu „Odmítnuto:".

    `registry.run` vrací šest různých vět o selhání; pět z nich prošlo jako
    úspěch a v UI svítila zelená fajfka u volání, které neudělalo nic.
    """

    async def fake_run(name, arguments, *, allow_commands):
        return tooling.ToolResult(f"Nástroj `{name}` neexistuje.", ok=False)

    monkeypatch.setattr(tooling, "run", fake_run)

    async def drain():
        return [
            event
            async for event in client.stream_chat(
                [{"role": "user", "content": "zkus to"}],
                _plan(),
                effort_tiers.resolve("light"),
                use_tools=True,
            )
        ]

    results = [e for e in asyncio.run(drain()) if e.type == "tool_result"]
    assert results
    assert all(e.tool_ok is False for e in results)


@pytest.mark.parametrize("name", ["smaz_vsechno", "read_file"])
def test_registry_marks_a_broken_call_as_failed(monkeypatch, name):
    monkeypatch.setattr(tooling, "available", lambda: True)
    result = asyncio.run(tooling.run(name, "{ tohle není JSON", allow_commands=False))
    assert result.ok is False
    assert result.text


def test_a_successful_tool_call_is_ok(monkeypatch, tmp_path):
    from app.tools import sandbox

    monkeypatch.setattr(tooling, "available", lambda: True)
    monkeypatch.setattr(sandbox, "roots", lambda: [tmp_path.resolve()])
    target = tmp_path / "a.txt"
    target.write_text("ahoj\n", encoding="utf-8")

    result = asyncio.run(
        tooling.run("read_file", {"path": str(target)}, allow_commands=False)
    )
    assert result.ok is True
    assert "ahoj" in result.text


def test_a_hanging_tool_is_a_timeout_not_a_hang(monkeypatch):
    """`asyncio.to_thread` nemá strop; zaseknuté čtení drželo celou odpověď."""
    monkeypatch.setattr(tooling, "available", lambda: True)
    monkeypatch.setattr(tooling, "TOOL_TIMEOUT", 0.05)
    monkeypatch.setitem(tooling._HANDLERS, "list_dir", lambda args: time.sleep(5))

    result = asyncio.run(tooling.run("list_dir", {}, allow_commands=False))
    assert result.ok is False
    assert "nestihl" in result.text
