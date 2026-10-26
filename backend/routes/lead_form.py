from uuid import UUID

from fastapi import APIRouter, Depends

from auth_dependencies import require_admin
from schemas.lead_form import CreateLeadFormRequest, ValidateFormRequest
from services import lead_form as service

router = APIRouter(prefix="/lead-forms", tags=["lead_form"], dependencies=[Depends(require_admin)])


@router.get("")
def list_lead_forms():
    """List all form-to-group routing mappings, oldest first, with the
    broker_group's community label joined in."""
    return service.list_lead_forms()


@router.post("/validate")
def validate_form(request: ValidateFormRequest):
    """Confirm a form_id is real and accessible via Meta's Graph API before
    mapping it. Returns the form's real name/status on success. 422 if Meta
    says it's invalid/inaccessible, 502 if Meta couldn't be reached."""
    return service.validate_form(request)


@router.post("")
def create_lead_form(request: CreateLeadFormRequest):
    """Map a real Meta/TikTok form_id to the broker group its leads should
    route to. Re-validates the form_id against Meta first (422 if invalid,
    502 if Meta unreachable) — this can't be bypassed by calling the API
    directly. 409 if that form_id is already mapped, 400 if the group
    doesn't exist."""
    return service.create_lead_form(request)


@router.delete("/{lead_form_id}")
def delete_lead_form(lead_form_id: UUID):
    """Remove a form-to-group mapping. 404 if not found."""
    return service.delete_lead_form(lead_form_id)
