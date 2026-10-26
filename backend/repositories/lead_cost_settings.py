"""Data access for lead_cost_settings. Only talks to Supabase.

ONE ROW, id = 1, pinned by a CHECK constraint (migrations/0088) — so both
functions address that row directly rather than searching for it. There is no
insert path: the migration seeds the row and the CHECK makes a second one
impossible, which is what removes "which row?" from every read.

The same shape as repositories/commission_settings.py, deliberately: two
singleton settings tables with two different access patterns would be one
pattern too many.
"""

from db import supabase

SETTINGS_ID = 1

_SETTINGS_SELECT = "id, cost_per_lead, created_at, updated_at"


def get():
    """The price every system-delivered lead is stamped with."""
    return (
        supabase.table("lead_cost_settings")
        .select(_SETTINGS_SELECT)
        .eq("id", SETTINGS_ID)
        .limit(1)
        .execute()
    )


def update(values: dict):
    """Change the price.

    AFFECTS FUTURE DELIVERIES ONLY. The price is copied onto each delivery when
    it happens (the trigger in 0088), so changing it here cannot reach back
    into a month already billed. That is the whole reason the cost is stamped
    rather than looked up at report time.

    Returns the updated row, so the caller answers with what was actually
    stored rather than echoing back what it was sent — the two differ when the
    database rounds to numeric(10,2).
    """
    return (
        supabase.table("lead_cost_settings")
        .update(values)
        .eq("id", SETTINGS_ID)
        .execute()
    )
