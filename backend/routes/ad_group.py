from uuid import UUID

from fastapi import APIRouter, Depends

from auth_dependencies import require_admin
from schemas.ad_group import CreateAdGroupRequest
from services import ad_group as service

router = APIRouter(prefix="/ad-groups", tags=["ad_group"], dependencies=[Depends(require_admin)])


@router.get("")
def list_ad_groups():
    """List all ad groups, oldest first, with campaign.name joined in."""
    return service.list_ad_groups()


@router.post("")
def create_ad_group(request: CreateAdGroupRequest):
    """Create an ad group under a campaign. 400 if the campaign doesn't exist,
    409 if platform_ad_group_id is already used by another row."""
    return service.create_ad_group(request)


@router.delete("/{ad_group_id}")
def delete_ad_group(ad_group_id: UUID):
    """Delete an ad group. 404 if not found."""
    return service.delete_ad_group(ad_group_id)
