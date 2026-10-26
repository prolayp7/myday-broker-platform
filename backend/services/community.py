"""Business logic for communities. Owns every HTTPException decision —
routes stay thin, repositories stay error-free. Translates raw DB errors
into meaningful HTTP responses via db_error."""

from uuid import UUID

from fastapi import HTTPException

from db_errors import db_error
from repositories import community as repo
from schemas.community import (
    CreateCommunityRequest,
    RenameCommunityRequest,
    SetCommunityCountriesRequest,
)


def list_communities():
    return repo.list_all().data


def create_community(request: CreateCommunityRequest):
    try:
        result = repo.insert(request.name)
    except Exception as err:
        # Unique violation here means the name (case-insensitive) is already taken.
        raise db_error(err, on_unique=HTTPException(409, "That community name already exists"))

    community = result.data[0]
    if request.country_ids:
        # Country linking is a separate call from the insert (no single
        # transaction across PostgREST requests). If it fails, the community
        # already exists — don't pretend the whole create succeeded. Surface
        # a clear error so the caller knows to retry the country mapping
        # (which they can do via PUT /communities/{id}/countries).
        try:
            repo.set_countries(community["id"], [str(cid) for cid in request.country_ids])
        except Exception as err:
            raise db_error(
                err,
                on_foreign_key=HTTPException(
                    400, "Community created, but one or more countries were invalid — set them from the community's page"
                ),
            )

    return result.data


def rename_community(community_id: UUID, request: RenameCommunityRequest):
    try:
        result = repo.rename(str(community_id), request.name)
    except Exception as err:
        raise db_error(err, on_unique=HTTPException(409, "That community name already exists"))

    if not result.data:
        raise HTTPException(status_code=404, detail="Community not found")

    return result.data


def set_community_countries(community_id: UUID, request: SetCommunityCountriesRequest):
    repo.set_countries(str(community_id), [str(cid) for cid in request.country_ids])
    return {"status": "ok"}


def delete_community(community_id: UUID):
    try:
        result = repo.delete(str(community_id))
    except Exception as err:
        # FK violation here means a broker_group still references this community.
        raise db_error(err, on_foreign_key=HTTPException(409, "Can't delete — still in use"))

    if not result.data:
        raise HTTPException(status_code=404, detail="Community not found")

    return result.data
