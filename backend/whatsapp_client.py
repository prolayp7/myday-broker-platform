"""Meta's WhatsApp Cloud API: one outbound template message to a broker.

Sits at the backend root beside twilio_client.py — same reason: an outbound
integration client, not a layer of
our own domain.

REPLACES twilio_client.py, WHICH IS STILL PRESENT. Twilio was the original
transport and was disconnected on Meta's side; every send after that returned
`Twilio 404 (20404)` — "that account resource no longer exists" — and 77
consecutive notifications failed over eleven days without anyone noticing,
because this feature's failures are invisible (the lead is still in the CRM, so
no broker complains). The old client is kept until this one is proven in
production, then deleted in its own commit.

WHY META DIRECTLY AND NOT TWILIO. Both go through Meta in the end — same
templates, same approval, same 24-hour-window rule; Twilio was a reseller and
removed none of Meta's requirements. Going direct removes a party, a bill and a
second account that can be disconnected underneath us. What it costs is
Twilio's readable error codes and message log, which is why the error mapping
below is written out explicitly rather than inferred.

NO SDK. The send is one HTTP POST with a bearer token, and httpx is already a
dependency. Same reasoning twilio_client.py records.

WHAT A TEMPLATE IS, AND WHY WE CANNOT JUST SEND TEXT. WhatsApp forbids free
text to someone who has not messaged you in the last 24 hours. A broker never
messages us — this channel is one-way — so every message must be a pre-approved
template, referenced by NAME and LANGUAGE (Meta's own addressing; Twilio used
an opaque Content SID for the same thing). The template's SHAPE is fixed at
approval; only the values vary.

NO RETRY IN HERE. The whatsapp_notification queue is the retry: a failed row
stays Pending, attempt_count increments, and the drain tries again on its next
pass. A second retry layer inside this module would multiply attempts and make
attempt_count a lie about how many times Meta was actually called. Same rule as
twilio_client.py.
"""

import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from meta_graph import GRAPH_API_BASE

# Loaded here rather than relied on from db.py, for the reason twilio_client.py
# records: reading credentials from a file only some other module happens to
# have loaded makes this work or fail depending on import order.
load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger("myday.whatsapp")

# os.environ.get with a default, never a bare subscript: a missing variable at
# import scope would raise, exit uvicorn and restart-loop the container — the
# whole API down, not just notifications.
_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")

# The SENDER, as Meta's own numeric id rather than the phone number. This is
# the `/{phone_number_id}/messages` path segment; the human-readable number
# (+1 437-347-9922) appears nowhere in a request.
_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")

# A SECOND SENDER, for reminders only — so a broker can mute the renewal and
# callback thread without losing new-lead alerts, which was the whole point of
# splitting them. Both numbers live in the same WhatsApp Business Account, so
# all three templates are approved for either: templates belong to the WABA,
# not to a number.
#
# UNSET MEANS "USE THE MAIN SENDER", and that is the safe default in both
# directions. Deploying this before the second number exists changes nothing,
# and clearing the variable later is a one-step rollback to a single thread
# without a code change. Note this is NOT the 132001 hazard the template names
# carry below — a wrong sender id fails on transport, whereas an unapproved
# template name fails every queued row permanently on its first attempt.
_REMINDER_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_REMINDER_PHONE_NUMBER_ID", "")

# The approved template, addressed by name and language.
#
# TWO VARIABLES, NOT ONE. Meta treats a template as (name, language) — the same
# name can exist in several languages and they are different templates with
# different approval states. Defaulting the language to 'en' would silently
# send the wrong one the day a second locale is added.
_TEMPLATE_NAME = os.environ.get("WHATSAPP_TEMPLATE_NAME", "")
_TEMPLATE_LANGUAGE = os.environ.get("WHATSAPP_TEMPLATE_LANGUAGE", "en")

# The renewal-ladder template (0118). A SECOND approved template, with its own
# name, its own four variables and its own buttons — nothing about it can be
# derived from the lead-alert one above.
#
# LEFT UNSET UNTIL META APPROVES IT. 132001 ("template does not exist") is in
# _PERMANENT_CODES, so pointing at an unapproved name fails every queued row on
# the FIRST attempt and emails the broker about a WhatsApp that never worked.
# Unset is the safe state: is_renewal_configured() is false and the drain does
# nothing at all.
_RENEWAL_TEMPLATE_NAME = os.environ.get("WHATSAPP_RENEWAL_TEMPLATE_NAME", "")
_RENEWAL_TEMPLATE_LANGUAGE = os.environ.get("WHATSAPP_RENEWAL_TEMPLATE_LANGUAGE", "en")

# The callback template (0122) — a THIRD approved template, for a broker's own
# "remind me to call them back" reminders. Four variables, no buttons.
#
# LEFT UNSET UNTIL META APPROVES IT, for exactly the reason above: 132001 is
# permanent, so pointing at an unapproved name fails every queued callback on
# its FIRST attempt and emails the broker about a WhatsApp that never worked.
# Unset is the safe state — is_callback_configured() is false, the drain skips
# callbacks, and their rows wait as Pending while renewals keep flowing.
_CALLBACK_TEMPLATE_NAME = os.environ.get("WHATSAPP_CALLBACK_TEMPLATE_NAME", "")
_CALLBACK_TEMPLATE_LANGUAGE = os.environ.get("WHATSAPP_CALLBACK_TEMPLATE_LANGUAGE", "en")

# THE BASE URL COMES FROM meta_graph, NOT A SECOND COPY HERE. That module
# already resolves META_GRAPH_API_BASE -> GRAPH_API_BASE_OVERRIDE -> a
# version-built default, and validates the version's shape at import so a typo
# stops the app starting rather than 404ing hours later in someone else's error
# message. Its own header records that a hand-written duplicate existed once
# and was removed, because "keep the two in step" is the arrangement that
# drifts. Importing it also means the WhatsApp and lead-pipeline calls can
# never disagree about which API version they are on.

# Meta is a third party on the far side of the internet. Without a timeout a
# hung connection would hold a drain worker thread forever and notifications
# would stop with the app still looking healthy.
_TIMEOUT = httpx.Timeout(15.0)


class WhatsAppError(Exception):
    """A send failed. Carries whether it is worth trying again.

    permanent=True means retrying cannot help — the number is not on WhatsApp,
    the template does not exist, the token is invalid. The service marks those
    Failed immediately rather than burning three more attempts on an outcome
    that is identical every time.

    Same shape as twilio_client.TwilioError, deliberately: services/whatsapp.py
    decides `exhausted = err.permanent or attempt >= MAX_ATTEMPTS` and that
    logic must not have to care which transport raised.
    """

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def _transport_configured() -> bool:
    """The credentials every send needs, whatever template it carries.

    SPLIT OUT so one template's absence cannot disable another's drain. The
    lead-alert and renewal queues are separate features that happen to share a
    phone number and a token.
    """
    return bool(_ACCESS_TOKEN and _PHONE_NUMBER_ID)


def is_configured() -> bool:
    """Every environment without Meta credentials pays nothing: the drain loop
    returns immediately instead of starting. Same kill-switch shape as
    twilio_client.is_configured().

    THE TEMPLATE NAME COUNTS AS CONFIGURATION. Without it every send would be
    rejected by Meta one row at a time, burning attempts and filling the queue
    with Failed rows — which looks like a broken integration rather than an
    unconfigured one. Better to not start at all and say so once.

    Still means the LEAD-ALERT template specifically — every existing caller
    reads it that way, and widening it would change their behaviour silently.
    """
    return bool(_transport_configured() and _TEMPLATE_NAME)


def is_renewal_configured() -> bool:
    """The same question for the renewal ladder's template.

    Its own function rather than a parameter on is_configured() because the two
    drains ask independently: an unconfigured renewal template must stop the
    renewal drain and leave lead alerts running, and the reverse.
    """
    return bool(_transport_configured() and _RENEWAL_TEMPLATE_NAME)


def renewal_template() -> tuple[str, str]:
    """The renewal template's (name, language), for send_template's kwargs.

    An accessor rather than letting callers read the module globals: a test
    that monkeypatches the name has one place to patch, and a caller cannot
    accidentally read the name while defaulting the language.
    """
    return _RENEWAL_TEMPLATE_NAME, _RENEWAL_TEMPLATE_LANGUAGE


def reminder_phone_number_id() -> str:
    """The sender reminders go out from — the second number when one is set,
    otherwise the main sender.

    AN ACCESSOR, NOT A GLOBAL READ AT THE CALL SITE, for the reason
    renewal_template() gives: one place for a test to patch, and a caller
    cannot read the second sender while forgetting the fallback.

    Falling back to _PHONE_NUMBER_ID rather than returning "" keeps the
    unconfigured case sending from the number that has always worked, instead
    of failing a send that has nothing wrong with it.
    """
    return _REMINDER_PHONE_NUMBER_ID or _PHONE_NUMBER_ID


def is_callback_configured() -> bool:
    """The same question for a broker's own callback reminders (0122).

    Independent of is_renewal_configured() on purpose: the two features are
    configured, approved and turned on separately, and an unapproved callback
    template must not silence the renewal ladder.
    """
    return bool(_transport_configured() and _CALLBACK_TEMPLATE_NAME)


def callback_template() -> tuple[str, str]:
    """The callback template's (name, language), for send_template's kwargs."""
    return _CALLBACK_TEMPLATE_NAME, _CALLBACK_TEMPLATE_LANGUAGE


# Meta error codes that will fail identically no matter how often we retry.
# Anything not listed is treated as transient and retried — a wrong guess in
# that direction costs three extra attempts, whereas wrongly calling something
# permanent loses a real notification. Same bias twilio_client.py chose.
#
#   131026  message undeliverable — recipient is not on WhatsApp, or cannot
#           receive it (the single most common real failure here)
#   131047  re-engagement required — outside the 24h window without a template
#   131051  unsupported message type
#   132000  template param count does not match the approved template
#   132001  template does not exist in this WABA / language
#   132005  template text was rejected
#   132007  template format mismatch
#   132012  template parameter format mismatch
#   132015  template is paused by Meta for quality
#   132016  template is disabled
#   133010  the sender phone number is not registered
#   190     access token expired or invalid
#   200     permission denied — token lacks whatsapp_business_messaging
#   10      application does not have permission for this action
_PERMANENT_CODES = {
    131026, 131047, 131051,
    132000, 132001, 132005, 132007, 132012, 132015, 132016,
    133010,
    190, 200, 10,
}


def _variables_to_parameters(variables: dict[str, str]) -> list[dict]:
    """Our positional {"1": ..., "2": ...} map into Meta's body parameters.

    THE ORDER IS THE CONTRACT. Meta's body parameters are a LIST — position in
    that list is what binds a value to {{1}}, {{2}}, {{3}} — whereas Twilio took
    a JSON object keyed by position. So the keys are sorted NUMERICALLY, not as
    strings: a template that ever reaches ten variables would otherwise order
    them 1, 10, 2 and put the wrong value in every placeholder from {{2}} on.

    services/whatsapp.py keeps producing the same map it always has; this is
    the only place that knows Meta wants a list.
    """
    return [
        {"type": "text", "text": variables[key]}
        for key in sorted(variables, key=lambda k: int(k))
    ]


def send_template(
    to_e164: str,
    variables: dict[str, str],
    *,
    template: str | None = None,
    language: str | None = None,
    buttons: list[dict] | None = None,
    phone_number_id: str | None = None,
) -> str:
    """Send an approved template to one broker. Returns Meta's message id.

    THE ID MEANS ACCEPTED, NOT DELIVERED. Meta replies with a message id and a
    status of "accepted"; the message can still fail afterwards. Only the
    webhook (routes/whatsapp_webhooks.py) knows which, so the caller records
    'Submitted' here and waits for that. This is the same distinction Twilio
    forced and it is worth keeping — recording 'Sent' on the API response would
    claim success for messages that never arrived.

    to_e164 is broker.phone_e164 — ALREADY NORMALISED BY THE DATABASE
    (normalize_phone_e164, migration 0058). This function does not normalise: a
    second implementation in Python would be a silent fork of that logic and
    the two would drift.

    `template` and `language` DEFAULT TO THE LEAD-ALERT ONES, so every existing
    call site is untouched and keeps sending exactly what it always sent.

    `buttons` EXISTS BECAUSE COMPONENTS MUST MATCH THE APPROVED TEMPLATE. A
    template with buttons rejects a send that omits their components, and the
    lead alert has none while the renewal nudge has two. Passing None leaves the
    payload byte-identical to what it was before this parameter existed — which
    is what tests/test_whatsapp.py asserts.

    `phone_number_id` DEFAULTS TO THE MAIN SENDER, so lead alerts are untouched
    and only the reminder drain sends from the second number. It changes the URL
    alone — the payload does not name the sender at all.
    """
    name = template or _TEMPLATE_NAME
    lang = language or _TEMPLATE_LANGUAGE
    # DEFAULTS TO THE MAIN SENDER, so every existing call site sends from the
    # number it always has. Only the reminder drain passes this.
    sender = phone_number_id or _PHONE_NUMBER_ID

    # THE TEMPLATE WE WERE ACTUALLY GIVEN, not the default. Calling
    # is_configured() here would make a renewal send depend on the lead-alert
    # template being set, which is exactly the coupling _transport_configured
    # was split out to remove.
    if not _transport_configured() or not name:
        raise WhatsAppError("WhatsApp is not configured", permanent=True)

    if not to_e164:
        # phone_e164 is null when the broker's number could not be parsed as
        # NANP. Nothing about that changes on a retry.
        raise WhatsAppError("Broker has no normalised phone number", permanent=True)

    payload = {
        "messaging_product": "whatsapp",
        # NO "whatsapp:" PREFIX, unlike Twilio. Meta's `to` is the bare E.164
        # number; the prefix was Twilio's own channel selector.
        "to": to_e164,
        "type": "template",
        "template": {
            "name": name,
            "language": {"code": lang},
            "components": [
                {"type": "body", "parameters": _variables_to_parameters(variables)},
                *(buttons or []),
            ],
        },
    }

    try:
        response = httpx.post(
            f"{GRAPH_API_BASE}/{sender}/messages",
            json=payload,
            headers={"Authorization": f"Bearer {_ACCESS_TOKEN}"},
            timeout=_TIMEOUT,
        )
    except httpx.RequestError as err:
        # Network-level: DNS, connection, timeout. Always worth another go.
        raise WhatsAppError(f"WhatsApp request failed: {err}") from err

    if response.status_code >= 400:
        # Meta nests the useful part: {"error": {"code": 131026, "message": ...,
        # "error_data": {"details": "..."}}}. The CODE is what decides
        # retry-or-not, so it is parsed rather than inferred from the HTTP
        # status — a 400 can be either.
        code = None
        message = response.text
        try:
            error = (response.json() or {}).get("error") or {}
            code = error.get("code")
            # error_data.details is the human sentence Meta actually explains
            # the failure in; `message` alone is often just the category.
            details = (error.get("error_data") or {}).get("details")
            message = details or error.get("message") or message
        except ValueError:
            pass

        permanent = code in _PERMANENT_CODES or response.status_code in (401, 403)

        # The recipient's number is deliberately NOT logged: it identifies a
        # broker, and this line goes to a log a lot of things can read.
        logger.warning(
            "WhatsApp send failed: HTTP %s code=%s permanent=%s",
            response.status_code, code, permanent,
        )
        raise WhatsAppError(f"WhatsApp {response.status_code} ({code}): {message}",
                            permanent=permanent)

    # {"messages": [{"id": "wamid.HBg..."}]} — the id is the join key the
    # delivery webhook comes back on, and what you paste into Meta's own logs
    # to see one message's fate. It goes in whatsapp_notification.message_sid,
    # which is transport-agnostic despite its Twilio-era name.
    try:
        message_id = (response.json()["messages"][0]["id"])
    except (ValueError, KeyError, IndexError):
        # A 2xx with no id should be impossible; treat it as transient rather
        # than recording a success we cannot trace in Meta's logs later.
        raise WhatsAppError("WhatsApp accepted the message but returned no id")

    return message_id
