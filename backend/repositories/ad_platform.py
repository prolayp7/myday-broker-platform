"""Data access for the ad_platform table. Only talks to Supabase."""

from db import supabase


def list_all():
    return supabase.table("ad_platform").select("*").order("name").execute()
