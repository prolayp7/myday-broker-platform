from uuid import UUID

from fastapi import APIRouter, Depends

from auth_dependencies import require_admin
from schemas.broker_group import CreateBrokerGroupRequest, RenameBrokerGroupRequest
from services import broker_group as service
from services import lead as lead_service

router = APIRouter(prefix="/broker-groups", tags=["broker_group"], dependencies=[Depends(require_admin)])


@router.post("")
def create_broker_group(request: CreateBrokerGroupRequest):
    """Create a broker group under the given community. group_number is auto-assigned."""
    return service.create_broker_group(request)


@router.get("")
def list_broker_groups():
    """List all broker groups with their community name attached, oldest first."""
    return service.list_broker_groups()


@router.patch("/{group_id}")
def rename_broker_group(group_id: UUID, request: RenameBrokerGroupRequest):
    """Rename a broker group. 404 if it doesn't exist."""
    return service.rename_broker_group(group_id, request)


@router.delete("/{group_id}")
def delete_broker_group(group_id: UUID):
    """Delete a broker group. Fails with 409 if brokers/campaigns/leads still reference it."""
    return service.delete_broker_group(group_id)


@router.get("/{group_id}/capacity")
def get_group_capacity(group_id: UUID):
    """Live allocation capacity for one group: leads_today, unassigned, and
    per-broker delivered-today counts (all anchored to today's reset). Powers
    the group capacity view and the reassign dialog's load bars. Read-only."""
    return lead_service.get_group_capacity(group_id)
