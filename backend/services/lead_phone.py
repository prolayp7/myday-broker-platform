"""Every number a lead can be reached on, as one list.

WHY THE BACKEND MERGES THIS. A customer's numbers live in three tables — the
lead, its drivers, and lead_phone — and the screens that show them hold only
one of the three. ContactHistoryPage receives `leadPhone` and nothing else;
mobile has `lead.phone`. Neither has the drivers, so neither could build this
list even if asked to.

Merging here also means desktop and mobile cannot disagree about what numbers a
customer has, which they would the moment either one grew its own rule.
"""

import logging

from fastapi import HTTPException

from db import supabase
from repositories import lead as lead_repo
from repositories import lead_phone as repo
from repositories import phone_match

logger = logging.getLogger("myday.lead_phone")

# Which source wins when the same number appears in more than one table.
#
# THIS IS THE NORMAL CASE, not an edge one: driver.phone is SEEDED from
# lead.phone when the applicant row is created (0058), so almost every lead has
# the same number twice before anyone touches anything.
_PRECEDENCE = ("lead", "driver", "secondary")


def list_for_lead(lead_id: str) -> list[dict]:
    """The lead's numbers, deduplicated, highest-precedence source winning.

    A number held by the lead or a driver is NOT DELETABLE even if a lead_phone
    row also exists for it: deleting that row would not stop the number
    matching, because the lead still holds it. A delete control that changes
    nothing is worse than no delete control.
    """
    if not lead_repo.get_by_id(lead_id).data:
        raise HTTPException(status_code=404, detail="Lead not found")

    found: dict[str, dict] = {}

    def remember(phone: str | None, e164: str | None, source: str, phone_id: str | None):
        # No E.164 means the number could never be matched against a call —
        # a placeholder, an extension, an international number. Shown, because
        # the broker still wants to see what the ad captured, but keyed by its
        # raw form so two unparseable numbers do not collapse into one.
        key = e164 or f"raw:{phone}"
        if not phone or key in found:
            return
        found[key] = {
            "phone": phone,
            "phone_e164": e164,
            "source": source,
            "phone_id": phone_id,
            "deletable": source == "secondary",
        }

    lead = lead_repo.get_by_id(lead_id).data[0]
    remember(lead.get("phone"), lead.get("phone_e164"), "lead", None)

    # Direct query rather than repositories/driver.py: its shared select string
    # carries phone but not phone_e164, and widening it would change every
    # other driver read to serve this one screen.
    drivers = (
        supabase.table("driver")
        .select("phone, phone_e164")
        .eq("lead_id", lead_id)
        .execute()
    ).data or []
    for driver in drivers:
        remember(driver.get("phone"), driver.get("phone_e164"), "driver", None)

    for row in repo.list_for_lead(lead_id).data or []:
        # lead_phone stores only the normalised form — nobody displays it
        # anywhere else, so there is no raw version to preserve (0063).
        remember(row["phone_e164"], row["phone_e164"], "secondary", row["id"])

    return sorted(found.values(), key=lambda p: _PRECEDENCE.index(p["source"]))


def delete(lead_id: str, phone_id: str) -> None:
    """Remove one secondary number from this lead."""
    rows = repo.delete(lead_id, phone_id).data
    if not rows:
        # Either it never existed or it belongs to another lead. The same
        # answer for both: naming the difference would confirm the existence of
        # a row on somebody else's customer.
        raise HTTPException(status_code=404, detail="Number not found on this lead")


def normalize_or_422(raw: str) -> str:
    """A dialable E.164 number, or a refusal the broker can act on.

    normalize_phone_e164 returns null for an extension, an international number
    or junk (0058). lead_phone.phone_e164 is NOT NULL, so without this the
    broker would get a raw database error for what is really "that is not a
    number I can dial" — and a number that will not normalise could never be
    matched against a call even if it were somehow stored.
    """
    normalized = phone_match.normalize(raw)
    if not normalized:
        raise HTTPException(
            status_code=422,
            detail="That is not a Canadian or US phone number",
        )
    return normalized
