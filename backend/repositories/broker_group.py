"""Data access for the broker_group table. Only talks to Supabase — no HTTP
concerns, no business rules."""

from db import supabase


def list_all():
    # "*, community(name)" reaches across the community_id FK to attach the
    # community name, so callers can show "Dragon 1" without a second query.
    # "broker(id)" embeds the reverse FK as an array of just ids — enough to
    # count. "lead_form(...)" pulls the mapped form's name/id too, so the list
    # can show WHICH Meta form routes to each group, not just whether one does.
    return (
        supabase.table("broker_group")
        .select("*, community(name), broker(id), lead_form(id, name, platform_form_id)")
        .order("created_at")
        .execute()
    )


def insert(community_id: str, name: str):
    return (
        supabase.table("broker_group")
        .insert({"community_id": community_id, "name": name})
        .execute()
    )


def rename(group_id: str, name: str):
    return supabase.table("broker_group").update({"name": name}).eq("id", group_id).execute()


def find_by_name_in_community(community_id: str, name: str):
    """Groups in a community whose name matches (case-insensitive) — used to
    reject a duplicate name within the same community. Names are unique
    per-community, not globally: "GTA Team" may exist in two communities."""
    return (
        supabase.table("broker_group")
        .select("id, community_id")
        .eq("community_id", community_id)
        .ilike("name", name)
        .execute()
    )


def get_community_id(group_id: str):
    """Just the group's community_id — needed on rename, where the request
    carries only the new name, to scope the duplicate check."""
    return supabase.table("broker_group").select("community_id").eq("id", group_id).execute()


def count_brokers(group_id: str):
    """How many brokers currently sit in this group — used to enforce the
    per-group headcount cap. A broker is either in a real working group
    (counts toward that group's cap) or moved into the Recycle Bin (counts
    toward nothing, see is_recycle_bin) — there's no third
    "deactivated but still occupying a real group" state anymore, so a
    plain count of group_id is the correct, complete picture."""
    return (
        supabase.table("broker")
        .select("id", count="exact")
        .eq("group_id", group_id)
        .execute()
    )


def is_recycle_bin(group_id: str) -> bool:
    """Whether this group is the one global Recycle Bin — exempt from the
    10-broker cap, since it's a holding area, not a working team."""
    result = supabase.table("broker_group").select("is_recycle_bin").eq("id", group_id).execute()
    return bool(result.data) and result.data[0]["is_recycle_bin"]


def delete(group_id: str):
    return supabase.table("broker_group").delete().eq("id", group_id).execute()
