from uuid import UUID

from pydantic import BaseModel, Field


class CreateLeadFormRequest(BaseModel):
    # The real Meta/TikTok form_id — present in every lead webhook payload,
    # unlike campaign_id which requires a separate follow-up API call. This
    # is the actual routing key. No name field: the display name is fetched
    # from Meta during validation (services/lead_form.py) so it can't drift
    # from the real form.
    platform_form_id: str = Field(min_length=1, max_length=200)
    group_id: UUID


class ValidateFormRequest(BaseModel):
    platform_form_id: str = Field(min_length=1, max_length=200)
