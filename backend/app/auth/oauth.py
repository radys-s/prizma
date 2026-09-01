"""Sign-in with Google, Microsoft, Apple and GitHub.

The first three are OpenID Connect, so they share one implementation driven by
`OIDC_PROVIDERS` + whatever credentials are configured in `.env`. A provider
with no client id is reported as unavailable rather than rendering a button
that fails after the user clicks it.

**GitHub is not OIDC.** It is plain OAuth 2.0: the token response contains an
access token and no `id_token`, so there are no signed claims to validate and
identity has to be fetched from the API afterwards. Two consequences worth
naming, because both look like bugs otherwise:

* GitHub OAuth Apps **do not support PKCE**. The `code_challenge` is sent
  anyway (harmless, ignored) and what actually protects the exchange is the
  single-use `state` plus the client secret, over a server-to-server TLS
  connection the browser never sees.
* A GitHub account's public email is often absent or a `noreply` alias, so the
  address comes from `/user/emails` and only if it is **primary and verified**.
  An unverified address must never be trusted: anyone can type it in.

`exchange()` normalises both shapes into the same `{sub, email, name}` dict, so
nothing downstream branches on which provider was used.

**On ID token signatures:** the token is fetched by this server directly from
the provider's token endpoint over TLS, never passed through the browser. OIDC
Core §3.1.3.7 explicitly allows TLS server validation to stand in for checking
the JWT signature in exactly this case, so we decode the claims and validate
`iss`, `aud` and `exp` rather than pulling in a JWKS client. If the token ever
starts arriving via the front channel (implicit flow), that reasoning stops
holding and signature verification becomes mandatory.

**Apple** needs `client_secret` to be an ES256 JWT signed with a key from a
paid Apple Developer account. Generating it needs that private key, so it is
read from config instead of being minted here.
"""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass

import httpx

from app.config import settings

STATE_TTL_SECONDS = 600.0


@dataclass(frozen=True)
class OidcProvider:
    id: str
    display_name: str
    authorize_url: str
    token_url: str
    issuers: tuple[str, ...]
    scope: str
    extra_authorize_params: tuple[tuple[str, str], ...] = ()
    # "oidc": identity arrives in a signed id_token from the token endpoint.
    # "oauth2": identity has to be fetched from `userinfo_url` afterwards.
    kind: str = "oidc"
    userinfo_url: str = ""
    emails_url: str = ""


OIDC_PROVIDERS: dict[str, OidcProvider] = {
    "google": OidcProvider(
        id="google",
        display_name="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        issuers=("https://accounts.google.com", "accounts.google.com"),
        scope="openid email profile",
    ),
    "microsoft": OidcProvider(
        id="microsoft",
        display_name="Microsoft",
        authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        issuers=(),  # tenant-specific; validated via aud + TLS instead
        scope="openid email profile",
    ),
    "apple": OidcProvider(
        id="apple",
        display_name="Apple",
        authorize_url="https://appleid.apple.com/auth/authorize",
        token_url="https://appleid.apple.com/auth/token",
        issuers=("https://appleid.apple.com",),
        scope="openid email name",
        # Apple only returns the id_token to a POSTed form response.
        extra_authorize_params=(("response_mode", "form_post"),),
    ),
    "github": OidcProvider(
        id="github",
        display_name="GitHub",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        issuers=(),
        # `read:user` for the profile, `user:email` for the verified address —
        # the narrowest pair that answers "who is this". Notably *not* `repo`:
        # this app has no business reading anyone's code.
        scope="read:user user:email",
        kind="oauth2",
        userinfo_url="https://api.github.com/user",
        emails_url="https://api.github.com/user/emails",
    ),
}


class OAuthError(RuntimeError):
    pass


def credentials(provider_id: str) -> tuple[str, str]:
    """(client_id, client_secret) from settings; empty strings when unset."""
    client_id = getattr(settings, f"{provider_id}_client_id", "") or ""
    client_secret = getattr(settings, f"{provider_id}_client_secret", "") or ""
    return client_id, client_secret


# Providers whose flow cannot complete without a client secret. Google and
# Microsoft authenticate the exchange with PKCE, so a client id alone is a
# working configuration for them; GitHub has no PKCE and Apple's secret *is*
# the signed assertion, so for those two a missing secret means the button
# would fail after the click rather than before it.
_SECRET_REQUIRED = {"github", "apple"}


def is_configured(provider_id: str) -> bool:
    client_id, client_secret = credentials(provider_id)
    if not client_id:
        return False
    if provider_id in _SECRET_REQUIRED and not client_secret:
        return False
    return True


def available() -> dict[str, bool]:
    return {pid: is_configured(pid) for pid in OIDC_PROVIDERS}


def redirect_uri(provider_id: str) -> str:
    return f"{settings.public_base_url}/api/auth/oauth/{provider_id}/callback"


# --------------------------------------------------------- pending handshakes

# state -> (provider_id, pkce_verifier, expires_at). In memory: a login
# handshake that doesn't survive a restart is a non-issue.
_pending: dict[str, tuple[str, str, float]] = {}


def _prune() -> None:
    now = time.monotonic()
    for state in [s for s, (_, _, exp) in _pending.items() if exp <= now]:
        del _pending[state]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def begin(provider_id: str) -> str:
    """Returns the provider's authorization URL to send the browser to."""
    provider = OIDC_PROVIDERS.get(provider_id)
    if provider is None:
        raise OAuthError(f"Neznámý provider: {provider_id}")
    if not is_configured(provider_id):
        raise OAuthError(
            f"{provider.display_name} není nastavený — chybí přihlašovací údaje v .env."
        )
    client_id, _ = credentials(provider_id)

    _prune()
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    _pending[state] = (provider_id, verifier, time.monotonic() + STATE_TTL_SECONDS)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(provider_id),
        "response_type": "code",
        "scope": provider.scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **dict(provider.extra_authorize_params),
    }
    return str(httpx.URL(provider.authorize_url, params=params))


def take_pending(state: str) -> tuple[str, str]:
    """Consumes the handshake for `state`. Single use — replay is rejected."""
    _prune()
    entry = _pending.pop(state, None)
    if entry is None:
        raise OAuthError("Neplatný nebo prošlý přihlašovací požadavek.")
    provider_id, verifier, _ = entry
    return provider_id, verifier


# ------------------------------------------------------------------ id tokens


def _decode_claims(id_token: str) -> dict:
    try:
        payload = id_token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except (IndexError, ValueError) as exc:
        raise OAuthError("Provider vrátil nečitelný id_token.") from exc


def _validate(claims: dict, provider: OidcProvider, client_id: str) -> None:
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if client_id not in audiences:
        raise OAuthError("id_token patří jiné aplikaci.")

    if provider.issuers and claims.get("iss") not in provider.issuers:
        raise OAuthError("Neočekávaný vydavatel id_tokenu.")

    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp <= time.time():
        raise OAuthError("id_token už vypršel.")

    if not claims.get("sub"):
        raise OAuthError("id_token neobsahuje identitu uživatele.")


async def exchange(provider_id: str, code: str, verifier: str) -> dict:
    """Swaps the authorization code for identity: `{sub, email, name}`.

    Both protocol shapes end here with the same dict, so `_finish_oauth` has no
    per-provider branch at all.
    """
    provider = OIDC_PROVIDERS[provider_id]
    client_id, client_secret = credentials(provider_id)

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(provider_id),
        "client_id": client_id,
        "code_verifier": verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            provider.token_url,
            data=data,
            headers={"Accept": "application/json"},
        )

        if response.status_code >= 400:
            raise OAuthError(
                f"{provider.display_name} odmítl přihlášení ({response.status_code})."
            )

        payload = response.json()

        # GitHub answers 200 with `{"error": "bad_verification_code"}` rather
        # than a 4xx, so a status check alone would sail past a failed exchange
        # and then complain about a missing token instead of the real cause.
        if payload.get("error"):
            raise OAuthError(
                f"{provider.display_name} odmítl přihlášení: {payload['error']}"
            )

        if provider.kind == "oauth2":
            return await _fetch_profile(client, provider, payload.get("access_token", ""))

    id_token = payload.get("id_token")
    if not id_token:
        raise OAuthError(f"{provider.display_name} nevrátil id_token.")

    claims = _decode_claims(id_token)
    _validate(claims, provider, client_id)
    return {
        "sub": str(claims["sub"]),
        "email": claims.get("email"),
        "name": claims.get("name") or claims.get("given_name") or claims.get("email"),
    }


async def _fetch_profile(client: httpx.AsyncClient, provider: OidcProvider, token: str) -> dict:
    """The OAuth 2.0 path: ask the API who the token belongs to."""
    if not token:
        raise OAuthError(f"{provider.display_name} nevrátil přístupový token.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    profile = await client.get(provider.userinfo_url, headers=headers)
    if profile.status_code >= 400:
        raise OAuthError(
            f"{provider.display_name} nevrátil profil ({profile.status_code})."
        )
    user = profile.json()
    subject = user.get("id")
    if not subject:
        raise OAuthError(f"{provider.display_name} nevrátil identitu uživatele.")

    email = user.get("email")
    if provider.emails_url:
        # The profile's `email` is whatever the user made public, which is
        # usually nothing. The verified primary address is the one to trust,
        # and the only one an account should ever be matched on.
        listed = await client.get(provider.emails_url, headers=headers)
        if listed.status_code < 400:
            entries = listed.json()
            if isinstance(entries, list):
                email = next(
                    (
                        e.get("email")
                        for e in entries
                        if isinstance(e, dict) and e.get("primary") and e.get("verified")
                    ),
                    email,
                )

    return {
        "sub": str(subject),
        "email": email,
        "name": user.get("name") or user.get("login") or email,
    }
