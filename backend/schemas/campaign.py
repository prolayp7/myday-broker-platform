from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# Fixed business taxonomy — same vocabulary as broker.lines_of_authority so
# the two stay directly comparable for broker-to-campaign matching.
Product = Literal["Auto", "Travel", "Business", "Life"]


class CreateCampaignRequest(BaseModel):
    # No group_id — campaign is reporting/attribution only. Routing lives on
    # lead_form (platform_form_id -> group_id), since Meta's real webhook
    # never carries campaign_id and routing must never block on the
    # separate follow-up API call needed to resolve it.
    name: str = Field(min_length=1, max_length=200)
    source_id: UUID
    product: Product
    platform_campaign_id: str | None = None
