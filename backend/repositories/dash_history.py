"""Data access for the gaps and claims read off a DASH report (0135)."""

from db import supabase


def replace(driver_id: str, gaps: list[dict], claims: list[dict]):
    """Swap this driver's gaps and claims for a new reading, all or nothing.

    An RPC rather than delete-then-insert from here: both happen inside one
    database call, so a failure can never leave the lists empty.
    """
    return supabase.rpc(
        "replace_dash_history",
        {"p_driver_id": driver_id, "p_gaps": gaps, "p_claims": claims},
    ).execute()


def list_gaps(driver_id: str):
    return (
        supabase.table("driver_insurance_gap")
        .select("gap_from, gap_to, reason")
        .eq("driver_id", driver_id)
        .order("gap_from")
        .execute()
    )


def list_claims(driver_id: str):
    return (
        supabase.table("driver_claim")
        .select(
            "id, date_of_loss, at_fault_percent, whose, claim_driver_license, "
            "claim_driver_name, insurer, claim_number, tp_bi, tp_pd, ab, coll, left_out"
        )
        .eq("driver_id", driver_id)
        .order("date_of_loss", desc=True)
        .execute()
    )


def set_left_out(driver_id: str, claim_id: str, left_out: bool):
    """The broker's "Leave out" / "Undo". Scoped to the driver so a claim id
    from another customer matches nothing."""
    return (
        supabase.table("driver_claim")
        .update({"left_out": left_out})
        .eq("id", claim_id)
        .eq("driver_id", driver_id)
        .execute()
    )
