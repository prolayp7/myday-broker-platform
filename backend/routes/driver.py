"""HTTP layer for the driver domain.

Gated by get_current_broker: any authenticated broker may read and edit a
driver.

DELIBERATELY no lead-ownership guard. Brokers cover for each other and leads
move between them mid-conversation; a guard here would block a colleague
picking up a call rather than protect anything, since every caller is already
a named, authenticated broker of this brokerage.
"""

from uuid import UUID

from fastapi import APIRouter, Depends

from auth_dependencies import get_current_broker
from schemas.driver import AddDriverRequest, DriverResponse, UpdateDriverRequest
from services import driver as service

router = APIRouter(tags=["driver"], dependencies=[Depends(get_current_broker)])


@router.get("/leads/{lead_id}/driver", response_model=DriverResponse)
def get_principal_driver(lead_id: UUID):
    """The lead's applicant driver, CREATED BLANK if it does not exist yet.

    A GET that can write is unusual, and deliberate: the applicant row is not
    something a broker decides to create, it is a fact of working the lead (see
    migrations/0035_driver.sql). Making it a POST would put a "create driver?"
    step in front of every lead — a question with exactly one answer. Idempotent
    either way: calling it twice returns the same row, never a second one.
    """
    return service.get_or_create_principal(str(lead_id))


@router.get("/leads/{lead_id}/drivers", response_model=list[DriverResponse])
def list_drivers(lead_id: UUID):
    """Every driver on the lead, applicant first — one per tab on the broker's
    screen. Creates the applicant if this lead has never been opened, for the
    same reason the single-driver read does: every lead has one by definition,
    so returning an empty list would be describing a state that cannot exist.
    """
    return service.list_for_lead(str(lead_id))


@router.post("/leads/{lead_id}/drivers", response_model=DriverResponse, status_code=201)
def add_driver(lead_id: UUID, request: AddDriverRequest):
    """Add an additional driver — a spouse, a child. Relationship is all that
    is asked; everything else is filled in on New Policy once the row exists.

    Not idempotent, deliberately: two children on one lead are two real people,
    so a repeated call creates a second row rather than returning the first.
    """
    return service.add_driver(str(lead_id), request.relationship_to_applicant)


@router.delete("/drivers/{driver_id}", status_code=204)
def delete_driver(driver_id: UUID):
    """Remove an additional driver.

    400 for the applicant: a lead without its subject is not a lead.
    """
    service.delete_driver(str(driver_id))


@router.patch("/drivers/{driver_id}", response_model=DriverResponse)
def update_driver(driver_id: UUID, request: UpdateDriverRequest):
    """Patch a driver with only the fields sent."""
    return service.update_driver(str(driver_id), request)
