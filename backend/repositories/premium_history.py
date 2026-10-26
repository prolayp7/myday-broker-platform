"""Data access for the premium_history table. Only talks to Supabase."""

from db import supabase

# Every column the New Policy screen needs, including the GENERATED ones —
# annual figures, totals and savings are computed by Postgres (0067), so they
# are read here and never written. Listing them explicitly rather than
# select("*") keeps the response shape stable as the table grows columns this
# screen has no use for.
_PREMIUM_SELECT = (
    "id, lead_id, policy_number, issued_at, effective_date, "

    # BOTH READINGS OF ALL SIX PRODUCTS (0106). One half of each pair is the
    # figure the broker typed and the other was derived by the database
    # trigger; a column missing here cannot be read however correct the
    # migration was, and the screen would render a duration from `undefined`.
    "current_auto_premium_annual, current_auto_premium_monthly, "
    "new_auto_premium_annual, new_auto_premium_monthly, "
    "current_homeowner_premium_annual, current_homeowner_premium_monthly, "
    "new_homeowner_premium_annual, new_homeowner_premium_monthly, "
    "current_tenant_premium_annual, current_tenant_premium_monthly, "
    "new_tenant_premium_annual, new_tenant_premium_monthly, "
    # BOTH UNITS, all generated. Plain sums since 0106 — the annual columns
    # are genuinely annual now, so nothing multiplies here or on the client.
    "total_current_premium_annual, total_current_premium_monthly, "
    "total_new_premium_annual, total_new_premium_monthly, "
    "savings_annual, savings_monthly, "
    # WHICH BOX THE BROKER TYPED IN (0101, narrowed by 0106). It no longer
    # tells a reader how to interpret a figure — both readings are stored and
    # both are correct. It says which one the form reopens on, and which way
    # the trigger derives.
    "current_premium_unit, new_premium_unit, "
    "created_at, updated_at"
)


def get_by_lead(lead_id: str):
    """This lead's premium row, or nothing.

    limit(1) rather than single(): idx_premium_history_lead_id_unique already
    guarantees at most one, and single() raises on zero rows where the caller
    wants an empty list to mean "not entered yet".
    """
    return (
        supabase.table("premium_history")
        .select(_PREMIUM_SELECT)
        .eq("lead_id", lead_id)
        .limit(1)
        .execute()
    )


def issued_between_for_broker(broker_id: str, start_date: str, end_date: str):
    """Annual premium on this broker's policies issued in [start_date, end_date)
    — one calendar month for My Day. Dates, not instants: issued_at is a DATE
    the broker picked, so there is no timezone to get wrong.

    The three per-product annual columns come too, because commission is worked
    out per product (services/commission.py::project).

    Filtered through the LEAD's assigned broker — premium_history has no
    broker column of its own, because a premium belongs to the policy and
    the policy belongs to whoever is holding the lead now.

    issued_at, not created_at: the figure is what the carrier actually
    wrote this month, so a quote entered in July and issued in August
    counts in August. Rows with no issued_at are not yet real business and
    are excluded by the filter rather than summed as zero.
    """
    return (
        supabase.table("premium_history")
        .select(
            "total_new_premium_annual, new_auto_premium_annual, "
            "new_homeowner_premium_annual, new_tenant_premium_annual, "
            "lead!inner(assigned_broker_id)"
        )
        .eq("lead.assigned_broker_id", broker_id)
        .gte("issued_at", start_date)
        .lt("issued_at", end_date)
        .execute()
    )


def insert(values: dict):
    return supabase.table("premium_history").insert(values).execute()


def update(lead_id: str, values: dict):
    return (
        supabase.table("premium_history")
        .update(values)
        .eq("lead_id", lead_id)
        .execute()
    )
