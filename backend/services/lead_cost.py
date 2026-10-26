"""What each broker owes for the leads the system gave them.

THE RULE, in one sentence:

    While the billing month is open, the cost follows the lead. Once it
    freezes, the cost stays with whoever held it at that moment.

August is open 1 August - 5 September and frozen from 6 September, 00:00
Ontario. One window governs both reassignment and credits — there is not a
separate deadline to remember for each.

WHO PAYS IS A QUESTION, NOT A COLUMN:

    month open   -> lead.assigned_broker_id
    month frozen -> the last lead_assignment row before the freeze instant

lead_assignment is append-only (0023), so the frozen answer is derivable and
stays true however often the lead moves afterwards. A billed_broker_id column
would have to be maintained by every reassignment path forever, to store
something the database already knows.
"""

import logging
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from repositories import broker as broker_repo
from repositories import lead_cost as repo
from repositories import lead_cost_settings as settings_repo
from schemas.lead_cost import UpdateLeadCostSettingsRequest

logger = logging.getLogger("myday.lead_cost")

# The brokerage's own clock. Same zone services/broker.py and services/lead.py
# already anchor to — a month boundary read in UTC would put a lead delivered
# at 8pm Ontario on the 31st into the following month's bill.
_ONTARIO = ZoneInfo("America/Toronto")

# Adjustments close at the start of this day of the following month. The month
# is charged 1st-31st and corrected in the first five days after it.
_FREEZE_DAY = 6


def parse_month(month: str) -> tuple[int, int]:
    """'2026-08' -> (2026, 8), or a 422 the caller can act on."""
    try:
        year, mon = month.split("-")
        year_i, mon_i = int(year), int(mon)
        if not 1 <= mon_i <= 12 or not 2000 <= year_i <= 2999:
            raise ValueError
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=422, detail="Month must look like 2026-08"
        )
    return year_i, mon_i


def month_bounds(month: str) -> tuple[str, str]:
    """The UTC instants a billing month starts and ends at.

    TIMEZONE-AWARE DATETIMES, NEVER BARE DATES. lead_delivery.delivered_at is a
    timestamptz; a plain date handed to PostgREST is compared at a UTC
    boundary, and a lead delivered 31 Aug 23:00 Ontario is 1 Sep in UTC — billed
    to the wrong month. This is the one place in the feature where a timezone
    slip costs money.

    Returned as ISO strings because that is what the repository sends onward.
    """
    year, mon = parse_month(month)
    start_local = datetime(year, mon, 1, tzinfo=_ONTARIO)
    end_local = datetime(
        year + (mon == 12), 1 if mon == 12 else mon + 1, 1, tzinfo=_ONTARIO
    )
    return (
        start_local.astimezone(timezone.utc).isoformat(),
        end_local.astimezone(timezone.utc).isoformat(),
    )


def freeze_instant(month: str) -> datetime:
    """When this month stops being editable: 00:00 Ontario on the 6th of the
    following month, as a UTC instant."""
    year, mon = parse_month(month)
    next_year = year + (mon == 12)
    next_mon = 1 if mon == 12 else mon + 1
    # Guarded for completeness rather than necessity — every month has a 6th.
    day = min(_FREEZE_DAY, monthrange(next_year, next_mon)[1])
    return datetime(next_year, next_mon, day, tzinfo=_ONTARIO).astimezone(
        timezone.utc
    )


def is_open(month: str) -> bool:
    """Whether this month still accepts credits and still moves with the lead."""
    return datetime.now(timezone.utc) < freeze_instant(month)


def is_current_month(month: str) -> bool:
    """Whether this is the month happening now, in Ontario."""
    year, mon = parse_month(month)
    today = datetime.now(_ONTARIO).date()
    return (year, mon) == (today.year, today.month)


# ── The price ───────────────────────────────────────────────────────────


def get_settings() -> dict:
    """The current price per lead.

    500 rather than a default when the row is missing: the migration seeds it
    and a CHECK stops a second one existing, so an empty table means the
    migration did not run. Inventing a price would bill somebody a number
    nobody chose.
    """
    rows = settings_repo.get().data or []
    if not rows:
        logger.error("lead_cost_settings is empty — migration 0088 has not run")
        raise HTTPException(status_code=500, detail="No lead cost configured")
    return rows[0]


def update_settings(request: UpdateLeadCostSettingsRequest) -> dict:
    """Change the price. Future deliveries only — see the repository."""
    rows = settings_repo.update(
        {"cost_per_lead": str(request.cost_per_lead)}
    ).data or []
    if not rows:
        raise HTTPException(status_code=500, detail="Could not save the price")
    return rows[0]


# ── Who pays ────────────────────────────────────────────────────────────


def _charged_broker_ids(
    deliveries: list[dict], leads_by_id: dict[str, dict], month: str
) -> dict[str, str | None]:
    """lead_id -> the broker charged for it, by the rule at the top.

    An OPEN month reads the lead's current holder, so the charge follows a
    reassignment without anything being written.

    A FROZEN month folds lead_assignment: the rows arrive oldest-first and
    already filtered to before the freeze, so the last one seen per lead is the
    holder at that instant. PostgREST has no DISTINCT ON, which is why this is
    a fold here rather than a query.
    """
    if is_open(month):
        return {
            d["lead_id"]: (leads_by_id.get(d["lead_id"]) or {}).get(
                "assigned_broker_id"
            )
            for d in deliveries
        }

    lead_ids = [d["lead_id"] for d in deliveries]
    rows = repo.custody_before(lead_ids, freeze_instant(month).isoformat())

    holder: dict[str, str | None] = {}
    for row in rows:
        # Ordered by assigned_at ascending, so each write overwrites an older
        # one and the survivor is the latest before the freeze.
        holder[row["lead_id"]] = row["broker_id"]

    # A delivery with no custody row before the freeze falls back to the broker
    # the system delivered it to. That row is written in the same transaction
    # as the delivery (assign_lead), so this is a belt-and-braces default
    # rather than an expected path.
    for d in deliveries:
        holder.setdefault(d["lead_id"], d["broker_id"])
    return holder


def _money(value) -> Decimal:
    """A stored numeric as a Decimal. None means UNPRICED, not free — the
    caller counts those separately rather than summing them as zero."""
    return Decimal(str(value)) if value is not None else Decimal(0)


def _gather(month: str) -> tuple[list[dict], dict[str, dict], dict[str, str | None]]:
    """One month's deliveries, their leads, and who is charged for each."""
    start_utc, end_utc = month_bounds(month)
    deliveries = repo.deliveries_between(start_utc, end_utc)

    lead_ids = [d["lead_id"] for d in deliveries]
    leads_by_id = {row["id"]: row for row in repo.leads_by_ids(lead_ids)}
    charged = _charged_broker_ids(deliveries, leads_by_id, month)
    return deliveries, leads_by_id, charged


# ── The reports ─────────────────────────────────────────────────────────


def group_summary(group_id: str, month: str) -> dict:
    """Every broker in a group, and what they owe for the month.

    FILTERED ON lead.group_id — the group the lead came in for, which never
    moves. A lead reassigned to a broker in another group still reports under
    the group that bought it; reading the broker's current group would shift
    historical spend whenever somebody changed team.
    """
    deliveries, leads_by_id, charged = _gather(month)

    per_broker: dict[str, dict] = {}
    unpriced = 0

    for d in deliveries:
        lead = leads_by_id.get(d["lead_id"])
        if not lead or str(lead.get("group_id")) != str(group_id):
            continue

        broker_id = charged.get(d["lead_id"])
        if not broker_id:
            continue

        # UNPRICED IS NOT FREE. A null cost means the settings row was missing
        # when the lead was delivered — a configuration failure. Counted, never
        # summed as zero, so a broken price surfaces instead of quietly
        # under-billing.
        if d["cost_at_delivery"] is None:
            unpriced += 1

        row = per_broker.setdefault(
            broker_id, {"leads": 0, "allocated": Decimal(0), "credited": Decimal(0)}
        )
        cost = _money(d["cost_at_delivery"])
        row["leads"] += 1
        row["allocated"] += cost
        if d["credit_reason"]:
            row["credited"] += cost

    names = {
        b["id"]: b["name"]
        for b in (broker_repo.list_by_group(group_id).data or [])
    }

    brokers = [
        {
            "broker_id": bid,
            "name": names.get(bid, "Unknown"),
            "leads": v["leads"],
            "allocated": str(v["allocated"]),
            "credited": str(v["credited"]),
            "charged": str(v["allocated"] - v["credited"]),
        }
        for bid, v in per_broker.items()
    ]
    brokers.sort(key=lambda b: b["name"])

    total_allocated = sum((Decimal(b["allocated"]) for b in brokers), Decimal(0))
    total_credited = sum((Decimal(b["credited"]) for b in brokers), Decimal(0))

    return {
        "month": month,
        "is_open": is_open(month),
        "unpriced_count": unpriced,
        "brokers": brokers,
        "total_allocated": str(total_allocated),
        "total_credited": str(total_credited),
        "total_charged": str(total_allocated - total_credited),
    }


def _broker_since_month(broker_id: str) -> str:
    """The month a broker joined, as YYYY-MM in ONTARIO time.

    A MONTH, NOT A TIMESTAMP, and converted here rather than in the browser: a
    broker created 31 Jul 21:00 Ontario is 1 Aug in UTC, so a client parsing
    the raw timestamp would place them in the wrong join month and hide a month
    they actually worked. Same class of slip month_bounds exists to prevent on
    the other side of the feature.

    404 RATHER THAN AN INDEX ERROR. broker_ledger never looked the broker up
    before, so an unknown id simply produced an empty ledger; reading
    created_at changes that, and `[0]` on an empty result would turn a mistyped
    admin URL into a 500.
    """
    rows = broker_repo.get_safe(broker_id).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Broker not found")

    created = datetime.fromisoformat(rows[0]["created_at"].replace("Z", "+00:00"))
    local = created.astimezone(_ONTARIO)
    return f"{local.year}-{local.month:02d}"


def broker_ledger(broker_id: str, month: str) -> dict:
    """One broker's month, line by line."""
    deliveries, leads_by_id, charged = _gather(month)

    lines = []
    allocated = credited = Decimal(0)
    unpriced = 0

    for d in deliveries:
        if str(charged.get(d["lead_id"])) != str(broker_id):
            continue

        lead = leads_by_id.get(d["lead_id"]) or {}
        cost = _money(d["cost_at_delivery"])
        if d["cost_at_delivery"] is None:
            unpriced += 1

        allocated += cost
        if d["credit_reason"]:
            credited += cost

        lines.append(
            {
                "lead_id": d["lead_id"],
                "name": lead.get("name"),
                "phone": lead.get("phone"),
                "delivered_at": d["delivered_at"],
                "cost": str(cost),
                "unpriced": d["cost_at_delivery"] is None,
                "credit_reason": d["credit_reason"],
            }
        )

    return {
        "month": month,
        "is_open": is_open(month),
        "broker_id": str(broker_id),
        "unpriced_count": unpriced,
        # The month list on the screen stops here — a broker is not offered
        # months that ended before they existed, each of which is $0 forever.
        "broker_since_month": _broker_since_month(broker_id),
        "lines": lines,
        "allocated": str(allocated),
        "credited": str(credited),
        "charged": str(allocated - credited),
    }


def my_ledger(broker_id: str, month: str) -> dict:
    """A broker's own statement, open months included.

    OPEN MONTHS ARE ANSWERED, not refused. This used to 404 anything still
    being adjusted, on the reasoning that a provisional figure is the one a
    broker remembers and argues about. The cost of that was worse: a broker
    given ten leads today learned what they owed after the 5th of NEXT month,
    which is most of a billing cycle spent in the dark.

    Nothing here is provisional by accident — cost_at_delivery is stamped on
    the delivery row as it happens (0088), so an open month's lines are exactly
    as real as a closed one's. What can still change is which of them carry a
    credit, and the response says so: `is_open` travels with the figures, and
    the screen uses it to withhold the word "final" and the PDF rather than
    withholding the number.
    """
    return broker_ledger(broker_id, month)


# ── Credits ─────────────────────────────────────────────────────────────


def _month_of(delivered_at: str) -> str:
    """The billing month a delivery falls in, in Ontario."""
    when = datetime.fromisoformat(delivered_at.replace("Z", "+00:00"))
    local = when.astimezone(_ONTARIO)
    return f"{local.year:04d}-{local.month:02d}"


def add_credit(lead_id: str, reason: str, admin_id: str) -> dict:
    """Take a lead off the bill.

    THE MONTH COMES FROM THE DELIVERY, never from today. A credit entered on
    4 September belongs to August — using the moment of entry would file it
    under the wrong month and leave August still charged.
    """
    rows = repo.get_delivery_for_lead(lead_id).data or []
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="That lead was not delivered by the system, so it is not charged",
        )
    delivery = rows[0]

    month = _month_of(delivery["delivered_at"])
    if not is_open(month):
        raise HTTPException(
            status_code=409,
            detail=f"{month} is closed — its figures cannot be changed",
        )

    updated = repo.add_credit(
        lead_id, reason, admin_id, datetime.now(timezone.utc).isoformat()
    ).data or []
    if not updated:
        # The conditional update matched nothing, so it was already credited —
        # possibly by another admin a moment ago.
        raise HTTPException(
            status_code=409, detail="That lead is already credited"
        )
    return updated[0]


def remove_credit(lead_id: str) -> None:
    """Put a credited lead back on the bill."""
    rows = repo.get_delivery_for_lead(lead_id).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="No delivery for that lead")

    month = _month_of(rows[0]["delivered_at"])
    if not is_open(month):
        raise HTTPException(
            status_code=409,
            detail=f"{month} is closed — its figures cannot be changed",
        )

    updated = repo.remove_credit(lead_id).data or []
    if not updated:
        raise HTTPException(status_code=409, detail="That lead is not credited")
