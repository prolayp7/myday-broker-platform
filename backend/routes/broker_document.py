from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile

from auth_dependencies import require_admin
from schemas.broker_document import BrokerDocumentResponse, DocumentKind
from services import broker_document as service

# Admin-only, like every other /brokers/{id}/… action. A broker does not
# attach their own licence: these are the brokerage's compliance records, and
# the whole point is that someone other than the broker verified them.
router = APIRouter(tags=["broker-document"], dependencies=[Depends(require_admin)])


@router.get(
    "/brokers/{broker_id}/documents",
    response_model=list[BrokerDocumentResponse],
)
def list_documents(broker_id: UUID):
    """Which of the three documents this broker has on file.

    Metadata only — name, type, size, when it was attached. Never the bytes and
    never the object path; reaching the file is a separate, signed request
    below.
    """
    return service.list_for(broker_id)


@router.post(
    "/brokers/{broker_id}/documents/{kind}",
    response_model=BrokerDocumentResponse,
)
async def upload_document(
    broker_id: UUID,
    kind: DocumentKind,
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
):
    """Attach a photo, RIBO licence or E&O certificate, replacing any the
    broker already had of that kind.

    ASYNC for one reason: UploadFile.read is a coroutine. The bytes are read
    here and handed to the service, which is sync like every other service in
    this codebase — so the size rule is applied to exactly what arrived, rather
    than to a Content-Length the client chose.

    kind is a path parameter typed as DocumentKind, so FastAPI rejects an
    unknown kind with a 422 before any of this runs — the check constraint in
    the database is the backstop, not the gate.
    """
    data = await file.read()
    return service.upload(broker_id, kind, file, data, admin["id"])


@router.get("/brokers/{broker_id}/documents/{kind}/url")
def document_url(broker_id: UUID, kind: DocumentKind):
    """A short-lived link for viewing or downloading the document.

    Returns a signed URL rather than the file itself, so it streams straight
    from storage to the browser without passing through this server — the same
    shape as contact audio playback (routes/contact_log.py::contact_audio).

    404 when the broker has nothing of that kind, which is an ordinary state:
    every document is optional.
    """
    return {"url": service.download_url(broker_id, kind)}


@router.delete("/brokers/{broker_id}/documents/{kind}", status_code=204)
def delete_document(
    broker_id: UUID,
    kind: DocumentKind,
    admin: dict = Depends(require_admin),
):
    """Remove a document. Deletes the stored file, not just the record.

    204, no body: there is nothing left to describe.
    """
    service.remove(broker_id, kind, admin["id"])
