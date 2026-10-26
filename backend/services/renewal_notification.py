"""Send the queued renewal-ladder WhatsApps.

The sweep (services/reminder.py) decides a rung is due and writes a reminder;
migration 0118's trigger queues a notification in the same transaction. This
module is the other half: it claims those rows and talks to Meta.

A NEAR-COPY OF services/whatsapp.py's drain, deliberately. That one is live and
carries the lead alerts brokers depend on; a shared module with a mode flag
would put both features in one blast radius for the sake of avoiding a hundred
lines. What they genuinely share — the transport, the error taxonomy, the
retry arithmetic — already lives in whatsapp_client.

WHAT IS DIFFERENT, and why:

  - The queue is keyed on reminder_id alone, so broker, lead and renewal date
    are read live rather than copied. A reassignment needs no handling at all.
  - Every business fact is RE-CHECKED at send time, not trusted from queue
    time. A Pending row can sit through an outage; the world moves.
  - It polls every 60s, not every 5. A late lead is lost to whoever calls
    first; a renewal nudge due at 9am is not that urgent.
"""

import asyncio
import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import whatsapp_client
from repositories import renewal_notification as repo
from services import renewal_fallback

logger = logging.getLogger("myday.renewal.notification")

_TZ = ZoneInfo("America/Toronto")

# Slower than the lead-alert drain's 5s, on purpose — see the module docstring.
DRAIN_INTERVAL_SECONDS = 60

# Same as the lead alerts'. Four attempts over four minutes is enough for a
# transient Meta blip; past that it is not transient.
MAX_ATTEMPTS = 4

# The stages a ladder nudge is for. Mirrors services/reminder.py::_ACTIVE_STAGES
# — a lead that has become Sold, Issued or Lost since queueing must not be
# chased. Deliberately a copy rather than an import: that module owns when a
# rung is CREATED, this one owns whether it still deserves to be SENT, and
# coupling them would make one file's edit silently change the other's meaning.
_ACTIVE_STAGES = ("New", "In Progress", "Quote Done", "Pending")

# Outside this, a queued row has missed its moment. 30 is the top of the ladder;
# below 0 the renewal has already passed.
_MIN_DELTA = 0
_MAX_DELTA = 30


def _principal_renewal_date(lead: dict) -> date | None:
    """The renewal date off this lead's principal driver, read fresh.

    driver arrives as a list even though at most one row is principal —
    PostgREST does not know idx_driver_one_principal_per_lead exists. Same
    shape services/reminder.py uses.
    """
    for driver in lead.get("driver") or []:
        if driver.get("is_principal") and driver.get("renewal_date"):
            return date.fromisoformat(str(driver["renewal_date"])[:10])
    return None


def _variables(broker: dict, lead: dict, delta: int) -> dict[str, str]:
    """The four template variables, by position.

    Matches the approved `renewal_nudge` EXACTLY — its shape is frozen at
    approval, so this order is not free to change without submitting a new
    template and updating WHATSAPP_RENEWAL_TEMPLATE_NAME.

        {{1}} broker name       "Hi {{1}}, your customer …"
        {{2}} customer name
        {{3}} customer phone
        {{4}} days until renewal

    {{1}} IS THE BROKER, not the customer — the reverse of the lead-alert
    template, and the easy mistake to make when copying that module.

    THE FULL NAME, not a first name. broker has one `name` column (0003) and no
    first_name anywhere in the schema. claude_client.split_name() exists but
    calls the Claude API for multi-word names and returns blanks when it is
    unavailable — an LLM round trip per message, and "Hi , your customer…"
    whenever it fails. "Hi Eldho George" is fine.

    {{4}} IS THE LIVE DELTA, computed by the caller at send time. A row queued
    at 30 days that goes out two days late says 28, because the broker is
    looking at the same calendar we are.
    """
    return {
        "1": broker.get("name") or "there",
        "2": lead.get("name") or "your customer",
        # phone_e164 in preference to phone so the broker can tap to call;
        # falls back to the raw number, since a badly formatted one a human can
        # still read beats showing them nothing.
        "3": lead.get("phone_e164") or lead.get("phone") or "Not provided",
        "4": str(delta),
    }


def _callback_variables(lead: dict) -> dict[str, str]:
    """The four variables of the approved `call_reminder` template.

    Its shape is frozen at approval, so this order is not free to change
    without submitting a new template:

        {{1}} customer name
        {{2}} customer phone
        {{3}} times they picked up
        {{4}} times they did not

    {{1}} IS THE CUSTOMER — the reverse of renewal_nudge, whose {{1}} is the
    broker. There is no broker name here at all: the broker set this reminder
    themselves and is the one receiving it, so greeting them by name would tell
    them something they already know.

    THE COUNTS ARE READ NOW, not at queue time, so a broker who rang twice more
    between setting the reminder and its due date sees the current figures.
    Both being 0 is normal and reads correctly — a reminder can be set without
    touching a counter.
    """
    return {
        "1": lead.get("name") or "your customer",
        # phone_e164 in preference to phone so the broker can tap to call;
        # falls back to the raw number, since a badly formatted one a human can
        # still read beats showing them nothing.
        "2": lead.get("phone_e164") or lead.get("phone") or "Not provided",
        "3": str(lead.get("answered_count") or 0),
        "4": str(lead.get("no_answer_count") or 0),
    }


def _buttons(notification_id: str, lead_id: str) -> list[dict]:
    """The two button components the approved template requires.

    A TEMPLATE WITH BUTTONS REJECTS A SEND THAT OMITS THEM. The lead alert has
    none and this has two, which is the whole reason send_template grew a
    `buttons` parameter.

    `index` follows the approved order — quick-reply 0, URL 1 — which Meta
    fixes, not us: quick replies must be grouped before other button types.

    THE URL PARAMETER IS THE SUFFIX ONLY. The approved template holds
    `https://crm.policyscanner.ca/leads?lead=` and Meta appends this to it;
    sending the whole URL would produce the base twice.

    The quick-reply payload is set so the component is well-formed, but nothing
    reads it back: Meta returns the button's LABEL in button.payload for
    template quick replies, so services/renewal_inbound.py identifies a tap by
    context.id instead — which Meta sets and a sender cannot forge.
    """
    return [
        {
            "type": "button",
            "sub_type": "quick_reply",
            "index": "0",
            "parameters": [{"type": "payload", "payload": str(notification_id)}],
        },
        {
            "type": "button",
            "sub_type": "url",
            "index": "1",
            "parameters": [{"type": "text", "text": str(lead_id)}],
        },
    ]


def _is_callback(row: dict) -> bool:
    """A broker's own Callback reminder, rather than a ladder rung (0122).

    auto_rule is null on everything set by hand; kind narrows that to the one
    manual kind with an approved template.
    """
    reminder = row.get("reminder") or {}
    return reminder.get("auto_rule") is None and reminder.get("kind") == "Callback"


def _why_not_sending_callback(row: dict, now: datetime) -> str | None:
    """Whether a broker's own callback still deserves to go.

    FOUR CHECKS, NOT THE LADDER'S TEN. A callback has nothing to do with a
    renewal: the lead may have no renewal date at all, may sit in any stage,
    and renewal_reminders_off (0119) silences the LADDER for a lead — it is not
    a broker saying "never remind me to call this person". Running the renewal
    rules over a callback would cancel most of them for reasons that do not
    apply.

    What still matters is only: is it still wanted, is it time, is it still
    theirs, and may we message them.
    """
    reminder = row.get("reminder") or {}
    lead = reminder.get("lead") or {}
    broker = reminder.get("broker") or {}

    if not reminder:
        return "The reminder no longer exists"

    if reminder.get("status") != "Open":
        return "The broker already closed this reminder"

    # Belt and braces: claim_pending already filters on due_at, but a row that
    # somehow arrives early must not be sent just because it reached here.
    due_at = reminder.get("due_at")
    if due_at:
        due = datetime.fromisoformat(str(due_at))
        # PostgREST returns timestamptz with an offset, so this is normally
        # aware. Guard the naive case anyway: comparing naive to aware raises
        # TypeError, and this runs BEFORE the per-row try block, so one odd
        # row would abort the whole pass every minute. Naive is read as UTC,
        # which is what the column stores.
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due > now:
            return "Not due yet"

    if reminder.get("broker_id") != lead.get("assigned_broker_id"):
        return "The lead was reassigned before the message was sent"

    if not broker.get("whatsapp_opted_in_at"):
        return "Broker is not opted in to WhatsApp notifications"

    return None


def _why_not_sending(row: dict, today: date) -> tuple[str | None, int | None]:
    """Whether this row still deserves to go, and the live day count.

    Returns (reason_to_cancel, delta). A reason means cancel; None means send,
    and delta is then the number for {{4}}.

    QUEUE TIME PROVES NOTHING ABOUT SEND TIME. A Pending row can sit through an
    unconfigured template, a stopped drain or repeated failures, and by the time
    it moves the renewal may have been corrected, the lead sold, the broker
    changed, or the whole thing switched off. Each check below is one of those.

    THE DATE COMPARISON IS THE SUBTLE ONE. A renewal corrected from Oct 15 to
    Oct 20 is a DIFFERENT CYCLE under idx_reminder_auto_cycle (lead_id,
    auto_rule, auto_for_renewal_date), so the sweep creates a second reminder
    and both can sit inside the 0-30 window at once. A live delta alone would
    send both. Comparing against auto_for_renewal_date is what ties a row to
    the cycle it was made for.
    """
    reminder = row.get("reminder") or {}
    lead = reminder.get("lead") or {}
    broker = reminder.get("broker") or {}

    if not reminder:
        return "The reminder no longer exists", None

    if reminder.get("status") != "Open":
        return "The broker already closed this reminder", None

    if lead.get("renewal_reminders_off"):
        return "The broker stopped renewal reminders for this lead", None

    if lead.get("stage") not in _ACTIVE_STAGES:
        return f"The lead is now {lead.get('stage')}", None

    if reminder.get("broker_id") != lead.get("assigned_broker_id"):
        return "The lead was reassigned before the message was sent", None

    if not broker.get("whatsapp_opted_in_at"):
        return "Broker is not opted in to WhatsApp notifications", None

    renewal = _principal_renewal_date(lead)
    if renewal is None:
        return "The renewal date was cleared", None

    queued_for = reminder.get("auto_for_renewal_date")
    if queued_for and str(queued_for)[:10] != renewal.isoformat():
        return "The renewal date changed after this reminder was made", None

    delta = (renewal - today).days
    if not _MIN_DELTA <= delta <= _MAX_DELTA:
        return f"The renewal is {delta} days away, outside the window", None

    return None, delta


def send_pending() -> dict:
    """One pass: claim what is due, re-check it, send it.

    Returns a tally rather than logging alone, so the caller can say what
    happened without re-querying.
    """
    # EACH KIND IS CONFIGURED SEPARATELY, so each is claimed separately. An
    # unapproved callback template must leave callbacks queued WITHOUT stopping
    # renewal delivery, and the reverse — one `return` gating both would make
    # either missing template silence the other feature entirely.
    kinds = []
    if whatsapp_client.is_renewal_configured():
        kinds.append("renewal")
    if whatsapp_client.is_callback_configured():
        kinds.append("callback")

    if not kinds:
        # No approved template yet. Deliberately a no-op rather than an error:
        # 132001 is permanent, so attempting a send would fail every queued row
        # on its first attempt and email brokers about a WhatsApp that has
        # never worked.
        return {"checked": 0}

    now = datetime.now(_TZ)
    today = now.date()
    rows = []
    for kind in kinds:
        rows.extend(repo.claim_pending(kind, now.isoformat()).data or [])

    sent = cancelled = failed = 0

    for row in rows:
        row_id = row["id"]
        reminder = row.get("reminder") or {}
        lead = reminder.get("lead") or {}
        broker = reminder.get("broker") or {}
        is_callback = _is_callback(row)

        # A callback is judged by its own four rules; the ladder's ten are all
        # about a renewal date this row does not have. See
        # _why_not_sending_callback for why running them here would cancel most
        # callbacks for irrelevant reasons.
        if is_callback:
            reason, delta = _why_not_sending_callback(row, now), None
        else:
            reason, delta = _why_not_sending(row, today)

        if reason:
            repo.mark_cancelled(row_id, reason)
            cancelled += 1
            continue

        attempt = row.get("attempt_count", 0) + 1

        if is_callback:
            template, language = whatsapp_client.callback_template()
            variables = _callback_variables(lead)
            # NO BUTTONS. call_reminder is approved without them, and a
            # template with buttons rejects a send that omits them — so the two
            # must agree. "Stop Reminder" is a renewal concept besides: a
            # broker cancels their own callback in the CRM.
            buttons = None
        else:
            template, language = whatsapp_client.renewal_template()
            variables = _variables(broker, lead, delta)
            buttons = _buttons(row_id, lead.get("id"))

        try:
            sid = whatsapp_client.send_template(
                broker.get("phone_e164"),
                variables,
                template=template,
                language=language,
                buttons=buttons,
                # BOTH REMINDER KINDS SHARE ONE SENDER — renewals and callbacks
                # are the thread a broker mutes or reads together, and splitting
                # them across numbers would defeat that. New-lead alerts keep
                # the main sender and stay a separate conversation.
                phone_number_id=whatsapp_client.reminder_phone_number_id(),
            )
        except whatsapp_client.WhatsAppError as err:
            # permanent means retrying cannot help — an unapproved template, a
            # number not on WhatsApp, a dead token.
            exhausted = err.permanent or attempt >= MAX_ATTEMPTS
            repo.mark_failed(row_id, str(err), attempt_count=attempt, exhausted=exhausted)
            failed += 1
            if exhausted:
                logger.error(
                    "%s WhatsApp gave up for notification %s: %s",
                    "Callback" if is_callback else "Renewal", row_id, err,
                )
                renewal_fallback.send_for_row(row, str(err), delta)
            continue
        except Exception as err:
            # Unexpected rather than a known WhatsApp failure. `permanent` is
            # deliberately not consulted — an unknown error is not evidence
            # that retrying is pointless. Same reasoning services/whatsapp.py
            # records.
            exhausted = attempt >= MAX_ATTEMPTS
            repo.mark_failed(row_id, str(err), attempt_count=attempt, exhausted=exhausted)
            failed += 1
            logger.exception("Renewal WhatsApp failed for notification %s", row_id)
            if exhausted:
                renewal_fallback.send_for_row(row, str(err), delta)
            continue

        repo.mark_submitted(row_id, sid)
        sent += 1

    return {
        "checked": len(rows),
        "submitted": sent,
        "cancelled": cancelled,
        "failed": failed,
    }


async def run_send_forever() -> None:
    """Drain the renewal queue every minute, for the life of the app.

    SLEEPS FIRST, unlike the reminder sweep. That one works immediately so a
    deploy does not delay the day's reminders; here a minute either way is
    nothing, and starting with a sleep keeps startup free of network calls.

    to_thread because everything below it is a blocking Supabase or httpx call.
    Running it on the event loop would stall every in-flight request.
    """
    # EITHER template is enough to start. The loop asks again per pass and per
    # kind (send_pending), so turning one on later needs no restart — and an
    # unapproved callback template must never stop renewals from going out.
    renewal_on = whatsapp_client.is_renewal_configured()
    callback_on = whatsapp_client.is_callback_configured()

    if not (renewal_on or callback_on):
        logger.info(
            "Reminder WhatsApp is not configured — drain not started. Set "
            "WHATSAPP_RENEWAL_TEMPLATE_NAME and/or WHATSAPP_CALLBACK_TEMPLATE_NAME "
            "once Meta approves the templates."
        )
        return

    logger.info(
        "Reminder WhatsApp drain started — renewal:%s callback:%s",
        "on" if renewal_on else "off",
        "on" if callback_on else "off",
    )

    while True:
        try:
            await asyncio.sleep(DRAIN_INTERVAL_SECONDS)
            result = await asyncio.to_thread(send_pending)
            if result["checked"]:
                logger.info("Renewal WhatsApp drain: %s", result)
        except asyncio.CancelledError:
            # Shutdown. Re-raised so the lifespan's await actually completes —
            # swallowing it here would hang the process on exit.
            raise
        except Exception:
            # Never let one bad pass end the loop. The next minute is a fresh
            # attempt and nothing was lost: rows stay Pending.
            logger.exception("Renewal WhatsApp drain failed")
