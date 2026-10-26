"""Request and response shapes for the MVR register (migrations/0132)."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The vocabularies the CHECKs in 0132 enforce, declared here so a bad value is
# a 422 naming the allowed ones rather than a database error.
BusinessReason = Literal["new_business", "adding_driver"]
InfoRetrieved = Literal["mvr", "predictor"]


class MvrPullResponse(BaseModel):
    id: str
    broker_id: str
    completed_by: str
    driver_name: str | None
    # FULL for an admin and on the head-office report, masked for a broker —
    # decided in services/mvr_pull.py, never by the screen.
    licence_number: str
    licence_masked: str
    requested_on: date
    aris_order_number: str
    business_reason: BusinessReason
    info_retrieved: InfoRetrieved
    use_completed_on: date
    destruction_on: date
    created_at: datetime


class MvrBroker(BaseModel):
    broker_id: str
    name: str


class MvrMonthResponse(BaseModel):
    month: str
    pulls: list[MvrPullResponse]
    # Empty for a broker: they only ever see themselves.
    brokers: list[MvrBroker]


class MvrUploadResult(BaseModel):
    """One per dropped file. A batch never fails as a whole because one PDF
    in it was not an MVR — the others are still logged."""

    file_name: str
    status: Literal["logged", "already_logged", "unreadable"]
    detail: str | None = None
    pulls: list[MvrPullResponse] = []


class UpdateMvrPullRequest(BaseModel):
    """Only the two dropdowns. Everything else came off the MVR and is not
    the broker's to retype."""

    model_config = ConfigDict(extra="forbid")

    business_reason: BusinessReason | None = None
    info_retrieved: InfoRetrieved | None = None


class MvrReportPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=8, max_length=128)


class MvrReportUnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=128)


class MvrReportUnlockResponse(BaseModel):
    token: str
