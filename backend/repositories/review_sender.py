"""Data access for review.broker, the submission review's senders. Only talks
to Supabase.

A CLIENT PER CALL, CLOSED AFTER. schema() builds a new postgrest client on the
default HTTP/2, not db.py's HTTP/1.1 one, and sharing an HTTP/2 client across
request threads is the 500 that db.py explains. These calls are rare and only
an admin makes them, so one connection each costs nothing.
"""

from db import supabase

_SELECT = "id, email, approved, created_at"


def _run(build):
    with supabase.schema("review") as client:
        return build(client.table("broker")).execute()


def list_for_domain(domain: str):
    return _run(lambda t: t.select(_SELECT).like("email", f"%@{domain}").order("email"))


def get_by_id(sender_id: str):
    return _run(lambda t: t.select(_SELECT).eq("id", sender_id).limit(1))


def get_by_email(email: str):
    return _run(lambda t: t.select(_SELECT).eq("email", email).limit(1))


def insert(email: str):
    return _run(lambda t: t.insert({"email": email, "approved": True}))


def set_approved(sender_id: str, approved: bool):
    return _run(lambda t: t.update({"approved": approved}).eq("id", sender_id))
