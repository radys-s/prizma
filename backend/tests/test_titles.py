"""Co se smí objevit v seznamu konverzací.

Název je jediné, co uživatel z vlákna vidí, dokud ho neotevře. Modul kolem něj
slibuje dvě věci — nikdy nevyhodí výjimku a nikdy nezablokuje stream — a k tomu
tichou třetí: co projde `_clean`, se opravdu zobrazí. Testy níž jsou hlavně
o tom, co jí projít *nesmí*.
"""

from app.routing import titles

# ------------------------------------------------ běžné odpovědi modelů


def test_a_plain_answer_is_the_title():
    assert titles._clean("Modrá obloha a rozptyl světla") == "Modrá obloha a rozptyl světla"


def test_quotes_bold_and_a_label_are_stripped():
    assert titles._clean('**"Název: Rozptyl světla"**') == "Rozptyl světla"


# ------------------------------------------------ co do panelu nepatří

# Reasoning model nahlas přemýšlí, než odpoví. Dřív se bralo natvrdo první
# řádka a ořezala se na 48 znaků, takže v panelu skončilo vlákno pojmenované
# „We need to respond with a short title, m…“ — useknutá věta o tom, že se má
# vymyslet název. Ořez text neudělal názvem, jen ho schoval.


def test_reasoning_before_the_answer_is_skipped():
    raw = (
        "We need to respond with a short title, maybe six words at most, "
        "in Czech, describing the topic of the exchange.\n"
        "Rozptyl světla v atmosféře"
    )
    assert titles._clean(raw) == "Rozptyl světla v atmosféře"


def test_a_think_block_is_removed_even_when_it_is_not_closed():
    # `_MAX_TOKENS` je 40 — když dojdou, uzavírací značka nedorazí a zbytek
    # odpovědi je jen přemýšlení. Není z čeho vzít název.
    raw = "<think>Okay, the user asks about the sky, so the title should"
    assert titles._clean(raw) == ""


def test_a_closed_think_block_leaves_the_answer_behind():
    raw = "<think>Hmm, něco o obloze.</think>\nProč je obloha modrá"
    assert titles._clean(raw) == "Proč je obloha modrá"


def test_prose_alone_yields_nothing_so_the_next_provider_is_tried():
    raw = (
        "Omlouvám se, ale bez dalšího kontextu nedokážu vymyslet název, "
        "který by konverzaci vystihoval dostatečně přesně."
    )
    assert titles._clean(raw) == ""


def test_an_empty_answer_yields_nothing():
    assert titles._clean("") == ""
    assert titles._clean("   \n\n  ") == ""


# ------------------------------------------------ ořez

# Krátký název se nesmí useknout uprostřed slova — přesně to je rozdíl mezi
# „Rozptyl světla v zemské…“ a „Rozptyl světla v zemské atmosf“.


def test_a_long_title_is_cut_on_a_word_boundary():
    title = titles._clean("Rozptyl slunečního světla v zemské atmosféře a barva oblohy")
    assert len(title) <= titles.MAX_CHARS
    assert not title.endswith("-")
    assert title.split()[-1] in "Rozptyl slunečního světla v zemské atmosféře a barva oblohy".split()
