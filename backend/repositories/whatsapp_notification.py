"""Data access for the whatsapp_notification table. Only talks to Supabase.

The rows are created by a database trigger (migration 0109), never from here —
this reads what is due and records what happened to it.
"""

from datetime import datetime, timezone

from db import supabase

# The lead and broker fields the send needs, fetched in the SAME query as the
# queue row via Supabase's embed syntax. Two extra round trips per notification
# would otherwise be the drain's whole cost.
#
# lead.assigned_broker_id is here for the ownership re-check, not for the
# message: a lead reassigned between queueing and sending must not have its
# customer's name and phone delivered to the broker who no longer owns it.
#
# broker.email and broker.name are for the EMAIL FALLBACK (migration 0110), not
# for the WhatsApp message. They are fetched here, in the same round trip, so
# that a terminal failure already holds everything the fallback needs — the
# alternative is a second query at exactly the moment something has just gone
# wrong, which is the worst time to add a dependency.
_PENDING_SELECT = (
    "id, lead_id, broker_id, attempt_count, fallback_email_sent_at, "
    "lead(name, phone, phone_e164, licensed_in_canada, assigned_broker_id), "
    "broker(name, email, phone_e164, whatsapp_opted_in_at)"
)


def claim_pending(limit: int = 25):
    """Rows due to be sent, oldest first.

    A LIMIT because a backlog — Meta down for an hour, a template pulled —
    must not turn one pass into a thousand HTTP calls holding the worker
    thread. The rest are picked up by the next pass seconds later.
    """
    return (
        supabase.table("whatsapp_notification")
        .select(_PENDING_SELECT)
        .eq("status", "Pending")
        .order("created_at")
        .limit(limit)
        .execute()
    )


def mark_submitted(row_id: str, message_sid: str):
    """Meta accepted the message. NOT the same as delivered — only the status
    callback can say that, so the row waits at Submitted until it arrives."""
    return (
        supabase.table("whatsapp_notification")
        .update({
            "status": "Submitted",
            "message_sid": message_sid,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "error_message": None,
        })
        .eq("id", row_id)
        .execute()
    )


def mark_failed(row_id: str, reason: str, *, attempt_count: int, exhausted: bool):
    """The send did not work.

    exhausted is decided by the SERVICE, not here — whether a failure is worth
    another attempt is a business rule, and a repository that imported the
    service to ask would invert the dependency every other repository in this
    package respects. This layer only writes what it is told.

    A row that is not exhausted stays Pending and the drain picks it up again.
    """
    return (
        supabase.table("whatsapp_notification")
        .update({
            "status": "Failed" if exhausted else "Pending",
            "attempt_count": attempt_count,
            "error_message": reason,
        })
        .eq("id", row_id)
        .execute()
    )


def mark_cancelled(row_id: str, reason: str):
    """The lead is no longer this broker's, so the message is not sent.

    An OUTCOME, not a failure: nothing went wrong, the world moved on. Kept as
    a row rather than deleted so the trigger can revive it if the lead comes
    back to this broker (migration 0109), and so the history is legible.
    """
    return (
        supabase.table("whatsapp_notification")
        .update({"status": "Cancelled", "error_message": reason})
        .eq("id", row_id)
        .execute()
    )


def claim_fallback_email(row_id: str) -> bool:
    """Reserve the right to send this row's fallback email. True if we got it.

    THE DUPLICATE GUARD, AND WHY IT IS A CLAIM RATHER THAN A CHECK. The obvious
    shape is "read the row, see if fallback_email_sent_at is null, send, then
    write the timestamp" — and that races. Two things can reach the same row's
    terminal state at once (the drain exhausting its attempts while a delivery
    webhook reports 'failed'), both read null, and the broker gets two emails
    about one lead.

    So the timestamp is written FIRST, conditionally, and the update itself is
    the lock: `.is_("fallback_email_sent_at", "null")` means the second caller's
    update matches no rows and comes back empty. Postgres decides the winner,
    not us.

    THE COST OF THIS ORDER is that a row whose email then fails to send still
    carries a sent_at timestamp. That is deliberate — fallback_email_error
    (migration 0110) is what distinguishes "sent" from "tried and failed", and
    the tracking screen shows both. Sending a broker two copies of the same lead
    is worse than a row that needs a person to read one extra column.
    """
    result = (
        supabase.table("whatsapp_notification")
        .update({"fallback_email_sent_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", row_id)
        .is_("fallback_email_sent_at", "null")
        .execute()
    )
    return bool(result.data)


def record_fallback_email_error(row_id: str, reason: str):
    """The fallback email did not go out. Records why, on the row that already
    holds the WhatsApp failure.

    Does NOT clear fallback_email_sent_at. Clearing it would hand the claim back
    and let the next pass try again — which sounds helpful and is not: the
    failures this sees are a bad address or a missing API key, neither of which
    a retry seconds later fixes, and an address that bounces would be retried
    forever. A person reads this column and acts.
    """
    return (
        supabase.table("whatsapp_notification")
        .update({"fallback_email_error": reason})
        .eq("id", row_id)
        .execute()
    )


def get_for_fallback(row_id: str):
    """The lead and broker behind one row, for a fallback the drain did not send.

    THE WEBHOOK PATH NEEDS THIS AND THE DRAIN DOES NOT. A delivery callback
    arrives carrying an id and a status and nothing else, so when it reports
    'failed' the row's related data has to be read before an email can be
    written. The drain already holds all of it from claim_pending's embed.
    """
    return (
        supabase.table("whatsapp_notification")
        .select(_PENDING_SELECT)
        .eq("id", row_id)
        .limit(1)
        .execute()
    )


def get_by_message_sid(message_sid: str):
    """The delivery webhook's lookup — it knows Meta's message id and nothing else."""
    return (
        supabase.table("whatsapp_notification")
        .select("id, status")
        .eq("message_sid", message_sid)
        .limit(1)
        .execute()
    )


def mark_delivered(row_id: str):
    """Meta's webhook confirmed the message reached the handset. The only
    state that means a broker was actually told."""
    return (
        supabase.table("whatsapp_notification")
        .update({
            "status": "Delivered",
            "delivered_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", row_id)
        .execute()
    )


def mark_failed_from_callback(row_id: str, reason: str):
    """Meta's webhook reported failed. Terminal: the message was
    accepted and then did not arrive, which no retry from our side fixes."""
    return (
        supabase.table("whatsapp_notification")
        .update({"status": "Failed", "error_message": reason})
        .eq("id", row_id)
        .execute()
    )
