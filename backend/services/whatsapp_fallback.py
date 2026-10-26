"""Emails a broker their lead when the WhatsApp notification has failed for good.

THE RULE. Every assigned lead reaches its broker through at least one channel.
WhatsApp is the primary — faster, and read sooner — and this is what happens
when WhatsApp has definitively given up. See migration 0110's header for the
incident that made it necessary.

WHY A SEPARATE MODULE FROM services/whatsapp.py. That module is the WhatsApp
drain: claim, send, retry, record. This is a different decision — whether a
human still needs telling, and how. Both terminal paths call in here (the drain
when attempts are spent, the delivery webhook when Meta reports 'failed'), and
putting it in either one would make the other import it sideways.

TERMINAL FAILURES ONLY. Never on Pending, Submitted, Delivered, Cancelled, or a
first transient failure. A transient failure is retried seconds later and
usually succeeds; emailing on it would double-notify on every hiccup and train
brokers to ignore both channels. 'Cancelled' means the lead moved to another
broker — that broker gets their own notification, and the original must not be
told about a lead they no longer own.

NEVER RAISES. Called from inside a drain pass and from inside a webhook handler,
both of which have more important work to finish. A fallback that could break
the caller would be worse than no fallback: it would turn one failed
notification into a stalled queue.
"""

import logging
import os
from urllib.parse import quote

import email_client
from repositories import whatsapp_notification as repo

logger = logging.getLogger("myday.whatsapp.fallback")

# Where a broker lands when they click through. Configured rather than derived:
# the backend has no reliable idea what public URL the frontend is served from,
# and guessing would produce a link that 404s on a phone at the worst moment.
_CRM_URL = os.environ.get("CRM_PUBLIC_URL", "").rstrip("/")

# Shown when a lead has no licensing answer — matches services/whatsapp.py's
# handling so the two channels say the same thing about the same lead.
_NOT_PROVIDED = "Not provided"

# The Meta form's own vocabulary, turned into English at the presentation edge.
# Deliberately a copy of the map in services/whatsapp.py rather than an import:
# that module owns the WhatsApp template's wording, this one owns the email's,
# and coupling them would mean a change to one silently rewriting the other.
_LICENSED_LABELS = {
    "2020_or_before": "2020 or before",
    "2021_or_later": "2021 or later",
}


def _lead_link(lead: dict) -> str:
    """A link that lands the broker on this lead in the CRM.

    THERE IS NO /leads/{id} ROUTE. The frontend's lead list reads its filter
    state from the query string (useSearchParams), so a search for the
    customer's phone number is the closest thing to a deep link that exists —
    and a phone number is unique enough in practice to land on one lead.

    Returns "" when the number or the base URL is missing, and the caller then
    omits the line entirely rather than printing a broken link.
    """
    phone = lead.get("phone_e164") or lead.get("phone") or ""
    if not _CRM_URL or not phone:
        return ""
    return f"{_CRM_URL}/leads?q={quote(phone)}"


def _compose(lead: dict, broker: dict, reason: str) -> tuple[str, str]:
    """The subject and body. Returns (subject, text).

    SAYS WHY IT ARRIVED, in the first line. A broker who gets an unexplained
    email about a lead they may already have seen on WhatsApp has to work out
    whether it is a duplicate; saying "WhatsApp didn't reach you" answers that
    before they wonder.

    CARRIES MORE THAN THE WHATSAPP TEMPLATE, because it can. The template is
    frozen at approval and deliberately bare — three facts under a heading. Mail
    has room for the link, which is what turns this from a notification into
    something the broker can act on without hunting for the lead.

    THE FAILURE REASON IS NOT IN THE BODY. A broker cannot act on
    "131026 undeliverable" and it would only undermine confidence in the tool.
    It is on the notification row for whoever reads the tracking screen.
    """
    name = lead.get("name") or _NOT_PROVIDED
    phone = lead.get("phone_e164") or lead.get("phone") or _NOT_PROVIDED
    licensed = _LICENSED_LABELS.get(
        lead.get("licensed_in_canada"), lead.get("licensed_in_canada")
    ) or _NOT_PROVIDED

    subject = f"New lead assigned: {name} (WhatsApp alert failed)"

    lines = [
        "Your WhatsApp alert for this lead could not be delivered, so here it is by email.",
        "",
        f"Name: {name}",
        f"Tel: {phone}",
        f"Year first licensed: {licensed}",
    ]

    link = _lead_link(lead)
    if link:
        lines += ["", f"Open in MyDay CRM: {link}"]

    return subject, "\n".join(lines)


def send_for_row(row: dict, reason: str) -> None:
    """Email the broker about one notification that has failed terminally.

    `row` must carry the embedded lead and broker (repo._PENDING_SELECT). The
    drain already holds one; the webhook path fetches it via get_for_fallback.

    NEVER RAISES — see the module docstring.
    """
    row_id = row.get("id")
    lead = row.get("lead") or {}
    broker = row.get("broker") or {}

    try:
        if not email_client.is_configured():
            # Not an error: an environment without Resend credentials simply has
            # no fallback, the same way one without Twilio has no WhatsApp. Said
            # once at debug so it does not fill the log on every failure.
            logger.debug("Email fallback not configured — skipping row %s", row_id)
            return

        to = (broker.get("email") or "").strip()
        if not to:
            # Recorded on the row rather than only logged: "this broker has no
            # address" is exactly the kind of thing that needs to be visible on
            # the tracking screen, not discovered in a log weeks later.
            if repo.claim_fallback_email(row_id):
                repo.record_fallback_email_error(row_id, "Broker has no email address")
            logger.error(
                "WhatsApp fallback: broker %s has no email address (row %s)",
                row.get("broker_id"), row_id,
            )
            return

        # CLAIM BEFORE SENDING. The update is the lock — if another path already
        # emailed this row, this returns False and we stop. See the repository.
        if not repo.claim_fallback_email(row_id):
            logger.debug("Email fallback already sent for row %s — skipping", row_id)
            return

        subject, text = _compose(lead, broker, reason)

        try:
            email_client.send(to=to, subject=subject, text=text)
        except email_client.EmailError as err:
            repo.record_fallback_email_error(row_id, str(err))
            # ERROR, not warning: this is the last channel. Both of them failing
            # means a broker has a lead and does not know it, which is the exact
            # silence this feature exists to break.
            logger.error("WhatsApp fallback email failed for row %s: %s", row_id, err)
            return

        logger.info(
            "WhatsApp fallback email sent for row %s (WhatsApp failed: %s)",
            row_id, reason,
        )

    except Exception:
        # Anything unmodelled — a Supabase hiccup, a bug in here. Logged loudly
        # and swallowed, because the caller is mid-drain or mid-webhook and must
        # finish its own work.
        logger.exception("WhatsApp fallback raised unexpectedly for row %s", row_id)


def send_for_row_id(row_id: str, reason: str) -> None:
    """Same, for a caller that has only an id — the delivery webhook.

    NEVER RAISES.
    """
    try:
        result = repo.get_for_fallback(row_id)
        rows = result.data or []
        if not rows:
            logger.error("WhatsApp fallback: row %s not found", row_id)
            return
        send_for_row(rows[0], reason)
    except Exception:
        logger.exception("WhatsApp fallback lookup failed for row %s", row_id)
