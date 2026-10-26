"""Data access for lead_phone — a customer's secondary numbers.

The table has existed since 0063 and until now had NO writer: it was read by
repositories/phone_match.py and populated by nothing, so it returned zero rows
on every call. The keypad is the writer it was designed for.
"""

from db import supabase

_SELECT = "id, lead_id, phone_e164, source, added_by, created_at"


def insert(lead_id: str, phone_e164: str, added_by: str, source: str = "added"):
    """Record a number this customer was reached on.

    Can raise a unique violation (23505) on (lead_id, phone_e164): the same
    number confirmed twice against the same lead is the same fact recorded
    twice, and the caller treats that as success rather than an error.
    """
    return (
        supabase.table("lead_phone")
        .insert(
            {
                "lead_id": lead_id,
                "phone_e164": phone_e164,
                "source": source,
                "added_by": added_by,
            }
        )
        .execute()
    )


def list_for_lead(lead_id: str):
    return (
        supabase.table("lead_phone")
        .select(_SELECT)
        .eq("lead_id", lead_id)
        .order("created_at")
        .execute()
    )


def exists_for_lead(lead_id: str, phone_e164: str) -> bool:
    rows = (
        supabase.table("lead_phone")
        .select("id")
        .eq("lead_id", lead_id)
        .eq("phone_e164", phone_e164)
        .limit(1)
        .execute()
    ).data
    return bool(rows)


def delete(lead_id: str, phone_id: str):
    """Remove one secondary number.

    SCOPED BY BOTH IDS. The phone id alone is enough to find the row, so
    filtering on it only would let any lead's URL delete any other lead's
    number — the lead in the path would be decoration rather than a check.
    """
    return (
        supabase.table("lead_phone")
        .delete()
        .eq("id", phone_id)
        .eq("lead_id", lead_id)
        .execute()
    )
