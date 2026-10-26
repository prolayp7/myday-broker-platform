"""Data access for the ad table. Only talks to Supabase."""

from db import supabase


def list_all():
    return (
        supabase.table("ad")
        .select("*, ad_group(name)")
        .order("created_at")
        .execute()
    )


def get_by_platform_id(platform_ad_id: str):
    """Used by the future webhook ingestion path to find-or-create."""
    return supabase.table("ad").select("*").eq("platform_ad_id", platform_ad_id).execute()


def insert(values: dict):
    return supabase.table("ad").insert(values).execute()


def delete(ad_id: str):
    return supabase.table("ad").delete().eq("id", ad_id).execute()
