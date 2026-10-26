from uuid import UUID

from fastapi import APIRouter, Depends

from auth_dependencies import require_admin
from schemas.campaign import CreateCampaignRequest
from services import campaign as service

router = APIRouter(prefix="/campaigns", tags=["campaign"], dependencies=[Depends(require_admin)])


@router.get("")
def list_campaigns():
    """List all campaigns, oldest first, with ad_platform name joined in.
    Reporting/attribution only — campaigns no longer carry routing; see
    lead_form for the actual form_id -> broker_group mapping."""
    return service.list_campaigns()


@router.post("")
def create_campaign(request: CreateCampaignRequest):
    """Create a campaign. 400 if source_id doesn't exist."""
    return service.create_campaign(request)


@router.delete("/{campaign_id}")
def delete_campaign(campaign_id: UUID):
    """Delete a campaign. 404 if not found."""
    return service.delete_campaign(campaign_id)
