"""The Website leads admin screen: who receives website leads, and how many a
day.

Admin-only at the router level, the lead_cost.py idiom — no endpoint here can
be reached without require_admin.
"""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends

from auth_dependencies import require_admin
from schemas.website_lead import (
    UpdateWebsiteBrokerRequest,
    UpdateWebsiteBrokersRequest,
    WebsiteBrokerSettingResponse,
    WebsiteLeadBoardResponse,
)
from services import website_lead as service

router = APIRouter(tags=["website-leads"], dependencies=[Depends(require_admin)])


@router.get("/admin/website-leads", response_model=WebsiteLeadBoardResponse)
def get_board():
    """Every broker in every group with their website switch, limit and
    today's count, plus how many submissions are waiting or failed."""
    return service.get_board()


@router.patch("/admin/website-leads", response_model=list[WebsiteBrokerSettingResponse])
def update_brokers(request: UpdateWebsiteBrokersRequest, background_tasks: BackgroundTasks):
    """The same switch and/or limit for many brokers — all of them, or none.

    One drain afterwards, not one per broker: switching ten people on releases
    the waiting submissions once, in turn order.
    """
    rows = service.update_brokers(request)
    background_tasks.add_task(service.drain)
    return rows


@router.patch("/admin/website-leads/{broker_id}", response_model=WebsiteBrokerSettingResponse)
def update_broker(
    broker_id: UUID,
    request: UpdateWebsiteBrokerRequest,
    background_tasks: BackgroundTasks,
):
    """Switch a broker on or off, or change their website limit.

    The drain runs straight after, so switching someone on or raising a limit
    releases any waiting submissions now rather than at the next minute. After
    a switch-off it simply finds nothing new for that broker.
    """
    row = service.update_broker(broker_id, request)
    background_tasks.add_task(service.drain)
    return row
