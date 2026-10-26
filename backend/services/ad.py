"""Business logic for ads. Owns every HTTPException decision."""

from uuid import UUID

from fastapi import HTTPException

from db_errors import db_error
from repositories import ad as repo
from schemas.ad import CreateAdRequest


def list_ads():
    return repo.list_all().data


def create_ad(request: CreateAdRequest):
    values = request.model_dump(mode="json")
    values["ad_group_id"] = str(request.ad_group_id) if request.ad_group_id else None

    try:
        result = repo.insert(values)
    except Exception as err:
        raise db_error(
            err,
            on_foreign_key=HTTPException(400, "That ad group doesn't exist"),
            on_unique=HTTPException(409, "An ad with that platform id already exists"),
        )

    return result.data


def delete_ad(ad_id: UUID):
    result = repo.delete(str(ad_id))

    if not result.data:
        raise HTTPException(status_code=404, detail="Ad not found")

    return result.data
