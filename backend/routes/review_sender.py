from uuid import UUID

from fastapi import APIRouter, Depends, status

from auth_dependencies import require_admin
from schemas.review_sender import (
    AddReviewSenderRequest,
    ReviewSenderResponse,
    SetReviewSenderRequest,
)
from services import review_sender as service

router = APIRouter(tags=["review_sender"], dependencies=[Depends(require_admin)])


@router.get("/review-senders", response_model=list[ReviewSenderResponse])
def list_review_senders():
    """The addresses that can, or once could, send files for review."""
    return service.list_senders()


@router.post(
    "/review-senders", response_model=ReviewSenderResponse, status_code=status.HTTP_201_CREATED
)
def add_review_sender(request: AddReviewSenderRequest, admin: dict = Depends(require_admin)):
    return service.add_sender(request.email, admin["id"])


@router.patch("/review-senders/{sender_id}", response_model=ReviewSenderResponse)
def set_review_sender(
    sender_id: UUID, request: SetReviewSenderRequest, admin: dict = Depends(require_admin)
):
    return service.set_approved(str(sender_id), request.approved, admin["id"])
