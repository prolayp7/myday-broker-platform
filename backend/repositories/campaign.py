"""Data access for the campaign table. Only talks to Supabase — no HTTP
concerns, no business rules."""

from db import supabase


def list_all():
    # Reach across the platform FK so callers get its name without a
    # second round trip. No broker_group join — campaign no longer routes.
    return (
        supabase.table("campaign")
        .select("*, ad_platform(name)")
        .order("created_at")
        .execute()
    )


def insert(values: dict):
    return supabase.table("campaign").insert(values).execute()


def delete(campaign_id: str):
    return supabase.table("campaign").delete().eq("id", campaign_id).execute()
