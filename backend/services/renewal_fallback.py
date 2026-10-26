"""Email a broker when a renewal WhatsApp could not be delivered.

ITS OWN MODULE, NOT whatsapp_fallback WITH A FLAG. That one imports
`repositories.whatsapp_notification` at module scope and composes "New lead
assigned: …" — pointing it at a renewal row would claim the wrong table's
fallback lock and send a broker an email about a lead assignment that never
happened. The two share `email_client` and nothing else worth sharing.

Only fires on TERMINAL failure — a permanent Meta error, or the last attempt.
A renewal is worth a year of premium; losing the nudge in silence is the one
outcome this whole feature exists to prevent.
"""

import logging
import os
from urllib.parse import quote

import email_client
from repositories import renewal_notification as repo

logger = logging.getLogger("myday.renewal.fallback")

# Same configured base as the WhatsApp fallback uses. Not derived: the backend
# has no reliable idea what public URL the frontend is served from.
_CRM_URL = os.environ.get("CRM_PUBLIC_URL", "").rstrip("/")

_NOT_PROVIDED = "Not provided"


def _lead_link(lead: dict) -> str:
    """A link that lands the broker on this lead in the CRM.

    BY ID, unlike whatsapp_fallback's phone-number search. That module predates
    the `?lead=<uuid>` handling the renewal nudge's own button needs, so it
    falls back to searching for the customer's number; this one can be exact.

    Returns "" when the id or the base URL is missing, and the caller omits the
    line rather than printing a broken link.
    """
    lead_id = lead.get("id") or ""
    if not _CRM_URL or not lead_id:
        return ""
    return f"{_CRM_URL}/leads?lead={quote(str(lead_id))}"


def _compose(
    lead: dict, broker: dict, days_away: int | None, reminder: dict | None = None
) -> tuple[str, str]:
    """The subject and body. Returns (subject, text).

    SAYS WHY IT ARRIVED, in the first line — a broker who may already have seen
    the WhatsApp should not have to work out whether this is a duplicate.

    THE FAILURE REASON IS NOT IN THE BODY. A broker cannot act on "131026
    undeliverable", and it would only undermine confidence in the tool. It is on
    the notification row for whoever reads the tracking screen.
    """
    name = lead.get("name") or _NOT_PROVIDED
    phone = lead.get("phone_e164") or lead.get("phone") or _NOT_PROVIDED

    # A CALLBACK IS NOT A RENEWAL (0122). Every line below this point is about
    # a policy expiring; sending it for "remind me to call them back" would
    # tell the broker something untrue about their own lead — and the reason
    # this fallback exists at all is that a reminder which silently fails is
    # worse than no reminder.
    reminder = reminder or {}
    if reminder.get("auto_rule") is None and reminder.get("kind") == "Callback":
        answered = lead.get("answered_count") or 0
        no_answer = lead.get("no_answer_count") or 0

        subject = f"Call reminder: {name} (WhatsApp alert failed)"
        lines = [
            "Your WhatsApp call reminder could not be delivered, so here it is by email.",
            "",
            f"Customer: {name}",
            f"Tel: {phone}",
            f"Picked up {answered} times and no answer {no_answer} times.",
        ]
        link = _lead_link(lead)
        if link:
            lines += ["", f"Open in MyDay CRM: {link}"]
        return subject, "\n".join(lines)

    when = (
        "today"
        if days_away == 0
        else f"in {days_away} days"
        if days_away is not None
        else "soon"
    )

    subject = f"Renewal {when}: {name} (WhatsApp alert failed)"

    lines = [
        "Your WhatsApp renewal reminder could not be delivered, so here it is by email.",
        "",
        f"Customer: {name}",
        f"Tel: {phone}",
        f"Renewal: {when}",
        "",
        "Worth a call before they auto-renew with their current insurer.",
    ]

    link = _lead_link(lead)
    if link:
        lines += ["", f"Open in MyDay CRM: {link}"]

    return subject, "\n".join(lines)


def send_for_row(row: dict, reason: str, days_away: int | None = None) -> None:
    """Send the fallback for one terminally-failed renewal notification.

    NEVER RAISES. It is called from the drain's failure path, where something
    has already gone wrong — letting a second failure escape would abandon the
    remaining rows in the batch over an email nobody is waiting on.

    `row` carries the embedded reminder -> lead / broker (repo._PENDING_SELECT).
    """
    row_id = row.get("id")
    reminder = row.get("reminder") or {}
    lead = reminder.get("lead") or {}
    broker = reminder.get("broker") or {}

    to = (broker.get("email") or "").strip()
    if not to:
        # Nothing to send to. Still claim the row so the tracking screen shows
        # why no email exists, rather than looking like it was never tried.
        try:
            if repo.claim_fallback_email(row_id):
                repo.record_fallback_email_error(row_id, "Broker has no email address")
        except Exception:
            logger.exception("Could not record the missing-email fallback for %s", row_id)
        return

    try:
        # CLAIM FIRST. Two paths can reach a row's terminal state at once — the
        # drain exhausting its attempts while a delivery webhook reports failed
        # — and the conditional update is what stops both sending.
        if not repo.claim_fallback_email(row_id):
            return

        subject, text = _compose(lead, broker, days_away, reminder)
        try:
            email_client.send(to=to, subject=subject, text=text)
        except Exception as err:
            repo.record_fallback_email_error(row_id, str(err))
            logger.exception("Renewal fallback email failed for %s", row_id)
    except Exception:
        logger.exception("Renewal fallback could not run for %s", row_id)


def send_for_row_id(row_id: str, reason: str) -> None:
    """Same, for a caller holding only an id — the delivery webhook.

    NEVER RAISES. Mirrors whatsapp_fallback.send_for_row_id so the webhook can
    treat the two queues' fallbacks interchangeably.
    """
    try:
        rows = repo.get_for_fallback(row_id).data or []
        if not rows:
            logger.error("Renewal fallback: row %s not found", row_id)
            return
        send_for_row(rows[0], reason)
    except Exception:
        logger.exception("Renewal fallback lookup failed for row %s", row_id)
