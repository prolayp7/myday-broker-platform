from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ReviewSenderResponse(BaseModel):
    id: UUID
    email: str
    approved: bool
    created_at: datetime


class AddReviewSenderRequest(BaseModel):
    email: str


class SetReviewSenderRequest(BaseModel):
    approved: bool
