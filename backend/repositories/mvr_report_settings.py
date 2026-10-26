"""Data access for mvr_report_settings (migrations/0132) — the one row holding
the head-office report's password hash. Only talks to Supabase."""

from datetime import datetime, timezone

from db import supabase


def get():
    return supabase.table("mvr_report_settings").select("password_hash, updated_at").eq("id", True).execute()


def set_password(password_hash: str):
    """Replaces the hash AND bumps updated_at, which is what invalidates every
    unlock token issued under the old password."""
    return (
        supabase.table("mvr_report_settings")
        .update({"password_hash": password_hash, "updated_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", True)
        .execute()
    )
