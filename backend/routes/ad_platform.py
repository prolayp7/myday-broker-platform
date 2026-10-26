from fastapi import APIRouter, Depends

from auth_dependencies import require_admin
from services import ad_platform as service

router = APIRouter(prefix="/ad-platforms", tags=["ad_platform"], dependencies=[Depends(require_admin)])


@router.get("")
def list_ad_platforms():
    """List all ad platforms (meta, tiktok, website, ...), alphabetically."""
    return service.list_ad_platforms()
