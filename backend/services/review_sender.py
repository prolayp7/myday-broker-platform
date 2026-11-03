"""Who may send files to the submission review.

The worker (submission-review/worker.py) reviews mail only from an address
saved here. The domain is checked here for a clear message, and again by a
CHECK on review.broker, so no route can approve an outside address.

THERE IS NO DELETE. A sender's files point at their row, and turning them off
stops new mail just the same.
"""

import re

from fastapi import HTTPException

from repositories import audit_log as audit_log_repo
from repositories import review_sender as repo

_DOMAIN = "fairstarinsurance.ca"
_ADDRESS = re.compile(rf"[^@\s]+@{re.escape(_DOMAIN)}")


def _audit(admin_id: str, sender: dict, action: str) -> None:
    audit_log_repo.insert(
        actor_broker_id=admin_id,
        action=action,
        entity_type="review_sender",
        entity_id=str(sender["id"]),
        detail={"email": sender["email"]},
    )


def list_senders() -> list[dict]:
    """Fairstar addresses only: an outside row is the old test mailbox's, and
    could not be approved anyway."""
    return repo.list_for_domain(_DOMAIN).data or []


def add_sender(email: str, admin_id: str) -> dict:
    address = email.strip().lower()
    if not _ADDRESS.fullmatch(address):
        raise HTTPException(status_code=422, detail=f"Only @{_DOMAIN} addresses can be added")
    existing = repo.get_by_email(address).data
    if existing and existing[0]["approved"]:
        raise HTTPException(status_code=409, detail="That address is already on the list")
    # An address that sent before this list existed, or was turned off, keeps
    # its row and its files.
    saved = (
        repo.set_approved(existing[0]["id"], True) if existing else repo.insert(address)
    ).data[0]
    _audit(admin_id, saved, "review_sender_added")
    return saved


def set_approved(sender_id: str, approved: bool, admin_id: str) -> dict:
    rows = repo.get_by_id(sender_id).data
    if not rows or not _ADDRESS.fullmatch(rows[0]["email"]):
        raise HTTPException(status_code=404, detail="Sender not found")
    saved = repo.set_approved(sender_id, approved).data[0]
    _audit(admin_id, saved, "review_sender_on" if approved else "review_sender_off")
    return saved
