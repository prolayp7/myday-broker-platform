"""Business logic for ad platforms. List-only for now — new platforms are
rare enough to add directly via the Supabase SQL Editor."""

from repositories import ad_platform as repo


def list_ad_platforms():
    return repo.list_all().data
