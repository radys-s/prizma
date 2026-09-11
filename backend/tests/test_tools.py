"""Co agent na tomhle počítači nesmí.

Testy jsou psané na hranice, ne na šťastnou cestu. U nástroje, který čte disk
a spouští programy, je „funguje to“ ta snadnější půlka — cena chyby je celá
v tom druhém sloupci, a navíc ho řídí cizí model, kterému nikdo nediktuje, co
zkusí. Každý test níž odpovídá jednomu způsobu, jak se dá ven z písku dostat.

Sada běží v public mode (viz `conftest.py`), takže poslední test tady zároveň
ověřuje to nejdůležitější: na veřejné adrese nejsou nástroje k dispozici vůbec.
"""

import asyncio

import pytest

from app.tools import registry, sandbox


@pytest.fixture()
def root(tmp_path, monkeypatch):
    """Písek jen pro tenhle test, jako jediný povolený kořen."""
    monkeypatch.setattr(sandbox, "roots", lambda: [tmp_path.resolve()])
    return tmp_path.resolve()


def test_path_inside_root_resolves(root):
    target = root / "poznamky.txt"
    target.write_text("ahoj", encoding="utf-8")
    assert sandbox.resolve(str(target)) == target


def test_path_outside_root_is_denied(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("jinde") / "tajne.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(sandbox.Denied):
        sandbox.resolve(str(outside))


def test_dotdot_cannot_climb_out(root):
    """Řetězec začíná kořenem, cesta ne — proto se `resolve()` volá první."""
    escape = str(root / ".." / ".." / "windows" / "system32")
    with pytest.raises(sandbox.Denied):
        sandbox.resolve(escape)


@pytest.mark.parametrize("name", [".env", "app.db", "server.key", "id_rsa"])
def test_secrets_are_denied_even_inside_the_root(root, name):
    secret = root / name
    secret.write_text("KEY=abc", encoding="utf-8")
    with pytest.raises(sandbox.Denied):
        sandbox.resolve(str(secret))


@pytest.mark.parametrize("folder", [".git", "node_modules", ".venv"])
def test_closed_folders_are_denied(root, folder):
    inside = root / folder / "config"
    inside.parent.mkdir(parents=True)
    inside.write_text("x", encoding="utf-8")
    with pytest.raises(sandbox.Denied):
        sandbox.resolve(str(inside))


def test_missing_path_is_denied(root):
    with pytest.raises(sandbox.Denied):
        sandbox.resolve(str(root / "neexistuje.txt"))


def test_allowed_command_parses_into_argv():
    assert sandbox.check_command("git status --short") == ["git", "status", "--short"]


@pytest.mark.parametrize("command", ["rm -rf /", "shutdown /s", "reg delete HKLM", "cmd"])
def test_commands_outside_the_allowlist_are_denied(command):
    with pytest.raises(sandbox.Denied):
        sandbox.check_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "git status && curl http://zlo.example",
        "python -c 1 | more",
        "git log > out.txt",
        "echo `whoami`",
    ],
)
def test_shell_operators_are_denied(command):
    """Bez shellu by stejně nefungovaly — a s allowlistem na prvním slově by
    prošly. Odmítnout je je jediná odpověď, která znamená to, co říká."""
    with pytest.raises(sandbox.Denied):
        sandbox.check_command(command)


def test_output_is_clipped():
    clipped = sandbox.clip("x" * (sandbox.MAX_OUTPUT_CHARS + 500))
    assert len(clipped) < sandbox.MAX_OUTPUT_CHARS + 100
    assert "zkráceno" in clipped


def test_public_mode_turns_the_whole_layer_off():
    """Nejdůležitější test v souboru: sada běží v public mode.

    Nástroj, který čte disk a spouští programy, je na veřejné adrese vzdálený
    shell — a to platí bez ohledu na to, co je uložené v nastavení. `available()`
    je jediné místo, kde se to rozhoduje, takže tenhle test hlídá i `specs()`
    a `run()` naráz.
    """
    assert registry.available() is False
    assert registry.specs(allow_commands=True) == []
    answer = asyncio.run(
        registry.run("read_file", {"path": "C:/Windows/win.ini"}, allow_commands=True)
    )
    assert "vypnutá" in answer


def test_unknown_tool_is_an_answer_not_a_crash(monkeypatch):
    monkeypatch.setattr(registry, "available", lambda: True)
    answer = asyncio.run(registry.run("smaz_vsechno", {}, allow_commands=False))
    assert "neexistuje" in answer


def test_commands_need_permission_even_when_the_model_asks(monkeypatch):
    """Model si jméno funkce může vymyslet a po pár kolech ho vymyslí — vidí
    `run_command` v historii konverzace, i když v aktuálním požadavku nabídnutý
    není. Oprávnění proto nesmí být jen v `specs()`."""
    monkeypatch.setattr(registry, "available", lambda: True)
    answer = asyncio.run(
        registry.run("run_command", {"command": "git status"}, allow_commands=False)
    )
    assert "není u téhle zprávy povolené" in answer


# --- search_text: maska od modelu není cesta -------------------------------
#
# `root.rglob(pattern)` bral masku tak, jak přišla, a nalezené soubory už
# nekontroloval proti kořenům — `glob="../../**/*.txt"` tím četl cokoli na
# disku a posílal to cizímu providerovi. Denylisty, které zbyly, o soukromých
# složkách mimo projekt nic nevědí.


@pytest.mark.parametrize("glob", ["../**/*.txt", "../../secrets/*", "/etc/*", "C:/Windows/*"])
def test_search_glob_that_is_a_path_is_refused(root, glob):
    with pytest.raises(sandbox.Denied):
        registry._search_text({"path": str(root), "pattern": "heslo", "glob": glob})


def test_search_never_leaves_the_root(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("jinde")
    (outside / "tajne.txt").write_text("heslo: 1234", encoding="utf-8")
    (root / "vlastni.txt").write_text("heslo: vlastni", encoding="utf-8")

    found = registry._search_text(
        {"path": str(root), "pattern": "heslo", "glob": "**/*.txt"}
    )
    assert "vlastni.txt" in found
    assert "tajne.txt" not in found


def test_search_prunes_closed_folders(root):
    (root / "node_modules").mkdir()
    (root / "node_modules" / "modul.js").write_text("heslo", encoding="utf-8")
    (root / "vlastni.js").write_text("heslo", encoding="utf-8")

    found = registry._search_text(
        {"path": str(root), "pattern": "heslo", "glob": "*.js"}
    )
    assert "vlastni.js" in found
    assert "node_modules" not in found


def test_search_finds_matches_in_subfolders(root):
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("def hledej():\n    pass\n", encoding="utf-8")
    found = registry._search_text(
        {"path": str(root), "pattern": "hledej", "glob": "*.py"}
    )
    assert "main.py" in found


# --- write_file: konce řádků se nesmí měnit --------------------------------


@pytest.fixture()
def writable(monkeypatch):
    monkeypatch.setattr(registry, "available", lambda: True)
    monkeypatch.setattr(registry, "can_write", lambda: True)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (b"stary\r\nobsah\r\n", b"novy\r\nobsah\r\n"),
        (b"stary\nobsah\n", b"novy\nobsah\n"),
    ],
)
def test_write_file_keeps_the_files_line_endings(root, writable, before, after):
    """`write_text` bez `newline=""` překládá `\n` na `os.linesep`.

    Na Windows z CRLF souboru udělá `\r\r\n` na každém řádku — obsah, který
    nikdo nepřečte a ve kterém `edit_file` už nic nenajde.
    """
    target = root / "soubor.txt"
    target.write_bytes(before)
    asyncio.run(
        registry.run(
            "write_file",
            {"path": str(target), "content": "novy\nobsah\n"},
            allow_commands=False,
        )
    )
    assert target.read_bytes() == after


def test_write_file_creates_new_files_with_lf(root, writable):
    target = root / "novy.txt"
    asyncio.run(
        registry.run(
            "write_file",
            {"path": str(target), "content": "a\nb\n"},
            allow_commands=False,
        )
    )
    assert target.read_bytes() == b"a\nb\n"
