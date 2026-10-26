"""Data access for the lead_form table. Only talks to Supabase."""

from db import supabase


def list_all():
    return (
        supabase.table("lead_form")
        .select("*, broker_group(group_number, community(name))")
        .order("created_at")
        .execute()
    )


def get_by_platform_form_id(platform_form_id: str):
    """Used by the future webhook ingestion path — the actual routing
    lookup: given an incoming form_id, which group should this lead go to."""
    return (
        supabase.table("lead_form")
        .select("*")
        .eq("platform_form_id", platform_form_id)
        .execute()
    )


def get_by_id(lead_form_id: str):
    return supabase.table("lead_form").select("*").eq("id", lead_form_id).limit(1).execute()


def count_for_group(group_id: str) -> int:
    """How many forms currently route to this group.

    A group may have several (a split test, seasonal variants), but never
    zero — see services/lead_form.delete_lead_form.
    """
    result = (
        supabase.table("lead_form")
        .select("id", count="exact")
        .eq("group_id", group_id)
        .execute()
    )
    return result.count or 0


def insert(values: dict):
    return supabase.table("lead_form").insert(values).execute()


def delete(lead_form_id: str):
    return supabase.table("lead_form").delete().eq("id", lead_form_id).execute()


def delete_for_group(group_id: str):
    """Remove every form mapped to a group. Only for deleting the group
    itself — see services/broker_group.delete_broker_group."""
    return supabase.table("lead_form").delete().eq("group_id", group_id).execute()
