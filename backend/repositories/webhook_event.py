"""Data access for the webhook_event table. Only talks to Supabase."""

from db import supabase


def insert_if_new(values: dict):
    """Insert a raw webhook event. Relies on the DB's unique constraint on
    dedup_key, not an app-level check-then-insert (which would race under
    Meta's documented at-least-once/retry delivery). Returns the inserted
    row, or an empty result if dedup_key already existed."""
    return (
        supabase.table("webhook_event")
        .upsert(values, on_conflict="dedup_key", ignore_duplicates=True)
        .execute()
    )


def mark_processed(event_id: str):
    """A lead was successfully created from this event.

    Clears next_retry_at so a row that succeeded on a retry stops being
    picked up — leaving it set would have the drain re-ingest a lead that
    already exists.
    """
    return (
        supabase.table("webhook_event")
        .update({"status": "processed", "next_retry_at": None})
        .eq("id", event_id)
        .execute()
    )


def mark_failed(event_id: str, reason: str, *, attempt_count: int, next_retry_at: str | None):
    """No lead was created. The raw event stays stored forever regardless (see
    the table's own docstring), so this is never data loss.

    next_retry_at is when the drain may try again, or None once the attempts
    are spent — at which point the row stops moving on its own and needs a
    person. There is no separate "gave up" status: null here IS that state,
    which keeps the two from ever disagreeing.
    """
    return (
        supabase.table("webhook_event")
        .update({
            "status": "failed",
            "failure_reason": reason,
            "attempt_count": attempt_count,
            "next_retry_at": next_retry_at,
        })
        .eq("id", event_id)
        .execute()
    )


def list_stale_received(before_iso: str, limit: int = 50):
    """Events still sitting at 'received' well after they arrived.

    'received' means stored but not yet ingested, which should last
    milliseconds — the webhook ingests immediately. A row still there minutes
    later means the process died mid-ingestion, or something escaped before
    any outcome was written.

    The retry drain selects status='failed' only, so without this sweep such a
    row is invisible to it forever. The exception boundary in
    services/lead_ingestion.py stops new ones appearing; this catches the ones
    a crash can still produce, and anything stranded before that boundary
    existed."""
    return (
        supabase.table("webhook_event")
        .select("id, raw_payload, attempt_count, failure_reason")
        .eq("status", "received")
        .lt("created_at", before_iso)
        .order("created_at")
        .limit(limit)
        .execute()
    )


def get_status(event_id: str):
    """Just the outcome of one event. Used by the retry drain to ask what
    ingest_meta_event decided, since that function reports through the row
    rather than a return value."""
    return (
        supabase.table("webhook_event")
        .select("id, status, attempt_count")
        .eq("id", event_id)
        .execute()
    )


def list_due_for_retry(now_iso: str, limit: int = 50):
    """Failed events whose retry time has arrived, oldest first.

    Oldest first because a customer who submitted a form two hours ago has
    been waiting longer than one who submitted two minutes ago — the same
    ordering the daily lead drain uses.
    """
    return (
        supabase.table("webhook_event")
        .select("id, raw_payload, attempt_count, failure_reason")
        .eq("status", "failed")
        .not_.is_("next_retry_at", "null")
        .lte("next_retry_at", now_iso)
        .order("created_at")
        .limit(limit)
        .execute()
    )
