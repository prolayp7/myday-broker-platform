"""Data access for the reminder table. Only talks to Supabase."""

from db import supabase

_REMINDER_SELECT = (
    "id, lead_id, broker_id, kind, due_at, note, status, "
    "completed_at, completed_by, source_contact_log_id, attempt_count, "
    "auto_rule, auto_for_renewal_date, "
    "created_at, updated_at, "
    # STAGE AND THE RENEWAL DATE COME FROM DIFFERENT TABLES. stage is the
    # lead's own; renewal_date lives on the PRINCIPAL driver (0098), so the
    # embed reaches through the lead to it. Both are what let the Reminders
    # page be triaged without opening anything: a Lost lead renewing next week
    # is a different job from a Quote Done chasing a document.
    #
    # driver comes back as a LIST even filtered to one row — PostgREST does not
    # know the partial unique index makes it at most one. services/reminder.py
    # flattens it.
    "lead(id, name, phone, stage, driver(renewal_date, is_principal))"
)


def list_for_lead(lead_id: str):
    """A lead's follow-ups, soonest first.

    Includes closed ones: the Contact History panel shows what was already done
    for this customer, struck through, not just what is outstanding.
    """
    return (
        supabase.table("reminder")
        .select(_REMINDER_SELECT)
        .eq("lead_id", lead_id)
        .order("due_at")
        .execute()
    )


def list_open_for_broker(broker_id: str, due_before: str):
    """This broker's OPEN reminders due at or before a moment — what My Day
    counts as overdue and due-today.

    Hits idx_reminder_open_due, the partial index on (broker_id, due_at) where
    status = 'Open'. Partial because closed rows accumulate forever and this
    query never looks at them.
    """
    return (
        supabase.table("reminder")
        .select(_REMINDER_SELECT)
        .eq("broker_id", broker_id)
        .eq("status", "Open")
        .lte("due_at", due_before)
        .order("due_at")
        .execute()
    )


def list_all_open_for_broker(broker_id: str):
    """Every open reminder this broker holds, soonest first — the Reminders
    page, which is a worklist rather than a day view.

    NO DUE-DATE CEILING, unlike list_open_for_broker. My Day answers "what is
    due today"; this answers "what do I owe at all", and a follow-up set for
    next month is still something the broker chose to write down.

    Same partial index, which covers (broker_id, due_at) — the ordering comes
    off it directly.
    """
    return (
        supabase.table("reminder")
        .select(_REMINDER_SELECT)
        .eq("broker_id", broker_id)
        .eq("status", "Open")
        .order("due_at")
        .execute()
    )


def leads_due_for_renewal_reminder(stages: list[str]):
    """Leads in these stages whose principal driver has a renewal date.

    THE SWEEP'S ONE READ. Filtering by stage in the query rather than in Python
    keeps it small — most of the book is 'New', which neither rule looks at.

    The renewal date itself is NOT filtered here. PostgREST cannot express "the
    embedded driver's date is N days away", and the arithmetic belongs in one
    place next to the rules that use it. The rows returned are the tens, not
    the thousands, so the filtering happens in the service.

    driver comes back as a list; the service takes the principal.
    """
    return (
        supabase.table("lead")
        .select(
            "id, name, stage, assigned_broker_id, renewal_reminders_off, "
            "driver(renewal_date, is_principal)"
        )
        .in_("stage", stages)
        .not_.is_("assigned_broker_id", "null")
        .execute()
    )


def insert_auto(values: dict):
    """A reminder the sweep set, written so a re-run is free.

    on_conflict names idx_reminder_auto_cycle's columns: the same lead, rule
    and renewal date is the same reminder, whether it is still open, was
    completed, or the broker dismissed it. ignore_duplicates makes the second
    write a no-op instead of an error, which is what lets the sweep run every
    hour without arguing with anyone.
    """
    return (
        supabase.table("reminder")
        .upsert(
            values,
            on_conflict="lead_id,auto_rule,auto_for_renewal_date",
            ignore_duplicates=True,
        )
        .execute()
    )


def get_by_id(reminder_id: str):
    return (
        supabase.table("reminder")
        .select(_REMINDER_SELECT)
        .eq("id", reminder_id)
        .limit(1)
        .execute()
    )


def insert(values: dict):
    return supabase.table("reminder").insert(values).execute()


def update(reminder_id: str, values: dict):
    """Patch a reminder. `values` is already validated by the service.

    The reminder_closed_has_timestamp constraint (0061) means status and
    completed_at must move together — the service always sets both, and the
    database refuses the write if it ever fails to.
    """
    return (
        supabase.table("reminder")
        .update(values)
        .eq("id", reminder_id)
        .execute()
    )
