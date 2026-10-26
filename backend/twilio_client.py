"""Twilio's WhatsApp API: one outbound template message to a broker.

Sits at the backend root beside whatsapp_client.py — same
reason: an outbound integration client, not a layer of our own domain.

WHY TWILIO AND NOT META'S CLOUD API. Both go through Meta in the end — same
templates, same approval, same 24-hour-window rule; Twilio is a reseller and
removes none of Meta's requirements. What it buys is readable error codes, a
message log, and a support path when a message silently does not arrive. It
also keeps a WhatsApp misconfiguration away from the Meta credential the lead
pipeline itself depends on, which at this scale is worth more than the
per-message markup.

NO TWILIO SDK. The send is one HTTP POST with basic auth, and httpx is already
a dependency. A whole SDK to construct one form-encoded body would be a
dependency to keep upgraded for no gain.

WHAT A TEMPLATE IS, AND WHY WE CANNOT JUST SEND TEXT. WhatsApp forbids free
text to someone who has not messaged you in the last 24 hours. A broker never
messages us — this channel is one-way — so every message must be a
pre-approved template, referenced by its Content SID with the variables filled
in. The template's SHAPE is fixed at approval time; only the values vary.

NO RETRY IN HERE. The whatsapp_notification queue is the retry: a failed row
stays Pending, attempt_count increments, and the drain tries again on its next
pass. A second retry layer inside this module would multiply attempts and make
attempt_count a lie about how many times Twilio was actually called. Same rule
as whatsapp_client.py.
"""

import json
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Loaded here rather than relied on from db.py, for the reason whatsapp_client.py
# records: reading credentials from a file only some other module happens to
# have loaded makes this work or fail depending on import order.
load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger("myday.twilio")

# os.environ.get with a default, never a bare subscript: a missing variable at
# import scope would raise, exit uvicorn and restart-loop the container — the
# whole API down, not just notifications.
_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")

# The Messaging Service, NOT a bare from-number. Sending via the service means
# swapping the WhatsApp sender later — a demo number for a purchased one, say —
# is a change in Twilio's console and nothing here moves.
_MESSAGING_SERVICE_SID = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "")

# The approved template's Content SID (HX...). Changes only when a NEW template
# is approved, which is what changing the message text requires.
_TEMPLATE_SID = os.environ.get("TWILIO_TEMPLATE_SID", "")

# Absolute public HTTPS URL of our delivery-receipt endpoint. Configured rather
# than derived from the request: it is also what the signature is validated
# against, and behind Railway's proxy a reconstructed URL comes back http://
# while Twilio signed https:// — every check would fail.
_STATUS_CALLBACK_URL = os.environ.get("TWILIO_STATUS_CALLBACK_URL", "")

_API_BASE = "https://api.twilio.com/2010-04-01"

# Twilio is a third party on the far side of the internet. Without a timeout a
# hung connection would hold a drain worker thread forever and notifications
# would stop with the app still looking healthy.
_TIMEOUT = httpx.Timeout(15.0)


class TwilioError(Exception):
    """A send failed. Carries whether it is worth trying again.

    permanent=True means retrying cannot help — the number is not on WhatsApp,
    the template was rejected, the credentials are wrong. The service marks
    those Failed immediately rather than burning four attempts on an outcome
    that is identical every time.
    """

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def is_configured() -> bool:
    """Every environment without Twilio credentials pays nothing: the drain
    loop returns immediately instead of starting. Same kill-switch shape as
    whatsapp_client.is_configured()."""
    return bool(
        _ACCOUNT_SID and _AUTH_TOKEN and _MESSAGING_SERVICE_SID and _TEMPLATE_SID
    )


# Twilio error codes that will fail identically no matter how often we retry.
# Anything not listed is treated as transient and retried — a wrong guess in
# that direction costs three extra attempts, whereas wrongly calling something
# permanent loses a real notification.
#
#   21211  invalid 'To' number
#   21408  not permitted to send to this region
#   21606  the from-number is not a valid WhatsApp sender
#   21610  recipient has blocked/unsubscribed
#   63003  recipient channel not found — the number is not on WhatsApp
#   63007  no WhatsApp sender for this Messaging Service
#   63016  free-form message outside the 24h window (template misconfigured)
#   63024  invalid template parameters
_PERMANENT_CODES = {21211, 21408, 21606, 21610, 63003, 63007, 63016, 63024}


def send_template(to_e164: str, variables: dict[str, str]) -> str:
    """Send the approved template to one broker. Returns Twilio's message SID.

    THE SID MEANS ACCEPTED, NOT DELIVERED. Twilio replies with status "queued";
    the message can still end up failed or undelivered afterwards. Only the
    status callback (routes/whatsapp_webhooks.py) knows which, so the caller
    records 'Submitted' here and waits for that.

    to_e164 is broker.phone_e164 — ALREADY NORMALISED BY THE DATABASE
    (normalize_phone_e164, migration 0058). This function does not normalise:
    a second implementation in Python would be a silent fork of that logic and
    the two would drift.
    """
    if not is_configured():
        raise TwilioError("Twilio is not configured", permanent=True)

    if not to_e164:
        # phone_e164 is null when the broker's number could not be parsed as
        # NANP. Nothing about that changes on a retry.
        raise TwilioError("Broker has no normalised phone number", permanent=True)

    payload = {
        "MessagingServiceSid": _MESSAGING_SERVICE_SID,
        "To": f"whatsapp:{to_e164}",
        "ContentSid": _TEMPLATE_SID,
        # Twilio wants the variables as a JSON object keyed by position:
        # {"1": "Jane Smith", "2": "+14165550134", "3": "2020 or before"}.
        "ContentVariables": json.dumps(variables),
    }

    # Omitted rather than sent empty: Twilio rejects a blank StatusCallback,
    # so an unset env var would break every send instead of just costing us
    # delivery receipts.
    if _STATUS_CALLBACK_URL:
        payload["StatusCallback"] = _STATUS_CALLBACK_URL

    try:
        response = httpx.post(
            f"{_API_BASE}/Accounts/{_ACCOUNT_SID}/Messages.json",
            data=payload,
            auth=(_ACCOUNT_SID, _AUTH_TOKEN),
            timeout=_TIMEOUT,
        )
    except httpx.RequestError as err:
        # Network-level: DNS, connection, timeout. Always worth another go.
        raise TwilioError(f"Twilio request failed: {err}") from err

    if response.status_code >= 400:
        # Twilio puts a numeric code and a human message in the error body.
        # The code is what decides retry-or-not, so it is parsed rather than
        # inferred from the HTTP status — a 400 can be either.
        code = None
        message = response.text
        try:
            body = response.json()
            code = body.get("code")
            message = body.get("message") or message
        except ValueError:
            pass

        permanent = code in _PERMANENT_CODES or response.status_code in (401, 403)

        # The recipient's number is deliberately NOT logged: it identifies a
        # broker, and this line goes to a log a lot of things can read.
        logger.warning(
            "Twilio send failed: HTTP %s code=%s permanent=%s",
            response.status_code, code, permanent,
        )
        raise TwilioError(f"Twilio {response.status_code} ({code}): {message}",
                          permanent=permanent)

    sid = response.json().get("sid")
    if not sid:
        # A 2xx with no SID should be impossible; treat it as transient rather
        # than recording a success we cannot trace in Twilio's logs later.
        raise TwilioError("Twilio accepted the message but returned no SID")

    return sid
