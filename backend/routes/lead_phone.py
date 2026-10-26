"""A lead's phone numbers.

Gated by get_current_broker like every other lead-scoped route.
"""

from uuid import UUID

from fastapi import APIRouter, Depends

from auth_dependencies import get_current_broker
from schemas.lead_phone import LeadPhoneResponse
from services import lead_phone as service

router = APIRouter(tags=["lead-phone"], dependencies=[Depends(get_current_broker)])


@router.get("/leads/{lead_id}/phones", response_model=list[LeadPhoneResponse])
def list_phones(lead_id: UUID):
    """Every number this lead can be reached on, deduplicated.

    The lead's own, its drivers', and any secondary numbers learned from
    answered calls — merged HERE rather than on the client, because no screen
    holds all three and two screens merging separately would eventually
    disagree.
    """
    return service.list_for_lead(str(lead_id))


@router.delete("/leads/{lead_id}/phones/{phone_id}", status_code=204)
def delete_phone(lead_id: UUID, phone_id: UUID):
    """Forget a secondary number.

    Scoped by BOTH ids — see repositories/lead_phone.py::delete.
    """
    service.delete(str(lead_id), str(phone_id))
