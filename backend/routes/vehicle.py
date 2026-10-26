"""HTTP layer for the vehicle domain — the cars actually being quoted.

Gated by get_current_broker: any authenticated broker may read and edit a
lead's vehicles.

DELIBERATELY no lead-ownership guard, matching routes/driver.py. Brokers cover
for each other and leads move between them mid-conversation; a guard here
would block a colleague picking up a call rather than protect anything, since
every caller is already a named, authenticated broker of this brokerage.

Nested under /drivers rather than /leads because that is what the row actually
hangs off: vehicles belong to the primary applicant, not to the file.
"""

from uuid import UUID

from fastapi import APIRouter, Depends

from auth_dependencies import get_current_broker
from schemas.vehicle import VehiclePayload, VehicleResponse
from services import vehicle as service

router = APIRouter(tags=["vehicle"], dependencies=[Depends(get_current_broker)])


@router.get("/drivers/{driver_id}/vehicles", response_model=list[VehicleResponse])
def list_vehicles(driver_id: UUID):
    """This driver's vehicles, oldest first.

    Works for any driver, not just the applicant. Only the applicant has cars
    in practice, so a spouse simply returns an empty list — a rule the route
    does not need to enforce, and would only have to be kept in step with if
    it did.
    """
    return service.list_for_driver(str(driver_id))


@router.post("/drivers/{driver_id}/vehicles", response_model=VehicleResponse, status_code=201)
def add_vehicle(driver_id: UUID, payload: VehiclePayload):
    """Add a car to the quote. Every field is optional: the row is created
    first so the form has somewhere to save to, then filled in as the customer
    answers."""
    return service.add_vehicle(str(driver_id), payload)


@router.patch("/vehicles/{vehicle_id}", response_model=VehicleResponse)
def update_vehicle(vehicle_id: UUID, payload: VehiclePayload):
    """Patch a vehicle with only the fields sent. Omitting a field leaves it
    alone; sending an explicit null clears it — a distinction that matters on
    a form filled in one answer at a time."""
    return service.update_vehicle(str(vehicle_id), payload)


@router.delete("/vehicles/{vehicle_id}", status_code=204)
def delete_vehicle(vehicle_id: UUID):
    """Remove a car from the quote — sold, or never theirs."""
    service.delete_vehicle(str(vehicle_id))
