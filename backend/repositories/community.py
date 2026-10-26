"""Data access for the community table. Only talks to Supabase — no HTTP
concerns, no business rules. If Supabase raises, the error bubbles up to
the service layer to interpret."""

from db import supabase


def list_all():
    return (
        supabase.table("community")
        .select("*, community_country(country:country_id(id, name))")
        .order("name")
        .execute()
    )


def insert(name: str):
    return supabase.table("community").insert({"name": name}).execute()


def rename(community_id: str, name: str):
    """Change a community's name. Can raise a unique violation (23505) — the
    name is unique case-insensitively; the service turns that into a 409."""
    return (
        supabase.table("community")
        .update({"name": name})
        .eq("id", community_id)
        .execute()
    )


def delete(community_id: str):
    return supabase.table("community").delete().eq("id", community_id).execute()


def set_countries(community_id: str, country_ids: list[str]):
    supabase.table("community_country").delete().eq("community_id", community_id).execute()
    if not country_ids:
        return
    supabase.table("community_country").insert(
        [{"community_id": community_id, "country_id": cid} for cid in country_ids]
    ).execute()
