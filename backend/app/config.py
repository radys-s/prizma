from pathlib import Path
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"


class Settings(BaseSettings):
    """Every field has a local-safe default.

    The whole point of this file is that `uvicorn app.main:app` on Tomáš's
    laptop, with no `.env` and no environment variables at all, behaves exactly
    the way it did before the app learned how to be deployed. Anything that only
    makes sense on a server is off until something turns it on.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # `host` and `port` used to live here and were read by nothing: uvicorn is
    # given them on its own command line, locally and in the Dockerfile alike.
    # Two settings that look authoritative and change nothing are worse than no
    # settings at all.
    db_path: Path = DATA_DIR / "app.db"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ------------------------------------------------------------- deployment

    # The one switch that turns "a local tool" into "a server on the internet".
    #
    # False: the API refuses any peer that isn't loopback, cookies are sent
    # without `Secure` (there is no TLS on localhost), and a missing mail key
    # just logs the login code to the console.
    #
    # True: the peer check is dropped — it would reject every real visitor —
    # and everything that was covering for it has to be strict instead. See
    # `auth/guard.py` for what actually stays: the Origin allowlist, the Host
    # allowlist, the session cookie and the CSRF token. The peer check was
    # never the only layer; it was the outermost one.
    public_mode: bool = False

    # Pootevře lokální režim telefonu ve stejné Wi-Fi — a nic víc.
    #
    # V lokálním režimu pouští `auth/guard.py` jen požadavky z loopbacku, takže
    # telefon dostane 403 dřív, než se stihne zeptat na kód. To je záměr: appka
    # na notebooku nemá důvod poslouchat síť. Jenže než bude deploy, je tohle
    # jediný způsob, jak si mobilní vzhled prohlédnout na skutečném telefonu.
    #
    # Proto výjimka, ale co nejtěsnější: povolí navíc jen peery z privátních
    # rozsahů (RFC 1918 a spol.), je vypnutá, dokud ji někdo výslovně nezapne,
    # a se `public_mode` se nesmí potkat — tam by nedávala smysl a jen by
    # rozšiřovala plochu. Chrání to pořád jen přístupový kód a jeho throttle,
    # takže se to zapíná na dobu koukání, ne natrvalo. Používá to
    # `mobile/lan-preview.ps1`, který běží v popředí a končí Ctrl+C.
    lan_preview: bool = False

    # Empty -> SQLite at `db_path`, exactly as before. Set -> that URL wins.
    # Neon wants `postgresql+psycopg://user:pass@host/db?sslmode=require`.
    database_url: str = ""

    # Master key for provider-key encryption: 32 bytes, base64 or hex.
    # `scripts/gen_secret.py` prints one. Never commit it; on Render it is an
    # environment variable and nowhere else.
    secret_key: str = ""

    # Hostnames this server answers for, on top of the loopback set that is
    # always allowed. On Render: ["prizma.onrender.com"].
    allowed_hosts: list[str] = []

    # The app is single-user and the URL is public, so the first person to sign
    # in would otherwise own the account. When this is set, only that address
    # can create it. Compared case-insensitively.
    owner_email: str = ""

    # The 4-digit access code is a convenience lock, not a credential. Once a
    # real identity is linked it can be switched off without touching code.
    passcode_login_enabled: bool = True

    # Where the browser is sent back after an OAuth round trip, and the base
    # the registered redirect URI is built from. Must match what you register
    # with the provider, character for character.
    public_base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:5173"

    # OIDC sign-in. Empty client id = that button is shown as "not set up"
    # instead of failing after the click. Google and Microsoft need only a
    # client id (PKCE, no secret); Apple's secret is an ES256 JWT you generate
    # from a paid Apple Developer key, which is why Apple stays dark until
    # there is both a paid membership and a verified domain.
    google_client_id: str = ""
    google_client_secret: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    apple_client_id: str = ""
    apple_client_secret: str = ""
    # GitHub is plain OAuth 2.0, not OIDC — it always needs the secret, because
    # its OAuth Apps have no PKCE.
    github_client_id: str = ""
    github_client_secret: str = ""

    # ------------------------------------------------------------------- mail

    # Resend. Without a verified domain the free tier sends from
    # `onboarding@resend.dev` and *only* to the address the account was created
    # with — which is exactly the one recipient this app has.
    resend_api_key: str = ""
    mail_from: str = "Prizma <onboarding@resend.dev>"

    # ---------------------------------------------------------------- contact

    # Contact URI put in the User-Agent of the keyless Wikipedia searcher.
    #
    # Wikimedia's API etiquette *requires* one: measured 2026-08-31, an
    # otherwise perfectly descriptive `Prizma/1.0 (local BYOK chat router;
    # single-user)` gets a flat 403 from every Wikimedia endpoint, and adding a
    # URL to it gets a 200. So this is not a nicety — without it the Wikipedia
    # leg cannot run at all.
    #
    # Empty by default and deliberately not guessed. It is the app owner's
    # public identity being handed to a third party, which is theirs to decide,
    # not something to infer from a git remote. While it is empty the Wikipedia
    # leg is skipped and the keyless floor is arXiv only — which still works,
    # needs no contact, and never 403s. Set it to a repo or project URL, either
    # in the environment or in backend/.env:
    #   CONTACT_URL=https://github.com/<user>/<repo>
    contact_url: str = ""

    # --------------------------------------------------------------- derived

    @property
    def cookies_secure(self) -> bool:
        """`Secure` on a localhost cookie would stop it being set at all."""
        return self.public_mode

    def effective_allowed_hosts(self) -> list[str]:
        """`allowed_hosts`, or the hostname of `public_base_url` if it is empty.

        The deployment hostname otherwise has to be typed correctly in three
        separate variables, and getting it wrong fails in the least helpful way
        available: `HardeningMiddleware` rejects *every* request with 421,
        including Render's health check, so the service never goes live and the
        log says only "Neplatná adresa serveru". Deriving it means one value has
        to be right instead of three.
        """
        explicit = [h.strip() for h in self.allowed_hosts if h.strip()]
        if explicit:
            return explicit
        host = urlsplit(self.public_base_url).hostname
        return [host] if host else []

    def allowed_origins(self) -> set[str]:
        """Origins a browser may drive this API from.

        In public mode the SPA is served by this same process, so its own
        origin is the one that matters; `cors_origins` stays in the set so a
        deployment that still runs Vite separately keeps working.
        """
        origins = set(self.cors_origins)
        if self.public_mode:
            origins.add(self.public_base_url.rstrip("/"))
            origins.add(self.frontend_url.rstrip("/"))
        return {o for o in origins if o}


class ConfigError(RuntimeError):
    """Raised at startup, never mid-request."""


def check_public_mode(current: Settings) -> None:
    """Refuse to boot a public server that is missing what makes it safe.

    Every one of these fails silently and late if it is only checked when it is
    first needed: no `secret_key` means keys cannot be decrypted, so the app
    would look fine until the first chat; no `allowed_hosts` means the Host
    allowlist rejects the real hostname, so every request 421s. Both are much
    cheaper to hit at boot.
    """
    if not current.public_mode:
        return

    problems: list[str] = []
    if not current.secret_key:
        problems.append("SECRET_KEY není nastavený — bez něj nejde dešifrovat žádný API klíč.")
    if not current.effective_allowed_hosts():
        problems.append("ALLOWED_HOSTS je prázdný — server by odmítl vlastní doménu (421).")
    if not current.public_base_url.startswith("https://"):
        problems.append("PUBLIC_BASE_URL musí být https:// adresa nasazené aplikace.")
    if not current.owner_email.strip():
        # Without it `identities.owner_allows()` returns True for everyone, so
        # between the deploy going live and the first sign-in, whoever finds the
        # URL first can claim the account — and with it every stored API key.
        problems.append(
            "OWNER_EMAIL je prázdný — účet by si mohl nárokovat kdokoli, kdo najde adresu."
        )
    if current.lan_preview:
        # Na serveru nemá co dělat: peer check tam stejně nejede, takže by to
        # nic nepovolilo — jen by v konfiguraci stálo napsané, že se něco
        # otevírá. Radši ať to nenaběhne, než aby to někdo četl jako záruku.
        problems.append("LAN_PREVIEW je zapnutý — ten patří jen k lokálnímu náhledu.")
    if problems:
        raise ConfigError("PUBLIC_MODE=1, ale: " + " ".join(problems))


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
