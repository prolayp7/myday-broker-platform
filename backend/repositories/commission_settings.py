"""Data access for commission_settings. Only talks to Supabase.

ONE ROW, id = 1, pinned by a CHECK constraint (migrations/0084) — so both
functions address that row directly rather than searching for it. There is no
insert path: the migration seeds the row and the CHECK makes a second one
impossible, which is what removes "which row?" from every read.
"""

from db import supabase

SETTINGS_ID = 1

# broker_share_pct (0134) must be here: without it every read reports the
# share as missing, and My Day can only show "—" for commission earned.
_SETTINGS_SELECT = (
    "id, auto_rate_pct, property_rate_pct, broker_share_pct, created_at, updated_at"
)


def get():
    """The two rates every projection is multiplied by, and the broker's share."""
    return (
        supabase.table("commission_settings")
        .select(_SETTINGS_SELECT)
        .eq("id", SETTINGS_ID)
        .limit(1)
        .execute()
    )


def update(values: dict):
    """Change the rate card.

    Returns the updated row, so the caller answers with what was actually
    stored rather than echoing back what it was sent — the two differ if the
    database rounds a numeric.
    """
    return (
        supabase.table("commission_settings")
        .update(values)
        .eq("id", SETTINGS_ID)
        .execute()
    )
