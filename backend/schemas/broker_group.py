from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# Trim before length-checking so a whitespace-only name ("   ") collapses to
# "" and is rejected by min_length, rather than sneaking in as a blank row.
# Mirrors CreateCommunityRequest.strip_name.
def _strip(value: str) -> str:
    return value.strip()


class CreateBrokerGroupRequest(BaseModel):
    community_id: UUID
    name: str = Field(min_length=1, max_length=200)
    # A broker group only exists once it can actually receive leads — see
    # services/broker_group.py::create_broker_group. The form's display name is
    # NOT taken from the admin: create_broker_group fetches it from Meta (so it
    # can never drift from the real form), so only the form id is needed here.
    platform_form_id: str = Field(min_length=1, max_length=200)

    _strip_name = field_validator("name", mode="before")(_strip)


class RenameBrokerGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    _strip_name = field_validator("name", mode="before")(_strip)
