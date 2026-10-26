"""Data access for the ad_group table. Only talks to Supabase."""

from db import supabase


def list_all():
    return (
        supabase.table("ad_group")
        .select("*, campaign(name)")
        .order("created_at")
        .execute()
    )


def get_by_platform_id(platform_ad_group_id: str):
    """Used by the future webhook ingestion path to find-or-create — look
    up an existing row by the platform's own id before creating a new one."""
    return (
        supabase.table("ad_group")
        .select("*")
        .eq("platform_ad_group_id", platform_ad_group_id)
        .execute()
    )


def insert(values: dict):
    return supabase.table("ad_group").insert(values).execute()


def delete(ad_group_id: str):
    return supabase.table("ad_group").delete().eq("id", ad_group_id).execute()
