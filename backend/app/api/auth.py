"""Sign-in endpoints.

These are the only routes reachable without a session, so they carry the
Origin check themselves and are rate-limited. Everything else in the API sits
behind `app.auth.guard.require_auth`.

Four ways in, all landing on the same single account (see
`app/auth/identities.py` for the rules that keep it single):

* **Access code** — the four-digit lock. `POST /login`.
* **E-mail** — password, then a six-digit code mailed to the address, or a TOTP
  code if an authenticator app is set up. Two steps, and only the second one
  ever mints a session.
* **OAuth** — Google, Microsoft, Apple (OIDC) and GitHub (OAuth 2.0).
* **Recovery code** — one of eight, single use, for when the phone is gone.

The two-step email flow is the part worth being careful about: `/email/login`
checks the password and returns a challenge id, and *only* `/email/verify`
issues cookies. A password alone is never enough, which is the whole point of
the second factor.
"""

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from app import mail
from app.auth import challenges, core, identities, oauth, throttle, totp
from app.auth.guard import enforce_origin
from app.config import settings
from app.db import get_session
from app.models.auth import AppUser

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 30 days, matching AuthSession.default_expiry.
_COOKIE_MAX_AGE = 30 * 24 * 3600

# The access code may be four characters; an email password may not. They are
# different things protecting the same account from different places — the code
# is typed on a phone by someone who already has the URL, the password is the
# credential an attacker guesses from anywhere.
MIN_EMAIL_PASSWORD_LEN = 8


class PasscodePayload(BaseModel):
    passcode: str = Field(min_length=1, max_length=256)


class EmailPayload(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class VerifyPayload(BaseModel):
    challenge_id: int
    code: str = Field(min_length=1, max_length=32)


class ResendPayload(BaseModel):
    challenge_id: int


class CodePayload(BaseModel):
    code: str = Field(min_length=1, max_length=64)


def _client_ip(request: Request) -> str:
    """The caller's address, as rewritten by uvicorn's proxy-header handling.

    Behind Render's TLS terminator `request.client.host` is the proxy unless
    uvicorn is started with `--proxy-headers --forwarded-allow-ips="*"`, which
    the Dockerfile does. Without that every attacker in the world shares one
    rate-limit bucket with Tomáš — and so the throttle would lock *him* out
    while doing nothing to them.
    """
    return request.client.host if request.client else "unknown"


def _issue_cookies(response: Response, token: str, csrf: str) -> None:
    """Session cookie is HttpOnly so script can't read it; the CSRF cookie is
    readable on purpose — the UI echoes it back in a header, which a
    cross-origin page cannot do.

    `secure` follows `public_mode` rather than being hardcoded either way: on
    the server it must be set, and on localhost setting it would mean the
    browser silently discards both cookies and login appears to do nothing.

    `samesite="lax"` is correct in both. The OAuth provider returns the user by
    a top-level GET navigation, which Lax permits and Strict would drop —
    landing the user back on a login screen after a successful sign-in.
    """
    secure = settings.cookies_secure
    response.set_cookie(
        core.SESSION_COOKIE,
        token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        core.CSRF_COOKIE,
        csrf,
        max_age=_COOKIE_MAX_AGE,
        httponly=False,
        samesite="lax",
        secure=secure,
        path="/",
    )


def _clear_cookies(response: Response) -> None:
    response.delete_cookie(core.SESSION_COOKIE, path="/")
    response.delete_cookie(core.CSRF_COOKIE, path="/")


def _current_user(request: Request, session: Session) -> AppUser | None:
    """The signed-in account, or None. Never raises — callers branch on it."""
    auth = core.resolve_session(session, request.cookies.get(core.SESSION_COOKIE))
    if auth is None:
        return None
    return session.get(AppUser, auth.user_id)


def _sign_in(session: Session, response: Response, user: AppUser, ip: str) -> dict:
    throttle.clear_failures(session, ip)
    token, csrf = core.create_session(session, user.id)
    _issue_cookies(response, token, csrf)
    return {"authenticated": True}


def _guard_throttle(session: Session, ip: str) -> None:
    locked = throttle.lockout_remaining(session, ip)
    if locked:
        raise HTTPException(
            status_code=429, detail=f"Příliš mnoho pokusů. Zkus to za {locked} s."
        )


def _fail(session: Session, ip: str, detail: str, status: int = 401) -> HTTPException:
    throttle.record_failure(session, ip)
    return HTTPException(status_code=status, detail=detail)


# --------------------------------------------------------------------- state


@router.get("/state")
def auth_state(request: Request, session: Session = Depends(get_session)):
    """What the login screen needs to render itself."""
    enforce_origin(request)
    user = core.get_user(session)
    signed_in = _current_user(request, session)
    linked = identities.identities_for(session, signed_in.id) if signed_in else []

    return {
        "has_account": user is not None,
        "authenticated": signed_in is not None,
        "user": (
            {
                "display_name": signed_in.display_name,
                "email": signed_in.email,
                "provider": signed_in.provider,
                "email_verified": bool(signed_in.email_verified),
                "totp_confirmed": bool(signed_in.totp_confirmed),
                "recovery_codes_left": challenges.recovery_remaining(session, signed_in.id),
            }
            if signed_in is not None
            else None
        ),
        "identities": [
            {
                "id": i.id,
                "provider": i.provider,
                "email": i.email,
                "display_name": i.display_name,
                "created_at": i.created_at,
            }
            for i in linked
        ],
        "oauth": oauth.available(),
        "passcode_enabled": settings.passcode_login_enabled,
        # An unset mail key locally means codes print to the terminal, which is
        # a working development setup; on a server it means email sign-in
        # genuinely cannot work, so the UI must not offer it.
        "email_enabled": mail.is_configured() or not settings.public_mode,
        "min_passcode_length": core.MIN_PASSCODE_LEN,
        "min_password_length": MIN_EMAIL_PASSWORD_LEN,
        "locked_for_seconds": throttle.lockout_remaining(session, _client_ip(request)),
    }


# --------------------------------------------------------------- access code


@router.post("/setup")
def setup(
    payload: PasscodePayload,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    """First run only — creates the account with an access code.

    Refused when `OWNER_EMAIL` is set, which is the configuration a public
    deployment uses: there, the account has to be claimed by an identity that
    proves who it is, and the access code is added afterwards from inside
    (`POST /passcode`). Otherwise the first stranger to open the URL could
    claim the app with four digits of their choosing.
    """
    enforce_origin(request)
    if core.has_account(session):
        raise HTTPException(status_code=409, detail="Účet už existuje.")
    if not settings.passcode_login_enabled:
        raise HTTPException(status_code=403, detail="Přihlášení kódem je vypnuté.")
    if settings.owner_email:
        raise HTTPException(
            status_code=403,
            detail="Účet musí nejdřív převzít vlastník — přihlas se Googlem, GitHubem nebo e-mailem.",
        )

    passcode = payload.passcode.strip()
    if len(passcode) < core.MIN_PASSCODE_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Heslo musí mít aspoň {core.MIN_PASSCODE_LEN} znaků.",
        )

    user = identities.resolve(
        session,
        provider=identities.LOCAL_PROVIDER,
        subject=identities.LOCAL_PROVIDER,
        display_name="Já",
        secret_hash=core.hash_passcode(passcode),
    )
    return _sign_in(session, response, user, _client_ip(request))


@router.post("/login")
def login(
    payload: PasscodePayload,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    enforce_origin(request)
    ip = _client_ip(request)
    if not settings.passcode_login_enabled:
        raise HTTPException(status_code=403, detail="Přihlášení kódem je vypnuté.")
    _guard_throttle(session, ip)

    user = core.get_user(session)
    # Hash even when there's no account, so a missing account and a wrong
    # passcode take the same time and can't be told apart.
    stored = identities.local_secret(session)
    if not core.verify_passcode(payload.passcode, stored) or user is None:
        raise _fail(session, ip, "Nesprávné heslo.")

    return _sign_in(session, response, user, ip)


@router.post("/passcode")
def set_passcode(
    payload: PasscodePayload,
    request: Request,
    session: Session = Depends(get_session),
):
    """Set or change the access code. Requires being signed in already.

    This is the other half of the `/setup` refusal above: on a server the
    account is claimed with a real identity, and the four-digit code is added
    here, from inside, by someone who is already in.
    """
    enforce_origin(request)
    user = _current_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="Nepřihlášeno.")

    passcode = payload.passcode.strip()
    if len(passcode) < core.MIN_PASSCODE_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Kód musí mít aspoň {core.MIN_PASSCODE_LEN} znaků.",
        )
    identities.set_secret(
        session,
        user.id,
        identities.LOCAL_PROVIDER,
        identities.LOCAL_PROVIDER,
        core.hash_passcode(passcode),
    )
    return {"ok": True}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    enforce_origin(request)
    core.revoke_session(session, request.cookies.get(core.SESSION_COOKIE))
    _clear_cookies(response)
    return {"authenticated": False}


# --------------------------------------------------------------------- email


def _mail_body(code: str, purpose: str) -> tuple[str, str]:
    what = "Potvrzení e-mailu" if purpose == challenges.PURPOSE_VERIFY else "Přihlášení"
    minutes = int(challenges.TTL.total_seconds() // 60)
    return (
        f"Prizma — {what.lower()}: {code}",
        f"{what} do Prizmy.\n\nTvůj kód: {code}\n\n"
        f"Platí {minutes} minut a jde použít jednou. "
        "Pokud ses nepřihlašoval ty, nic nedělej — bez kódu se nikdo nedostane dovnitř.",
    )


async def _send_challenge(session: Session, user: AppUser, email: str, purpose: str) -> dict:
    challenge, code = challenges.create(session, user.id, email, purpose)
    subject, body = _mail_body(code, purpose)
    if not await mail.send(email, subject, body):
        # The challenge exists but its code went nowhere, so it is unusable.
        # Leaving it would mean the next "poslat znovu" rotates a row the user
        # never received a code for, which reads as the resend not working.
        session.delete(challenge)
        session.commit()
        raise HTTPException(status_code=503, detail="Nepodařilo se odeslat kód na e-mail.")
    return {
        "stage": "2fa",
        "challenge_id": challenge.id,
        "method": "email",
        "email": _mask(email),
    }


def _mask(email: str) -> str:
    """`tomas.radek111@gmail.com` -> `to…11@gmail.com`.

    The screen says which address the code went to without printing the whole
    thing — the login screen is shown to whoever has the URL, and a full
    address there is a free gift to anyone phishing it later.
    """
    name, _, domain = email.partition("@")
    if len(name) <= 4:
        return f"{name[:1]}…@{domain}"
    return f"{name[:2]}…{name[-2:]}@{domain}"


@router.post("/email/register")
async def email_register(
    payload: EmailPayload,
    request: Request,
    session: Session = Depends(get_session),
):
    """Create the account with an email address, or link one to it."""
    enforce_origin(request)
    ip = _client_ip(request)
    _guard_throttle(session, ip)

    if not identities.valid_email(payload.email):
        raise HTTPException(status_code=422, detail="Tohle nevypadá jako e-mailová adresa.")
    email = identities.normalize_email(payload.email)
    if len(payload.password) < MIN_EMAIL_PASSWORD_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Heslo musí mít aspoň {MIN_EMAIL_PASSWORD_LEN} znaků.",
        )

    try:
        user = identities.resolve(
            session,
            provider=identities.EMAIL_PROVIDER,
            subject=email,
            email=email,
            display_name=email,
            current_user=_current_user(request, session),
            secret_hash=core.hash_passcode(payload.password),
        )
    except identities.NotTheOwner as exc:
        raise _fail(session, ip, str(exc), status=403) from exc
    except identities.DifferentAccount as exc:
        raise _fail(session, ip, "jiny_ucet", status=403) from exc

    return await _send_challenge(session, user, email, challenges.PURPOSE_VERIFY)


@router.post("/email/login")
async def email_login(
    payload: EmailPayload,
    request: Request,
    session: Session = Depends(get_session),
):
    """Password check only. A session is issued by `/email/verify`, never here."""
    enforce_origin(request)
    ip = _client_ip(request)
    _guard_throttle(session, ip)

    email = identities.normalize_email(payload.email)
    identity = identities.find_identity(session, identities.EMAIL_PROVIDER, email or "")
    stored = identity.secret_hash if identity else None
    # Hashed even when the identity does not exist, so "no such address" and
    # "wrong password" cost the same and cannot be told apart by timing.
    if not core.verify_passcode(payload.password, stored) or identity is None:
        raise _fail(session, ip, "Nesprávný e-mail nebo heslo.")

    user = session.get(AppUser, identity.user_id)
    if user is None:
        raise _fail(session, ip, "Nesprávný e-mail nebo heslo.")

    # An authenticator app, once confirmed, replaces the emailed code: it is
    # faster, works with no inbox and no mail provider, and does not spend the
    # 100-messages-a-day free quota on routine sign-ins.
    #
    # A challenge row is still created, with nothing mailed. It is the token
    # that says "the password was checked" — without it `/totp/verify` would be
    # a password-free way in, guarded by six digits alone.
    if user.totp_confirmed and user.totp_secret:
        challenge, _unused = challenges.create(
            session, user.id, email or "", challenges.PURPOSE_TOTP
        )
        return {
            "stage": "2fa",
            "challenge_id": challenge.id,
            "method": "totp",
            "email": _mask(email or ""),
        }

    return await _send_challenge(session, user, email or "", challenges.PURPOSE_LOGIN)


@router.post("/email/verify")
def email_verify(
    payload: VerifyPayload,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    """The second factor. The only email route that issues cookies."""
    enforce_origin(request)
    ip = _client_ip(request)
    _guard_throttle(session, ip)

    try:
        pending = challenges.load(session, payload.challenge_id)
        if pending.purpose not in challenges.MAILED_PURPOSES:
            raise challenges.ChallengeError("Tenhle krok se ověřuje aplikací, ne e-mailem.")
        challenge = challenges.verify(session, payload.challenge_id, payload.code)
    except challenges.ChallengeError as exc:
        raise _fail(session, ip, str(exc)) from exc

    user = session.get(AppUser, challenge.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Účet už neexistuje.")

    if challenge.purpose == challenges.PURPOSE_VERIFY:
        user.email_verified = True
        if not user.email:
            user.email = challenge.email
        session.add(user)
        session.commit()

    return _sign_in(session, response, user, ip)


@router.post("/totp/verify")
def totp_verify(
    payload: VerifyPayload,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    """Finish an email login whose second factor is an authenticator app.

    `challenge_id` is the row `/email/login` created after checking the
    password, which is what keeps this from being a way in that needs no
    password. Also accepts a recovery code, because "my phone is gone" is
    exactly when a user reaches this screen and cannot produce a TOTP code.
    """
    enforce_origin(request)
    ip = _client_ip(request)
    _guard_throttle(session, ip)

    try:
        challenge = challenges.load(session, payload.challenge_id)
        if challenge.purpose != challenges.PURPOSE_TOTP:
            raise challenges.ChallengeError("Tenhle krok se ověřuje e-mailem, ne aplikací.")
        challenges.check_usable(session, challenge)
    except challenges.ChallengeError as exc:
        raise _fail(session, ip, str(exc)) from exc

    user = session.get(AppUser, challenge.user_id)
    if user is None or not user.totp_confirmed or not user.totp_secret:
        raise _fail(session, ip, "Dvoufázové ověření není nastavené.")

    code = payload.code.strip()
    if not totp.verify(user.totp_secret, code) and not challenges.redeem_recovery(
        session, user.id, code
    ):
        # Counted on the challenge as well as the IP, so five wrong codes burn
        # this login attempt and force the password step to be repeated.
        challenge.attempts += 1
        if challenge.attempts >= challenges.MAX_ATTEMPTS:
            session.delete(challenge)
        else:
            session.add(challenge)
        session.commit()
        raise _fail(session, ip, "Nesprávný kód.")

    challenges.consume(session, challenge)
    return _sign_in(session, response, user, ip)


@router.post("/email/resend")
async def email_resend(
    payload: ResendPayload,
    request: Request,
    session: Session = Depends(get_session),
):
    enforce_origin(request)
    try:
        challenge = challenges.load(session, payload.challenge_id)
    except challenges.ChallengeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if challenge.purpose not in challenges.MAILED_PURPOSES:
        raise HTTPException(status_code=409, detail="Tenhle krok se e-mailem neověřuje.")
    if challenge.consumed_at is not None:
        raise HTTPException(status_code=409, detail="Tenhle kód už byl použitý.")

    wait = challenges.may_resend(challenge)
    if wait:
        raise HTTPException(status_code=429, detail=f"Počkej ještě {wait} s.")

    code = challenges.rotate_code(session, challenge)
    subject, body = _mail_body(code, challenge.purpose)
    if not await mail.send(challenge.email, subject, body):
        raise HTTPException(status_code=503, detail="Nepodařilo se odeslat kód na e-mail.")
    return {"sent": True, "email": _mask(challenge.email)}


# ---------------------------------------------------------------------- TOTP


@router.post("/totp/setup")
def totp_setup(request: Request, session: Session = Depends(get_session)):
    """Mint a secret. Not active until `/totp/confirm` proves the app agrees."""
    enforce_origin(request)
    user = _current_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="Nepřihlášeno.")

    secret = totp.secret()
    user.totp_secret = secret
    user.totp_confirmed = False
    session.add(user)
    session.commit()
    account = user.email or "Prizma"
    return {"secret": secret, "uri": totp.uri(secret, account), "digits": totp.DIGITS}


@router.post("/totp/confirm")
def totp_confirm(
    payload: CodePayload,
    request: Request,
    session: Session = Depends(get_session),
):
    enforce_origin(request)
    user = _current_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="Nepřihlášeno.")
    if not user.totp_secret:
        raise HTTPException(status_code=409, detail="Nejdřív si nech vygenerovat klíč.")
    if not totp.verify(user.totp_secret, payload.code):
        raise HTTPException(status_code=401, detail="Kód nesedí. Zkontroluj čas na telefonu.")

    user.totp_confirmed = True
    session.add(user)
    session.commit()
    # Issued here and shown exactly once: they are the only way back in if the
    # phone is lost, and storing them anywhere retrievable would defeat them.
    return {"confirmed": True, "recovery_codes": challenges.issue_recovery(session, user.id)}


@router.post("/totp/disable")
def totp_disable(request: Request, session: Session = Depends(get_session)):
    enforce_origin(request)
    user = _current_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="Nepřihlášeno.")
    user.totp_secret = None
    user.totp_confirmed = False
    session.add(user)
    session.commit()
    return {"confirmed": False}


# ---------------------------------------------------------------- identities


@router.delete("/identities/{identity_id}")
def unlink_identity(
    identity_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    enforce_origin(request)
    user = _current_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="Nepřihlášeno.")
    try:
        identities.unlink(session, user.id, identity_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


# --------------------------------------------------------------------- OAuth


@router.get("/oauth/{provider_id}/start")
def oauth_start(provider_id: str, request: Request):
    enforce_origin(request)
    try:
        url = oauth.begin(provider_id)
    except oauth.OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url, status_code=307)


def _finish_oauth(
    session: Session, provider_id: str, claims: dict, current_user: AppUser | None
) -> RedirectResponse:
    """Sign in, create or link — `identities.resolve` decides which."""
    try:
        user = identities.resolve(
            session,
            provider=provider_id,
            subject=claims["sub"],
            email=claims.get("email"),
            display_name=claims.get("name"),
            current_user=current_user,
        )
    except identities.DifferentAccount:
        return RedirectResponse(
            f"{settings.frontend_url}/?auth_error=jiny_ucet", status_code=303
        )
    except identities.NotTheOwner:
        return RedirectResponse(
            f"{settings.frontend_url}/?auth_error=neni_vlastnik", status_code=303
        )

    token, csrf = core.create_session(session, user.id)
    response = RedirectResponse(settings.frontend_url, status_code=303)
    _issue_cookies(response, token, csrf)
    return response


@router.get("/oauth/{provider_id}/callback")
async def oauth_callback(
    provider_id: str,
    request: Request,
    state: str = "",
    code: str = "",
    session: Session = Depends(get_session),
):
    enforce_origin(request)
    current_user = _current_user(request, session)
    try:
        expected_provider, verifier = oauth.take_pending(state)
        if expected_provider != provider_id:
            raise oauth.OAuthError("Provider neodpovídá zahájenému přihlášení.")
        claims = await oauth.exchange(provider_id, code, verifier)
    except oauth.OAuthError as exc:
        return RedirectResponse(
            f"{settings.frontend_url}/?auth_error={exc}", status_code=303
        )
    return _finish_oauth(session, provider_id, claims, current_user)


@router.post("/oauth/{provider_id}/callback")
async def oauth_callback_post(
    provider_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    """Apple replies with `response_mode=form_post`, so it lands here.

    The body is parsed by hand rather than with `Form(...)`: FastAPI's form
    support pulls in python-multipart, and Apple posts plain
    `application/x-www-form-urlencoded` — one stdlib call covers it.
    """
    raw = (await request.body()).decode("utf-8", errors="replace")
    fields = parse_qs(raw, keep_blank_values=True)
    state = fields.get("state", [""])[0]
    code = fields.get("code", [""])[0]
    return await oauth_callback(provider_id, request, state, code, session)
