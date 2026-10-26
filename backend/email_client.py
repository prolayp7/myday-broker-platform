"""Resend's HTTP API: one transactional email.

Sits at the backend root beside whatsapp_client.py — same reason: an
outbound integration client, not a layer of
our own domain.

WHY RESEND AND NOT SMTP. SMTP from a container means credentials, TLS, and a
sending reputation we would have to build and defend ourselves; every large
provider treats an unknown IP as suspect, and the failure is silent — the mail
is accepted and then filed as spam. Resend is one authenticated POST against a
domain whose SPF/DKIM records we publish once, which is the same shape as every
other integration in here.

NO SDK. The send is one HTTP POST with a bearer token, and httpx is already a
dependency. A whole SDK to construct one JSON body would be a dependency to keep
upgraded for no gain — the rule twilio_client.py records for the same reason.

NO RETRY IN HERE. The caller decides. For the WhatsApp fallback there is
deliberately no retry at all: the email is itself the retry for a notification
that already failed, and a fallback that retried a fallback would be two
mechanisms deep with nobody watching either. A failure is recorded on the
notification row (migration 0110) where the tracking screen can show it.

ONE FUNCTION AND NO READ PATH. Nothing in the CRM asks Resend what it has sent;
delivery evidence lives on our own rows.
"""

import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Loaded here rather than relied on from db.py, for the reason whatsapp_client.py
# records: reading credentials from a file only some other module happens to
# have loaded makes this work or fail depending on import order.
load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger("myday.email")

# os.environ.get with a default, never a bare subscript: a missing variable at
# import scope would raise, exit uvicorn and restart-loop the container — the
# whole API down, not just email.
_API_KEY = os.environ.get("RESEND_API_KEY", "")

# The From address. MUST be on a domain verified in Resend — an unverified
# sender is rejected outright, and a lookalike that slips through lands in spam,
# which for this feature is indistinguishable from not sending at all.
_FROM = os.environ.get("RESEND_FROM", "")

_API_URL = "https://api.resend.com/emails"

# Resend is a third party on the far side of the internet. Without a timeout a
# hung connection would hold a drain worker thread forever and notifications
# would stop with the app still looking healthy — the failure mode
# twilio_client.py documents.
_TIMEOUT = httpx.Timeout(15.0)


class EmailError(Exception):
    """A send failed.

    No `permanent` flag, unlike TwilioError. Nothing retries an email here, so
    a caller has no decision to make about whether to try again — the reason is
    recorded and a person reads it. Adding the flag would imply a retry path
    that does not exist.
    """


def is_configured() -> bool:
    """Every environment without Resend credentials pays nothing: the caller
    skips the fallback instead of raising. Same kill-switch shape as
    whatsapp_client.is_configured()."""
    return bool(_API_KEY and _FROM)


def send(*, to: str, subject: str, text: str) -> str:
    """Send one plain-text email. Returns Resend's message id.

    TEXT ONLY, NO HTML. This mail exists to tell a broker a lead is waiting and
    hand them a link; an HTML part would be a second copy of the same words to
    keep in step, and plain text renders identically everywhere including a
    locked phone screen.

    KEYWORD-ONLY ARGUMENTS. A positional (to, subject, text) is three strings
    in a row that a call site can silently transpose — sending the subject line
    as the body to an address that is actually the subject. The names make that
    impossible to get wrong.
    """
    if not is_configured():
        raise EmailError("Resend is not configured")

    if not to:
        raise EmailError("No recipient address")

    payload = {"from": _FROM, "to": [to], "subject": subject, "text": text}

    try:
        response = httpx.post(
            _API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {_API_KEY}"},
            timeout=_TIMEOUT,
        )
    except httpx.RequestError as err:
        # Network-level: DNS, connection, timeout.
        raise EmailError(f"Resend request failed: {err}") from err

    if response.status_code >= 400:
        message = response.text
        try:
            message = response.json().get("message") or message
        except ValueError:
            pass

        # The recipient is deliberately NOT logged: a broker's address is
        # personal data and this line goes to a log a lot of things can read.
        # The reason is enough to act on, and the notification row records
        # which broker it was.
        logger.warning("Resend send failed: HTTP %s", response.status_code)
        raise EmailError(f"Resend {response.status_code}: {message}")

    message_id = response.json().get("id")
    if not message_id:
        # A 2xx with no id should be impossible; treat it as a failure rather
        # than recording a success we cannot trace in Resend's dashboard later.
        raise EmailError("Resend accepted the email but returned no id")

    return message_id
