"""Reading what was delivered, and crediting a delivery. Only talks to Supabase.

EVERY BULK READ HERE IS PAGED, and that is not defensive habit — it is the
difference between a correct bill and a quietly wrong one. PostgREST caps an
unbounded select (1,000 rows by default) and returns the truncated page with NO
error. At the brokerage's own target size the cap sits inside a normal month:

    20 brokers x 3 leads/day x 30 days ~ 1,800 deliveries

An unpaged read would return 1,000 of those, sum to a plausible-looking figure,
and under-bill by the remainder. Nothing would report it. It would be found by
a broker who noticed their statement was short, months later.

Today's largest table is 426 rows, so this cannot be observed yet. That is
exactly why it goes in now rather than after the first wrong invoice.
"""

from db import supabase

# Rows per request. Comfortably under PostgREST's default cap, so a page is
# never itself truncated — the loop below detects the end by a SHORT page, and
# a page silently clipped to the cap would look like a full one and stop early.
_PAGE = 500

# Lead ids per `in_()` batch. A PostgREST filter travels in the URL, and a few
# thousand uuids would exceed what the server accepts — a limit that also fails
# without a useful error.
_ID_BATCH = 100

_DELIVERY_SELECT = (
    "id, lead_id, broker_id, delivered_at, cost_at_delivery, "
    "credit_reason, credited_by, credited_at"
)


def _all_pages(build_query):
    """Every row a query matches, one page at a time.

    build_query(offset, limit) returns a fresh PostgREST builder — fresh
    because these builders are stateful and reusing one across pages carries
    the previous range with it.
    """
    rows: list[dict] = []
    offset = 0
    while True:
        page = build_query(offset, _PAGE).execute().data or []
        rows.extend(page)
        # A SHORT page means the end. Testing `len(page) == 0` instead would
        # cost one extra round trip on every read; testing against _PAGE is
        # only safe because _PAGE is below the server cap (see above).
        if len(page) < _PAGE:
            return rows
        offset += _PAGE


def deliveries_between(start_utc: str, end_utc: str) -> list[dict]:
    """Every system delivery in a window, whoever it went to.

    Half-open: >= start, < end. A lead delivered at exactly midnight on the 1st
    belongs to the month starting, not the one ending — and an inclusive upper
    bound would put a delivery at 00:00:00.000 on the 1st in both months.

    Not filtered by broker or group. Who is charged depends on custody at a
    point in time (services/lead_cost.py), which cannot be expressed as a
    column filter — so the month is fetched and resolved in Python.
    """
    return _all_pages(
        lambda offset, limit: (
            supabase.table("lead_delivery")
            .select(_DELIVERY_SELECT)
            .gte("delivered_at", start_utc)
            .lt("delivered_at", end_utc)
            .order("delivered_at")
            .range(offset, offset + limit - 1)
        )
    )


def leads_by_ids(lead_ids: list[str]) -> list[dict]:
    """The lead rows behind a set of deliveries — name, phone, group, holder.

    group_id comes from the LEAD, not from the broker who holds it: a lead
    belongs to the group it came in for, and that column never moves. Reading
    the broker's current group instead would shift historical spend between
    groups whenever somebody changed team.
    """
    if not lead_ids:
        return []

    rows: list[dict] = []
    for i in range(0, len(lead_ids), _ID_BATCH):
        batch = lead_ids[i : i + _ID_BATCH]
        rows.extend(
            _all_pages(
                lambda offset, limit, batch=batch: (
                    supabase.table("lead")
                    .select("id, name, phone, group_id, assigned_broker_id")
                    .in_("id", batch)
                    .range(offset, offset + limit - 1)
                )
            )
        )
    return rows


def custody_before(lead_ids: list[str], instant_utc: str) -> list[dict]:
    """Every custody row for these leads recorded before an instant.

    WHAT MAKES A FROZEN MONTH ANSWERABLE. lead_assignment is append-only
    (0023), so the last row before the freeze is who was holding the lead when
    the month closed — a fact that stays true no matter how often the lead
    moves afterwards.

    Returns ALL matching rows rather than the last one per lead: PostgREST has
    no DISTINCT ON, so the reduction happens in the service. Ordered so that
    fold is a single pass.
    """
    if not lead_ids:
        return []

    rows: list[dict] = []
    for i in range(0, len(lead_ids), _ID_BATCH):
        batch = lead_ids[i : i + _ID_BATCH]
        rows.extend(
            _all_pages(
                lambda offset, limit, batch=batch: (
                    supabase.table("lead_assignment")
                    .select("lead_id, broker_id, assigned_at")
                    .in_("lead_id", batch)
                    .lt("assigned_at", instant_utc)
                    .order("assigned_at")
                    .range(offset, offset + limit - 1)
                )
            )
        )
    return rows


def get_delivery_for_lead(lead_id: str):
    """One lead's delivery, or nothing if the system never delivered it."""
    return (
        supabase.table("lead_delivery")
        .select(_DELIVERY_SELECT)
        .eq("lead_id", lead_id)
        .limit(1)
        .execute()
    )


def add_credit(lead_id: str, reason: str, broker_id: str, now_utc: str):
    """Credit a delivery — but only if it is not already credited.

    A CONDITIONAL UPDATE, and the `is_("credit_reason", "null")` is the whole
    guard. Reading "is it credited?" and then writing is two statements with a
    gap between them: two admins on the 4th, both looking at the same screen,
    would both pass the check and the second would overwrite the first's
    reason. One statement has no gap.

    An empty result therefore means somebody got there first, and the service
    turns that into a 409. That is what makes "cannot be credited twice" true
    rather than merely intended.
    """
    return (
        supabase.table("lead_delivery")
        .update(
            {
                "credit_reason": reason,
                "credited_by": broker_id,
                "credited_at": now_utc,
            }
        )
        .eq("lead_id", lead_id)
        .is_("credit_reason", "null")
        .execute()
    )


def remove_credit(lead_id: str):
    """Undo a credit — but only if there is one.

    The mirror of add_credit: an empty result means it was not credited, and
    the service answers 409 rather than reporting success for a no-op.

    All three columns are cleared together, which the CHECK in 0088 requires
    anyway — a reason without an author is a state the schema refuses.
    """
    return (
        supabase.table("lead_delivery")
        .update(
            {"credit_reason": None, "credited_by": None, "credited_at": None}
        )
        .eq("lead_id", lead_id)
        .not_.is_("credit_reason", "null")
        .execute()
    )
