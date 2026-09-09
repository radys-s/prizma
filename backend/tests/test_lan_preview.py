"""Náhled na telefonu — jediné místo, kde lokální režim pouští cizí zařízení.

Je to výjimka v bezpečnostní vrstvě, takže se testuje z obou stran: že bez
zapnutí nepustí nic a že po zapnutí pustí jen to, co má. Test na veřejný rozsah
je ten důležitý — kdyby výjimka jednou začala brát cokoli mimo loopback,
pořád by procházela, dokud se ho někdo nezeptá.
"""

import pytest

from app.auth.guard import _is_lan_preview_peer
from app.config import ConfigError, Settings, check_public_mode, settings


@pytest.fixture()
def preview_on():
    """Zapne náhled jen na dobu testu — `settings` je sdílený objekt."""
    was = settings.lan_preview
    settings.lan_preview = True
    yield
    settings.lan_preview = was


def test_vypnuty_nahled_nepusti_ani_sousedni_telefon():
    # Výchozí stav. Bez tohohle by celá výjimka mohla být omylem vždy zapnutá.
    assert settings.lan_preview is False
    assert _is_lan_preview_peer("192.168.1.50") is False


@pytest.mark.parametrize(
    "peer",
    ["192.168.1.50", "10.0.0.8", "172.16.4.2", "fe80::1"],
)
def test_zapnuty_nahled_pusti_zarizeni_ze_site(preview_on, peer):
    assert _is_lan_preview_peer(peer) is True


@pytest.mark.parametrize(
    "peer",
    ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2001:4860:4860::8888"],
)
def test_ani_zapnuty_nahled_nepusti_verejnou_adresu(preview_on, peer):
    # Schválně jen adresy, které jsou opravdu na internetu. `is_private` bere
    # jako privátní i dokumentační rozsahy typu 203.0.113.0/24 (TEST-NET) —
    # což je v pořádku, ty se stejně neroutují, ale jako důkaz by neobstály.
    assert _is_lan_preview_peer(peer) is False


def test_loopback_si_nahled_nenarokuje(preview_on):
    # Loopback prochází o řádek dřív, v `LOOPBACK_HOSTS`. Kdyby ho brala i
    # tahle větev, přestalo by být z kódu poznat, která vrstva ho pouští.
    assert _is_lan_preview_peer("127.0.0.1") is False
    assert _is_lan_preview_peer("::1") is False


def test_nesmyslny_peer_neprojde(preview_on):
    assert _is_lan_preview_peer("telefon.local") is False
    assert _is_lan_preview_peer("") is False
    assert _is_lan_preview_peer(None) is False


def test_na_verejnem_serveru_nahled_shodi_start():
    # Peer check tam stejně nejede, takže by to nic nepovolilo — ale nechat to
    # v konfiguraci stát by vypadalo jako záruka, kterou to nedává.
    public = Settings(
        public_mode=True,
        secret_key="0" * 64,
        allowed_hosts=["prizma.example"],
        public_base_url="https://prizma.example",
        owner_email="owner@example.com",
        lan_preview=True,
    )
    with pytest.raises(ConfigError, match="LAN_PREVIEW"):
        check_public_mode(public)
