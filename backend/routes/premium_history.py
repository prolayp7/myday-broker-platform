"""HTTP layer for premiums.

Gated by get_current_broker: recording what a policy is worth is the broker's
own work on their own lead, not an admin action.

DELIBERATELY no lead-ownership guard, matching routes/driver.py and
routes/contact_log.py. Brokers cover for each other and leads move between
them mid-conversation; a guard here would block a colleague picking up a call
rather than protect anything, since every caller is already a named,
authenticated broker of this brokerage.
"""

from uuid import UUID

from fastapi import APIRouter, Depends

from auth_dependencies import get_current_broker
from schemas.premium_history import PremiumResponse, SavePremiumRequest
from services import premium_history as service

router = APIRouter(tags=["premium"], dependencies=[Depends(get_current_broker)])


@router.get("/leads/{lead_id}/premium", response_model=PremiumResponse | None)
def get_premium(lead_id: UUID):
    """This lead's premium figures, or null if none have been entered.

    Null rather than 404: most leads have no premium yet, and that is an
    ordinary state the screen renders as empty inputs — not an error.
    """
    return service.get_for_lead(str(lead_id))


@router.put("/leads/{lead_id}/premium", response_model=PremiumResponse)
def save_premium(lead_id: UUID, request: SavePremiumRequest):
    """Save what the broker has typed so far.

    PUT and an upsert, not POST: one premium row per lead, and a broker
    filling these figures in across several calls will save repeatedly.
    Omitted fields are left alone, so a partial save cannot wipe what was
    entered earlier.

    Returns the row including every GENERATED column — the annual figures,
    the totals and the savings — so the screen shows Postgres's own
    arithmetic rather than recomputing it and risking a different answer.
    """
    return service.save_for_lead(str(lead_id), request)
