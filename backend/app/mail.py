"""Outgoing email, via Resend.

One recipient, a handful of messages a year: the login code and nothing else.
That is why this is fifty lines of `httpx` rather than a mail library.

**It never raises into a request handler.** A provider outage is not a reason
for a 500 with a stack trace — `send()` returns False and the caller answers
"nepodařilo se odeslat kód", which is both true and actionable.

Without a verified domain Resend's free tier sends from `onboarding@resend.dev`
and **only to the address the Resend account was created with**. For an app
with exactly one user that is not a limitation, it is the entire requirement.
"""

import logging

import httpx

from app.config import settings

log = logging.getLogger("prizma.mail")

API_URL = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 15.0


def is_configured() -> bool:
    return bool(settings.resend_api_key)


async def send(to: str, subject: str, text: str) -> bool:
    """-> whether the provider accepted it. Never raises."""
    if not is_configured():
        return _fallback(to, subject, text)

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                API_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={"from": settings.mail_from, "to": [to], "subject": subject, "text": text},
            )
    except httpx.HTTPError as exc:
        log.warning("mail: transport failed (%s)", exc.__class__.__name__)
        return False

    if response.status_code >= 400:
        # The body can name the actual problem — an unverified domain, a
        # recipient the sandbox will not send to — and none of it is secret.
        log.warning("mail: rejected %s %s", response.status_code, response.text[:200])
        return False
    return True


def _fallback(to: str, subject: str, text: str) -> bool:
    """No mail provider configured.

    Locally this prints the code to the terminal, so developing the login flow
    needs no email account at all.

    On a public server it refuses. Printing a live login code into a log that
    a hosting dashboard renders in a browser is a credential leak, and one that
    would never look like a mistake — the flow would appear to work perfectly.
    """
    if settings.public_mode:
        log.error("mail: RESEND_API_KEY chybí a jsme v public_mode — kód neodeslán")
        return False

    log.warning("mail: RESEND_API_KEY není nastavený, vypisuju do konzole")
    print(f"\n--- e-mail pro {to} ---\n{subject}\n\n{text}\n---\n", flush=True)
    return True
