"""Act on a broker tapping a button in a renewal WhatsApp.

Today there is one button to act on: "Stop Reminder", which silences the rest of
the ladder for that lead.

THE SHAPE OF A TEMPLATE BUTTON PRESS, from Meta's own reference:

    {
      "context": {"from": "<our number>", "id": "<ORIGINAL MESSAGE ID>"},
      "from": "<broker's whatsapp number>",
      "id": "<inbound message id>",
      "type": "button",
      "button": {"payload": "<BUTTON LABEL>", "text": "<BUTTON LABEL>"}
    }

NOT `interactive.button_reply` — that is the shape for interactive, non-template
messages, and reading for it here would silently match nothing.

IDENTIFIED BY context.id, AND ONLY BY THAT. It is the id of the message the
button belonged to, which is exactly what we stored as
renewal_notification.message_sid. Meta sets it; a sender cannot forge it.

Deliberately NOT by button.payload: Meta returns the button's LABEL TEXT there
for template quick replies, so a value we put in would not survive the round
trip — and even if it did, trusting an id supplied by the sender to decide which
lead to mute is the wrong shape.
"""

import logging
import re

from repositories import lead as lead_repo
from repositories import renewal_notification as repo

logger = logging.getLogger("myday.renewal.inbound")

# The label on the approved template's quick reply. Compared case-insensitively
# and only as a sanity check — the ACTION is decided by which button index Meta
# reports, and today the template has exactly one quick reply, so a press is
# unambiguous. This exists so a future second button cannot be mistaken for
# this one.
_STOP_LABEL = "stop reminder"


def _digits(value: str | None) -> str:
    """Just the digits.

    META'S `from` HAS NO PLUS. It arrives as 14165550142 while broker.phone_e164
    carries +14165550142, so a direct == rejects every legitimate tap. Stripping
    both sides is the whole fix; nothing here tries to parse or validate a
    number, because the database already normalised the one we trust
    (normalize_phone_e164, 0058).
    """
    return re.sub(r"\D", "", value or "")


def handle_button(message: dict) -> str:
    """One inbound button message. Returns what happened, for the log.

    NEVER RAISES. The webhook must answer 200 whatever this finds, or Meta
    retries the same delivery forever.

    IDEMPOTENT. Meta delivers at least once, so the same tap can arrive twice —
    setting a boolean twice is the same as setting it once, and the second pass
    simply finds the flag already true.
    """
    context_id = (message.get("context") or {}).get("id")
    if not context_id:
        # A button press with no context is not one of ours — Meta sends other
        # message types to the same endpoint.
        return "no context id"

    label = ((message.get("button") or {}).get("text") or "").strip().lower()
    if label and label != _STOP_LABEL:
        logger.info("Ignoring an unrecognised button: %r", label)
        return "unrecognised button"

    rows = repo.get_by_message_sid(context_id).data or []
    if not rows:
        # Not a renewal notification. A delivery receipt for the lead-alert
        # queue, or a message we never sent.
        return "no matching notification"

    row = rows[0]
    reminder = row.get("reminder") or {}
    lead = reminder.get("lead") or {}
    broker = reminder.get("broker") or {}
    lead_id = lead.get("id")

    if not lead_id:
        return "notification has no lead"

    # WHO TAPPED IT. The message must come from the broker this reminder
    # belongs to — one broker must never be able to silence another's lead by
    # replying to a message that was not theirs.
    sender = _digits(message.get("from"))
    owner = _digits(broker.get("phone_e164"))
    if not sender or not owner or sender != owner:
        logger.warning(
            "Ignoring a Stop Reminder tap from %s: not the broker for lead %s",
            sender or "an unknown number",
            lead_id,
        )
        return "sender is not the broker"

    try:
        lead_repo.set_renewal_reminders_off(lead_id, True)
        # Anything already queued must go too, or a rung sent minutes from now
        # would contradict the broker who just said stop. LADDER ROWS ONLY —
        # custom reminders live in this table as well, and this button is not
        # about them.
        repo.cancel_pending_ladder_for_lead(
            lead_id, "The broker stopped renewal reminders for this lead"
        )
    except Exception:
        logger.exception("Could not stop renewal reminders for lead %s", lead_id)
        return "failed"

    logger.info(
        "Renewal reminders stopped for lead %s by broker %s",
        lead_id,
        reminder.get("broker_id"),
    )
    return "stopped"
