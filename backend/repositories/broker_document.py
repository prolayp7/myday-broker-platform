"""Data access for the broker_document table (migrations/0070). Only talks to
Supabase.

Columns are listed explicitly rather than select("*"), following the rule in
repositories/broker.py: a column added to the table later must not start
appearing in responses on its own.

object_path IS read here, unlike a secret — the service needs it to sign a URL
and to delete the superseded object. It is the response model
(schemas/broker_document.py) that keeps it from reaching the browser.
"""

from db import supabase

COLUMNS = (
    "id, broker_id, kind, object_path, file_name, content_type, "
    "size_bytes, uploaded_by, uploaded_at"
)


def list_by_broker(broker_id: str):
    return (
        supabase.table("broker_document")
        .select(COLUMNS)
        .eq("broker_id", broker_id)
        .order("kind")
        .execute()
    )


def get(broker_id: str, kind: str):
    return (
        supabase.table("broker_document")
        .select(COLUMNS)
        .eq("broker_id", broker_id)
        .eq("kind", kind)
        .execute()
    )


def upsert(values: dict):
    """Write the document, replacing whatever that broker had of this kind.

    on_conflict names the (broker_id, kind) unique constraint, so a re-upload
    updates the existing row instead of failing — the replacement semantics the
    table was built for. Without it a second E&O certificate would be a 409 the
    service would have to unpick into a read-then-update.
    """
    return (
        supabase.table("broker_document")
        .upsert(values, on_conflict="broker_id,kind")
        .execute()
    )


def delete(broker_id: str, kind: str):
    return (
        supabase.table("broker_document")
        .delete()
        .eq("broker_id", broker_id)
        .eq("kind", kind)
        .execute()
    )
