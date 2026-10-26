"""Data access for the audit_log table (migrations/0012_auth.sql). Purely
observational — see docs/round-robin-allocation-design.md §3 for why the
round-robin's daily-cap CORRECTNESS never depends on this table (that's
lead_delivery, migrations/0018); this is history only."""

from db import supabase


def insert(*, actor_broker_id: str | None, action: str, entity_type: str, entity_id: str, detail: dict | None = None):
    return (
        supabase.table("audit_log")
        .insert({
            "actor_broker_id": actor_broker_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "detail": detail,
        })
        .execute()
    )
