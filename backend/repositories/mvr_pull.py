"""Data access for mvr_pull (migrations/0132). Only talks to Supabase."""

from db import supabase

_SELECT = (
    "id, broker_id, completed_by, driver_name, licence_number, requested_on, "
    "aris_order_number, business_reason, info_retrieved, created_at"
)

# Paged for the reason repositories/lead_cost.py gives: PostgREST truncates an
# unbounded select at 1,000 rows WITHOUT an error, and a register missing lines
# is worse than a slow one.
_PAGE = 500


def _all_pages(build_query) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        page = build_query(offset, _PAGE).execute().data or []
        rows.extend(page)
        if len(page) < _PAGE:
            return rows
        offset += _PAGE


def insert(values: dict):
    return supabase.table("mvr_pull").insert(values).execute()


def get(pull_id: str):
    return supabase.table("mvr_pull").select(_SELECT).eq("id", pull_id).execute()


def between(start: str, end: str, broker_id: str | None = None) -> list[dict]:
    """Every pull requested in [start, end), oldest first — register order.

    Half-open on DATES: end is the 1st of the following month, so the last day
    of the month is included and the 1st of the next is not.
    """

    def build(offset: int, limit: int):
        query = (
            supabase.table("mvr_pull")
            .select(_SELECT)
            .gte("requested_on", start)
            .lt("requested_on", end)
        )
        if broker_id:
            query = query.eq("broker_id", broker_id)
        return query.order("requested_on").order("created_at").range(offset, offset + limit - 1)

    return _all_pages(build)


def brokers_with_pulls() -> list[dict]:
    """broker_id and completed_by for every pull ever logged.

    For the admin's Broker dropdown. Read from the register rather than the
    broker table so it lists exactly the people who appear in it — including a
    broker since archived, whose lines still have to be found.
    """
    return _all_pages(
        lambda offset, limit: (
            supabase.table("mvr_pull")
            .select("broker_id, completed_by")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
    )


def update(pull_id: str, values: dict):
    return supabase.table("mvr_pull").update(values).eq("id", pull_id).execute()


def delete(pull_id: str):
    return supabase.table("mvr_pull").delete().eq("id", pull_id).execute()
