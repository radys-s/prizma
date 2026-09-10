"""Co se model o nástrojích dozví — a co mu do toho nesmí kecat plánovač.

Nástroje samotné testuje `test_tools.py`. Tady jde o vrstvu nad nimi: model
může mít `list_dir` připojený a stejně odpovědět „na tvoji složku nevidím“,
protože mu nikdo neřekl jedinou cestu, kterou smí použít, nebo protože mu
plánovač napsal do zadání pravý opak. Obojí se stalo v provozu (10. 9. 2026,
Groq GPT-OSS 120B, režim Pro) a obojí vypadá zvenku jako vypnutá funkce.
"""

import pytest

from app.api import chat as chat_api
from app.routing import plan as planning
from app.tools import registry, sandbox


@pytest.fixture()
def tools_on(monkeypatch, tmp_path):
    """Zapnuté nástroje s jedním známým kořenem."""
    monkeypatch.setattr(registry, "available", lambda: True)
    monkeypatch.setattr(registry, "can_write", lambda: False)
    monkeypatch.setattr(sandbox, "roots", lambda: [tmp_path.resolve()])
    return tmp_path.resolve()


def test_model_dostane_seznam_povolenych_slozek(tools_on):
    """Jádro té chyby: bez tohohle nemá `list_dir` co dosadit za cestu."""
    message = registry.system_message(allow_commands=False)
    assert message is not None
    assert str(tools_on) in message["content"]


def test_modelu_se_zakazuje_tvrdit_ze_na_soubory_nevidi(tools_on):
    assert "Nikdy netvrď" in registry.system_message(allow_commands=False)["content"]


def test_bez_zapisu_se_to_rekne_nahlas(tools_on):
    content = registry.system_message(allow_commands=False)["content"]
    assert "Zapisovat do souborů" in content
    # Zákaz zápisu nesmí model svést k tomu, že si zakáže i čtení. Stalo se to
    # u příkazů a chová se to stejně: každé omezení musí hned dodat, co funguje.
    assert "čtení funguje" in content


def test_se_zapisem_uz_se_to_neopakuje(tools_on, monkeypatch):
    monkeypatch.setattr(registry, "can_write", lambda: True)
    content = registry.system_message(allow_commands=False)["content"]
    assert "Zapisovat do souborů" not in content


def test_bez_prikazu_model_vi_co_ma_rict_uzivateli(tools_on):
    """Odmítnutí uprostřed odpovědi je horší než věta „zapni si to“."""
    content = registry.system_message(allow_commands=False)["content"]
    assert "Počítač + příkazy" in content


def test_s_prikazy_uz_se_o_ne_nezada(tools_on):
    assert "Počítač + příkazy" not in registry.system_message(allow_commands=True)["content"]


def test_vypnute_nastroje_nemaji_co_rict(monkeypatch):
    monkeypatch.setattr(registry, "available", lambda: False)
    assert registry.system_message(allow_commands=False) is None


def test_planovac_se_o_nastrojich_dozvi():
    """Plánovač vidí jen text zprávy.

    Bez téhle poznámky psal do `directives` doslova „jasně uveď, že jako model
    nemáš přístup k souborům v uživatelově složce“ — a protože jeho rozbor jde
    do promptu jako systémová zpráva, odpovídající model ho poslechl.
    """
    assert "has working tools" in planning._TOOLS_NOTE
    assert "Never write a" in planning._TOOLS_NOTE


class _Req:
    def __init__(self, use_tools: bool) -> None:
        self.use_tools = use_tools


def test_prepinac_v_composeru_umi_nastroje_vypnout(monkeypatch):
    monkeypatch.setattr(chat_api.tooling, "available", lambda: True)
    assert chat_api._tools_requested(_Req(use_tools=False)) is False
    assert chat_api._tools_requested(_Req(use_tools=True)) is True


def test_prepinac_v_composeru_je_nesmi_zapnout(monkeypatch):
    """Composer si smí přát, nastavení rozhoduje. Kdyby to bylo obráceně,
    stačilo by k obejití nastavení jedno pole v požadavku."""
    monkeypatch.setattr(chat_api.tooling, "available", lambda: False)
    assert chat_api._tools_requested(_Req(use_tools=True)) is False
