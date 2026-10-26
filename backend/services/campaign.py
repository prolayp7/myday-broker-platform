"""Business logic for campaigns. Owns every HTTPException decision."""

from uuid import UUID

from fastapi import HTTPException

from repositories import campaign as repo
from schemas.campaign import CreateCampaignRequest


def list_campaigns():
    return repo.list_all().data


def create_campaign(request: CreateCampaignRequest):
    values = request.model_dump(mode="json")
    values["source_id"] = str(request.source_id)

    try:
        result = repo.insert(values)
    except Exception as err:
        if "23503" in str(err):
            raise HTTPException(400, "That ad platform doesn't exist")
        raise HTTPException(status_code=500, detail="Something went wrong")

    return result.data


def delete_campaign(campaign_id: UUID):
    result = repo.delete(str(campaign_id))

    if not result.data:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return result.data
