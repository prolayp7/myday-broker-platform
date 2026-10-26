from uuid import UUID

from pydantic import BaseModel, Field


class CreateAdGroupRequest(BaseModel):
    campaign_id: UUID
    name: str = Field(min_length=1, max_length=200)
    platform_ad_group_id: str | None = None
