"""HTTP layer for the contact timeline.

Gated by get_current_broker: any authenticated broker may read or add to a
lead's timeline.

DELIBERATELY no lead-ownership guard, matching routes/driver.py. Brokers cover
for each other and leads move between them mid-conversation; a guard here would
block a colleague picking up a call rather than protect anything, since every
caller is already a named, authenticated broker of this brokerage. The row
records WHO logged it, which is the accountability that matters.
"""

from uuid import UUID

from fastapi import APIRouter, Depends

from auth_dependencies import get_current_broker
from schemas.contact_log import (
    ContactResponse,
    LogContactRequest,
    UpdateContactNotesRequest,
)
from services import contact_log as service

router = APIRouter(tags=["contact"], dependencies=[Depends(get_current_broker)])


@router.get("/leads/{lead_id}/contacts", response_model=list[ContactResponse])
def list_contacts(lead_id: UUID):
    """The lead's whole contact history, newest first.

    Empty is a normal answer for a lead nobody has rung yet.
    """
    return service.list_for_lead(str(lead_id))


@router.post("/leads/{lead_id}/contacts", response_model=ContactResponse, status_code=201)
def log_contact(
    lead_id: UUID,
    request: LogContactRequest,
    broker: dict = Depends(get_current_broker),
):
    """Record a contact the broker made themselves — WhatsApp, their own
    mobile, in person.

    broker_id is taken from the session, never the body — the person logging a
    contact is the person authenticated, and accepting it as input would let
    one broker file a call under another's name.
    """
    return service.log_contact(
        lead_id=str(lead_id),
        broker_id=broker["id"],
        outcome=request.outcome,
        notes=request.notes,
        occurred_at=request.occurred_at.isoformat() if request.occurred_at else None,
    )


@router.patch("/contacts/{contact_id}/notes", response_model=ContactResponse)
def update_contact_notes(contact_id: UUID, request: UpdateContactNotesRequest):
    """Add or edit the broker's note on a contact.

    Notes only. Outcome, duration and time are what happened; stage_at_contact
    is history. None of them are opinions to be revised later.
    """
    return service.set_notes(str(contact_id), request.notes)
