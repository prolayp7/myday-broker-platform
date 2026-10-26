"""Data access for the country table. Fixed, pre-seeded lookup list —
no create/delete from the app, only reads."""

from db import supabase


def list_all():
    return supabase.table("country").select("*").order("name").execute()
