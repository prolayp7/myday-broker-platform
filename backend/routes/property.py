"""HTTP layer for the property domain — the homes on a combined auto+property
quote.

Gated by get_current_broker: any authenticated broker may read and edit a
lead's properties.

DELIBERATELY no lead-ownership guard, matching routes/driver.py and
routes/vehicle.py. Brokers cover for each other and leads move between them
mid-conversation; a guard here would block a colleague picking up a call rather
than protect anything, since every caller is already a named, authenticated
broker of this brokerage.

Nested under /drivers rather than /leads because that is what the row actually
hangs off: the applicant owns these properties. The LEAD is whoever filled in
the ad — a son can enquire on behalf of his father — and the house belongs to
the father, who is the applicant.

There is no POST for the applicant's own home. That row exists because the
quote is combined, so it is created by PATCH /leads/{id}/policy-type rather
than by a broker deciding to add it.
"""

from uuid import UUID

from fastapi import APIRouter, Depends

from auth_dependencies import get_current_broker
from schemas.property import AddPropertyRequest, PropertyPayload, PropertyResponse
from services import property as service

router = APIRouter(tags=["property"], dependencies=[Depends(get_current_broker)])


@router.get("/drivers/{driver_id}/properties", response_model=list[PropertyResponse])
def list_properties(driver_id: UUID):
    """This applicant's properties, oldest first — the Primary, then any
    rentals in the order they were added.

    Empty for an auto-only quote, which is the ordinary case rather than an
    error: most policies are Standalone.
    """
    return service.list_for_driver(str(driver_id))


@router.post("/drivers/{driver_id}/properties", response_model=PropertyResponse, status_code=201)
def add_property(driver_id: UUID, request: AddPropertyRequest):
    """Add an investment property — a rental dwelling or a rented condo.

    400 when the applicant's policy is Tenant (a tenant policy covers one
    home), when no policy type has been chosen yet, or when the request asks
    for a Primary.
    """
    return service.add_property(str(driver_id), request)


@router.patch("/properties/{property_id}", response_model=PropertyResponse)
def update_property(property_id: UUID, payload: PropertyPayload):
    """Patch a property with only the fields sent. Omitting a field leaves it
    alone; sending an explicit null clears it — a distinction that matters on a
    form filled in one answer at a time."""
    return service.update_property(str(property_id), payload)


@router.delete("/properties/{property_id}", status_code=204)
def delete_property(property_id: UUID):
    """Remove an investment property — sold, or never theirs.

    400 for the applicant's own home: it goes by changing the policy type, not
    from here.
    """
    service.delete_property(str(property_id))
