"""Oddělení uvažování modelu od odpovědi.

Celý smysl téhle vrstvy je jedna věta: do bubliny odpovědi nesmí spadnout ani
znak z toho, co si model přemýšlel nahlas. Testy jsou proto psané na místa, kde
se to nejsnáz stane — na hranice chunků, na neuzavřený blok a na `<think>`,
který není uvažování, ale kus textu v odpovědi.
"""

from app.providers.client import _tail_prefix, _ThinkSplitter


def split(chunks: list[str]) -> tuple[str, str]:
    """Projede kousky splitterem a vrátí (odpověď, uvažování)."""
    splitter = _ThinkSplitter()
    text = reason = ""
    for chunk in chunks:
        for kind, piece in splitter.feed(chunk):
            if kind == "text":
                text += piece
            else:
                reason += piece
    for kind, piece in splitter.flush():
        if kind == "text":
            text += piece
        else:
            reason += piece
    return text, reason


def test_blok_na_zacatku_neodtece_do_odpovedi():
    text, reason = split(["<think>rozmýšlím</think>Ahoj."])
    assert text == "Ahoj."
    assert reason == "rozmýšlím"


def test_znacka_rozseknuta_mezi_chunky():
    # Nejčastější reálný případ: chunk skončí uprostřed `</think>`.
    text, reason = split(["<thi", "nk>a", "b</thi", "nk>Odpověď"])
    assert text == "Odpověď"
    assert reason == "ab"


def test_znak_po_znaku():
    text, reason = split(list("<think>xy</think>Ok"))
    assert text == "Ok"
    assert reason == "xy"


def test_neuzavreny_blok_je_cely_uvazovani():
    # Model nedomyslel a stream skončil. Prázdná odpověď je správně —
    # poznámky vydávat za odpověď by bylo horší.
    text, reason = split(["<think>půlka myšlenky"])
    assert text == ""
    assert reason == "půlka myšlenky"


def test_think_uprostred_odpovedi_je_text():
    # Odpověď o tom, jak značka vypadá. Splitter se po prvním textu odzbrojí,
    # takže ji nechá být.
    text, reason = split(["Značka ", "<think>", " se používá takhle."])
    assert text == "Značka <think> se používá takhle."
    assert reason == ""


def test_bez_znacek_projde_beze_zmeny():
    text, reason = split(["Prostá ", "odpověď."])
    assert text == "Prostá odpověď."
    assert reason == ""


def test_uvodni_bily_znak_neodzbroji():
    # Poskytovatelé rádi pošlou první chunk jako "\n". Kdyby to splitter bral
    # jako začátek odpovědi, blok za ním by prošel rovnou do bubliny.
    text, reason = split(["\n", "<think>hm</think>Ano"])
    assert text == "\nAno"
    assert reason == "hm"


def test_tail_prefix_najde_nejdelsi_zacatek():
    assert _tail_prefix("abc</thi", "</think>") == 5
    assert _tail_prefix("abc<", "<think>") == 1
    assert _tail_prefix("abc", "<think>") == 0
    # Celá značka není „možný začátek" — tu už našel `find`.
    assert _tail_prefix("<think>", "<think>") == 0
