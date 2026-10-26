"""What brokers owe for the leads the system gave them.

TWO ROUTERS IN ONE MODULE, mounted separately — the routes/lead.py:16,23
idiom. The admin router is gated at the router level so no endpoint on it can
be reached without require_admin; the broker router is a distinct object so
that gate cannot apply to it, because a broker must be able to read their own
statement.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from auth_dependencies import get_current_broker, require_admin
from schemas.lead_cost import (
    AddCreditRequest,
    GroupCostResponse,
    LeadCostSettingsResponse,
    LedgerResponse,
    UpdateLeadCostSettingsRequest,
)
from services import lead_cost as service

router = APIRouter(tags=["lead-cost"], dependencies=[Depends(require_admin)])

broker_router = APIRouter(tags=["lead-cost"])


@router.get("/lead-cost-settings", response_model=LeadCostSettingsResponse)
def get_settings():
    """The price every system-delivered lead is stamped with."""
    return service.get_settings()


@router.put("/lead-cost-settings", response_model=LeadCostSettingsResponse)
def update_settings(request: UpdateLeadCostSettingsRequest):
    """Change the price.

    FUTURE DELIVERIES ONLY. The price is copied onto each delivery as it
    happens, so this cannot reach back into a month already billed.
    """
    return service.update_settings(request)


@router.get("/lead-cost/groups/{group_id}", response_model=GroupCostResponse)
def group_summary(group_id: UUID, month: str = Query(pattern=r"^\d{4}-\d{2}$")):
    """Every broker in a group and what they owe for the month."""
    return service.group_summary(str(group_id), month)


@router.get("/lead-cost/brokers/{broker_id}", response_model=LedgerResponse)
def broker_ledger(broker_id: UUID, month: str = Query(pattern=r"^\d{4}-\d{2}$")):
    """One broker's month, line by line, with credits shown against the leads
    they belong to."""
    return service.broker_ledger(str(broker_id), month)


@router.post("/lead-cost/credits", status_code=201)
def add_credit(request: AddCreditRequest, admin: dict = Depends(require_admin)):
    """Take a lead off the bill.

    409 when the lead's month is closed, and 409 when it is already credited —
    the second decided by a conditional update rather than a read-then-write,
    so two admins cannot both succeed.

    admin id comes from the session, never the body: this records who reduced
    somebody's charge.
    """
    return service.add_credit(request.lead_id, request.reason, admin["id"])


@router.delete("/lead-cost/credits/{lead_id}", status_code=204)
def remove_credit(lead_id: UUID):
    """Put a credited lead back on the bill. 409 if the month is closed, or if
    it was not credited in the first place."""
    service.remove_credit(str(lead_id))


@broker_router.get("/my-lead-cost", response_model=LedgerResponse)
def my_lead_cost(
    month: str = Query(pattern=r"^\d{4}-\d{2}$"),
    broker: dict = Depends(get_current_broker),
):
    """The signed-in broker's own statement.

    TAKES NO BROKER ID, exactly like /my-day and /my-profile: it always
    describes the caller, so there is no id to pass and no way to ask for
    somebody else's bill.

    ANY month, open or closed. The response carries `is_open` so the screen
    can withhold the word "final" on a month whose credits are still moving,
    rather than withholding the figure itself.
    """
    return service.my_ledger(broker["id"], month)
