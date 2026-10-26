from uuid import UUID

from fastapi import APIRouter, Depends

from auth_dependencies import require_admin
from schemas.community import (
    CreateCommunityRequest,
    RenameCommunityRequest,
    SetCommunityCountriesRequest,
)
from services import community as service

router = APIRouter(prefix="/communities", tags=["community"], dependencies=[Depends(require_admin)])


@router.get("")
def list_communities():
    """List all communities, alphabetically by name, with their mapped countries."""
    return service.list_communities()


@router.post("")
def create_community(request: CreateCommunityRequest):
    """Create a community, optionally mapping it to one or more countries.
    Fails with 409 if the name is already taken."""
    return service.create_community(request)


@router.patch("/{community_id}")
def rename_community(community_id: UUID, request: RenameCommunityRequest):
    """Rename a community. Fails with 409 if the new name is already taken."""
    return service.rename_community(community_id, request)


@router.delete("/{community_id}")
def delete_community(community_id: UUID):
    """Delete a community. Fails with 409 if any broker group still references it."""
    return service.delete_community(community_id)


@router.put("/{community_id}/countries")
def set_community_countries(community_id: UUID, request: SetCommunityCountriesRequest):
    """Replace the full set of countries this community maps to."""
    return service.set_community_countries(community_id, request)
