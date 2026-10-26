"""Data access for website leads. Only talks to Supabase.

The routing itself lives in SQL (migration 0127) — the drain, the switch/limit
change and the admin board are each one RPC, so the lock that orders them is
held by the database, not by this process.
"""

from db import supabase


def insert_submission_if_new(values: dict):
    """Store a submission as 'waiting'. Relies on webhook_event's unique
    dedup_key, exactly as the Meta path does: a retried or double-clicked
    submission is an empty result, not a second row."""
    return (
        supabase.table("webhook_event")
        .upsert(values, on_conflict="dedup_key", ignore_duplicates=True)
        .execute()
    )


def assign_waiting(limit: int):
    """Run one batch of the drain — at most `limit` submissions. Returns
    {assigned, failed, waiting}."""
    return supabase.rpc("assign_waiting_website_leads", {"p_limit": limit}).execute()


def board():
    """Every broker with their website switch, limit and today's count, plus
    the waiting and failed queue sizes."""
    return supabase.rpc("website_lead_board", {}).execute()


def set_broker_setting(broker_id: str, is_on: bool | None, daily_cap: int | None):
    """Upsert one broker's switch and/or limit, under the drain's lock."""
    return supabase.rpc(
        "set_website_broker_setting",
        {"p_broker_id": broker_id, "p_is_on": is_on, "p_daily_cap": daily_cap},
    ).execute()


def set_broker_settings(broker_ids: list[str], is_on: bool | None, daily_cap: int | None):
    """The same switch and/or limit for every broker listed, in one
    transaction under the drain's lock (migration 0129). Returns their rows."""
    return supabase.rpc(
        "set_website_broker_settings",
        {"p_broker_ids": broker_ids, "p_is_on": is_on, "p_daily_cap": daily_cap},
    ).execute()
