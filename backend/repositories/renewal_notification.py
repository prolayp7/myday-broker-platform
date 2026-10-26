"""Data access for the renewal_notification table. Only talks to Supabase.

Rows are created by a database trigger (migration 0118) the moment a ladder
reminder is written, never from here — this reads what is due and records what
happened to it.

Deliberately a sibling of whatsapp_notification rather than a shared module:
the two queues carry different templates, different failure emails and a
different identity column, and the one thing they genuinely share — the
transport — already lives in whatsapp_client.
"""

from datetime import datetime, timezone

from db import supabase

# EVERYTHING THE SEND NEEDS, READ THROUGH THE REMINDER.
#
# Nothing here is a copy. renewal_notification holds only reminder_id, so the
# broker, the lead and the renewal date are whatever they are RIGHT NOW rather
# than whatever they were when the row was queued. That is what makes a
# reassignment (admin_assign_lead, 0076, moves reminder.broker_id) need no
# handling at all.
#
# Two levels deep — reminder -> lead -> driver — which PostgREST resolves and
# repositories/reminder.py already relies on.
#
# Every field below exists because a send-time check needs it:
#   reminder.status                 the broker may have dismissed it
#   reminder.auto_for_renewal_date  compared against the driver's CURRENT date
#   lead.assigned_broker_id         ownership, against reminder.broker_id
#   lead.renewal_reminders_off      Stop Reminder, tapped after queueing (0119)
#   lead.stage                      it may have become Sold, Issued or Lost
#   driver.renewal_date             the live delta, and {{4}}
#   broker.whatsapp_opted_in_at     consent, re-checked every time
#   broker.email / name             the fallback email, fetched now so a
#                                   terminal failure needs no second query
#   reminder.kind                   which template to send (0122)
#   reminder.due_at                 whether it is time yet (0122)
#   lead.answered_count             {{3}} on the callback template (0121)
#   lead.no_answer_count            {{4}} on the callback template (0121)
#
# reminder!inner, NOT reminder: an ordinary embed filtered on due_at would
# filter the EMBEDDED OBJECT and still hand back the parent notification row —
# the send would go out on time-travelled data and look like it worked. The
# inner join is what makes the filter reject the row itself.
_PENDING_SELECT = (
    "id, reminder_id, attempt_count, fallback_email_sent_at, "
    "reminder!inner("
    "id, status, broker_id, note, kind, due_at, auto_rule, auto_for_renewal_date, "
    "lead(id, name, phone, phone_e164, stage, assigned_broker_id, "
    "renewal_reminders_off, answered_count, no_answer_count, "
    "driver(renewal_date, is_principal)), "
    # broker!reminder_broker_id_fkey, NOT plain broker: `reminder` has TWO
    # foreign keys to broker — broker_id (whose reminder it is) and
    # completed_by (who closed it) — so PostgREST cannot guess which to embed
    # and refuses the whole query with PGRST201. Naming the constraint picks
    # the owner, which is who the WhatsApp is addressed to.
    "broker!reminder_broker_id_fkey(name, email, phone_e164, whatsapp_opted_in_at)"
    ")"
)

# Which reminders each configured template is allowed to send. Kept here beside
# the query so the filter and the vocabulary cannot drift apart.
_LADDER_RULES = ("renewal_30", "renewal_15", "renewal_5")


def claim_pending(kind: str, now: str, limit: int = 25):
    """Rows of one kind that are ACTUALLY DUE, oldest first.

    `kind` is 'renewal' or 'callback'. The two are claimed separately because
    they are configured separately: an unapproved callback template must leave
    callbacks queued without stopping renewal delivery, and the reverse.

    DUE_AT IS THE POINT. Until 0122 this filtered on status alone. A callback
    is set days ahead, so without this filter the broker's phone would buzz
    within a minute of them choosing "next Tuesday".

    THIS ALSO CHANGES WHEN LADDER RUNGS GO OUT, deliberately. The sweep stamps
    each rung due at 9am Toronto (services/reminder.py::_DUE_HOUR) but the old
    claim ignored that, so a rung created by the 1am sweep was sent at 1am.
    Nobody noticed because the renewal template has never been configured.
    With the filter, rungs send at the 9am they were always stamped with.

    A LIMIT for the same reason the lead-alert drain has one: a backlog must not
    turn one pass into a thousand HTTP calls holding the worker thread.
    """
    query = (
        supabase.table("renewal_notification")
        .select(_PENDING_SELECT)
        .eq("status", "Pending")
        .lte("reminder.due_at", now)
    )

    if kind == "callback":
        # Manual reminders only — auto_rule is null on everything a broker
        # sets by hand, and Callback is the only manual kind with a template.
        query = query.is_("reminder.auto_rule", "null").eq("reminder.kind", "Callback")
    else:
        query = query.in_("reminder.auto_rule", list(_LADDER_RULES))

    return query.order("created_at").limit(limit).execute()


def get_by_message_sid(message_sid: str):
    """The row Meta is talking about.

    Used by BOTH inbound paths: a delivery receipt naming this message, and a
    broker tapping Stop Reminder — whose webhook carries the original message id
    as context.id. The partial index on message_sid (0118) covers it.
    """
    return (
        supabase.table("renewal_notification")
        .select(_PENDING_SELECT)
        .eq("message_sid", message_sid)
        .limit(1)
        .execute()
    )


def mark_submitted(row_id: str, message_sid: str):
    """Meta accepted the message. NOT the same as delivered — only the status
    callback can say that, so the row waits at Submitted until it arrives."""
    return (
        supabase.table("renewal_notification")
        .update({
            "status": "Submitted",
            "message_sid": message_sid,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "error_message": None,
        })
        .eq("id", row_id)
        .execute()
    )


def mark_delivered(row_id: str):
    """The webhook says it reached the handset."""
    return (
        supabase.table("renewal_notification")
        .update({
            "status": "Delivered",
            "delivered_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", row_id)
        .execute()
    )


def mark_failed(row_id: str, reason: str, *, attempt_count: int, exhausted: bool):
    """The send did not work.

    exhausted is decided by the SERVICE, not here — whether a failure is worth
    another attempt is a business rule, and a repository that imported the
    service to ask would invert the dependency every other repository in this
    package respects.

    A row that is not exhausted stays Pending and the drain picks it up again.
    """
    return (
        supabase.table("renewal_notification")
        .update({
            "status": "Failed" if exhausted else "Pending",
            "attempt_count": attempt_count,
            "error_message": reason,
        })
        .eq("id", row_id)
        .execute()
    )


def mark_failed_from_callback(row_id: str, reason: str):
    """Meta's webhook reported failed. Terminal: the message was accepted and
    then did not arrive, which no retry from our side fixes.

    Separate from mark_failed because there is no attempt to count — this is
    not our send failing, it is Meta telling us afterwards.
    """
    return (
        supabase.table("renewal_notification")
        .update({"status": "Failed", "error_message": reason})
        .eq("id", row_id)
        .execute()
    )


def get_for_fallback(row_id: str):
    """One row with everything the fallback email needs, by id.

    The delivery webhook holds only an id, so this re-reads the embedded
    reminder, lead and broker rather than making the caller carry them.
    """
    return (
        supabase.table("renewal_notification")
        .select(_PENDING_SELECT)
        .eq("id", row_id)
        .limit(1)
        .execute()
    )


def mark_cancelled(row_id: str, reason: str):
    """The message is not being sent, and nothing went wrong.

    An OUTCOME, not a failure: the renewal date was corrected, the lead was
    sold, the broker tapped Stop. Kept as a row rather than deleted so the
    history is legible and so unique(reminder_id) still holds.
    """
    return (
        supabase.table("renewal_notification")
        .update({"status": "Cancelled", "error_message": reason})
        .eq("id", row_id)
        .execute()
    )


def cancel_pending_ladder_for_lead(lead_id: str, reason: str):
    """Stop Reminder was tapped: drop anything still queued for this lead.

    LADDER RUNGS ONLY, filtered on the embedded reminder's auto_rule. Custom
    reminders live in this same table, and 'stop renewal alerts' must never
    silence a follow-up the broker set for themselves.

    Two steps rather than one, because PostgREST cannot filter an UPDATE by an
    embedded table's column: select the ids first, then update by id.
    """
    rows = (
        supabase.table("renewal_notification")
        .select("id, reminder(lead_id, auto_rule)")
        .eq("status", "Pending")
        .execute()
    )
    ladder = ("renewal_30", "renewal_15", "renewal_5")
    ids = [
        row["id"]
        for row in (rows.data or [])
        if (row.get("reminder") or {}).get("lead_id") == lead_id
        and (row.get("reminder") or {}).get("auto_rule") in ladder
    ]
    if not ids:
        return None
    return (
        supabase.table("renewal_notification")
        .update({"status": "Cancelled", "error_message": reason})
        .in_("id", ids)
        .execute()
    )


def claim_fallback_email(row_id: str) -> bool:
    """Reserve the right to send this row's fallback email. True if we got it.

    THE SAME CLAIM-FIRST SHAPE as whatsapp_notification's, and for the same
    reason: two things can reach a row's terminal state at once — the drain
    exhausting its attempts while a delivery webhook reports 'failed' — and a
    read-then-write would let both send. The conditional update IS the lock;
    Postgres decides the winner.
    """
    result = (
        supabase.table("renewal_notification")
        .update({"fallback_email_sent_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", row_id)
        .is_("fallback_email_sent_at", "null")
        .execute()
    )
    return bool(result.data)


def record_fallback_email_error(row_id: str, reason: str):
    """The fallback email did not go out. Records why, without clearing the
    claim — handing it back would retry a bad address forever. A person reads
    this column and acts."""
    return (
        supabase.table("renewal_notification")
        .update({"fallback_email_error": reason})
        .eq("id", row_id)
        .execute()
    )
