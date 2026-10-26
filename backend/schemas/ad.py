from uuid import UUID

from pydantic import BaseModel, Field


class CreateAdRequest(BaseModel):
    # Nullable to match the real Meta leadgen webhook: full ad_group context
    # isn't guaranteed at ingestion time, filled in later by enrichment.
    ad_group_id: UUID | None = None
    platform_ad_id: str | None = None
    platform_leadgen_id: str | None = None
    form_id: str = Field(min_length=1, max_length=200)
