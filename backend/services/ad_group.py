"""Business logic for ad groups. Owns every HTTPException decision."""

from uuid import UUID

from fastapi import HTTPException

from db_errors import db_error
from repositories import ad_group as repo
from schemas.ad_group import CreateAdGroupRequest


def list_ad_groups():
    return repo.list_all().data


def create_ad_group(request: CreateAdGroupRequest):
    values = request.model_dump(mode="json")
    values["campaign_id"] = str(request.campaign_id)

    try:
        result = repo.insert(values)
    except Exception as err:
        raise db_error(
            err,
            on_foreign_key=HTTPException(400, "That campaign doesn't exist"),
            on_unique=HTTPException(409, "An ad group with that platform id already exists"),
        )

    return result.data


def delete_ad_group(ad_group_id: UUID):
    result = repo.delete(str(ad_group_id))

    if not result.data:
        raise HTTPException(status_code=404, detail="Ad group not found")

    return result.data
