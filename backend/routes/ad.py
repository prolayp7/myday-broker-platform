from uuid import UUID

from fastapi import APIRouter, Depends

from auth_dependencies import require_admin
from schemas.ad import CreateAdRequest
from services import ad as service

router = APIRouter(prefix="/ads", tags=["ad"], dependencies=[Depends(require_admin)])


@router.get("")
def list_ads():
    """List all ads, oldest first, with ad_group.name joined in (may be null)."""
    return service.list_ads()


@router.post("")
def create_ad(request: CreateAdRequest):
    """Create an ad. ad_group_id is optional (matches the real Meta webhook,
    which doesn't guarantee full context at ingestion time)."""
    return service.create_ad(request)


@router.delete("/{ad_id}")
def delete_ad(ad_id: UUID):
    """Delete an ad. 404 if not found."""
    return service.delete_ad(ad_id)
