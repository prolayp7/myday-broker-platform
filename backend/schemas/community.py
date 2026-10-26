from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CreateCommunityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    country_ids: list[UUID] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()


class RenameCommunityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()


class SetCommunityCountriesRequest(BaseModel):
    country_ids: list[UUID] = Field(default_factory=list)
