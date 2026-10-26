from fastapi import APIRouter, Depends

from auth_dependencies import require_admin
from services import country as service

router = APIRouter(prefix="/countries", tags=["country"], dependencies=[Depends(require_admin)])


@router.get("")
def list_countries():
    """List all countries, alphabetically by name. Fixed lookup list."""
    return service.list_countries()
