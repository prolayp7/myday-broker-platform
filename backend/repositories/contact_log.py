"""Data access for the contact_log table. Only talks to Supabase."""

from db import supabase

_CONTACT_SELECT = (
    "id, lead_id, broker_id, outcome, stage_at_contact, "
    "notes, occurred_at, created_at, updated_at, "
    "broker(id, name)"
)


def list_for_lead(lead_id: str):
    """A lead's whole timeline, newest first.

    Ordered by occurred_at, NOT created_at: a broker logging yesterday's
    WhatsApp message writes a row today, and sorting by when the row was
    written would put it after things that happened later.
    """
    return (
        supabase.table("contact_log")
        .select(_CONTACT_SELECT)
        .eq("lead_id", lead_id)
        .order("occurred_at", desc=True)
        .execute()
    )


def get_by_id(contact_id: str):
    return (
        supabase.table("contact_log")
        .select(_CONTACT_SELECT)
        .eq("id", contact_id)
        .limit(1)
        .execute()
    )


def insert(values: dict):
    """Write one contact row.

    `values` is built by the service — stage_at_contact in particular is read
    from the lead server-side and never accepted from the client, or a broker
    could file a call against a stage the lead was never in.
    """
    return supabase.table("contact_log").insert(values).execute()


def set_notes(contact_id: str, notes: str | None):
    """Add or replace a broker's note on a contact.

    Editable after the fact because the note is the only thing that makes a
    six-week-old row readable, and the broker is its only source.

    Null clears it: a note typed against the wrong call has to be removable,
    not just overwritable.
    """
    return (
        supabase.table("contact_log")
        .update({"notes": notes})
        .eq("id", contact_id)
        .execute()
    )
