"""Business logic for broker documents — the proof behind the compliance
fields an admin types at onboarding (RIBO licence number, E&O term).

WHY A SEPARATE SERVICE from services/broker.py. That file is about the broker
RECORD; this one moves bytes to and from object storage and owns rules that
have nothing to do with a broker — what a file may contain, how big it may be,
and the order in which a replacement is safe. Keeping them apart also keeps
services/broker.py free of a storage dependency it would otherwise import for
one feature.

SUPABASE STORAGE, NOT GCS. The split is by workload: call recordings run to
real volume with a 7-year retention sweep and metered egress, so they stay in
GCS (gcs_client.py, untouched by this feature). Broker documents are three
small files per broker, and live next to their own rows in the same Supabase
project — no second bucket, no wider IAM grant than the recordings service
account already has.

It does reuse that file's guards (_reject_if_archived), because the archive
rule is the broker's, not this feature's: a second copy of it here would be
free to drift from the one every other mutation enforces.

REPLACEMENT, NOT VERSIONING. One row per broker per kind. Re-uploading an E&O
certificate overwrites the row and deletes the object it replaced — see
migrations/0070 for why history is deliberately not kept.
"""

import logging
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile

import supabase_storage
from db_errors import db_error
from repositories import audit_log as audit_log_repo
from repositories import broker_document as repo
from schemas.broker_document import (
    ALLOWED_TYPES,
    EXTENSION,
    KIND_LABEL,
    KIND_PHRASE,
    MAX_BYTES,
)
from services.broker import _reject_if_archived, _safe_broker_or_404

logger = logging.getLogger("myday.broker_document")


def _object_path(broker_id: str, kind: str, content_type: str) -> str:
    """Where this document belongs in the bucket.

    BROKER-FIRST, unlike recordings' date-first keying (gcs_client.
    recording_path): the retention question here is "this broker left, remove
    their files", which is a prefix delete.

    RANDOM SUFFIX so a replacement never writes over the file it replaces. The
    new object is confirmed written before the old one is deleted, so an upload
    that fails halfway leaves the existing good copy untouched — with a fixed
    path it would already have been overwritten by a partial file.
    """
    return f"brokers/{broker_id}/{kind}-{uuid4().hex}.{EXTENSION[content_type]}"


def _validate(kind: str, file: UploadFile, data: bytes) -> str:
    """Refuse anything this kind should not accept. Returns the content type.

    Checked against the bytes ACTUALLY READ, never file.size or a declared
    Content-Length — both come from the client, so a size limit trusting them
    is a limit on honest callers only.
    """
    allowed = ALLOWED_TYPES[kind]
    content_type = (file.content_type or "").split(";")[0].strip().lower()

    if content_type not in allowed:
        # Name what IS accepted. "Unsupported file type" leaves an admin
        # guessing whether the problem is the format, the size or the kind.
        #
        # Comma-separated with a final "or", rather than "or" throughout: three
        # formats joined by "or" reads as "a JPEG or PDF or PNG file", which
        # scans as a stutter rather than a list.
        names = sorted(t.split("/")[1].upper() for t in allowed)
        readable = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"
        raise HTTPException(
            status_code=422,
            detail=f"{KIND_PHRASE[kind]} must be a {readable} file",
        )

    if not data:
        raise HTTPException(status_code=422, detail="That file is empty")

    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That file is {len(data) / 1024 / 1024:.1f}MB — "
                f"the limit is {MAX_BYTES // 1024 // 1024}MB"
            ),
        )

    return content_type


def _existing(broker_id: str, kind: str) -> dict | None:
    rows = repo.get(broker_id, kind).data
    return rows[0] if rows else None


def list_for(broker_id: UUID) -> list[dict]:
    """Every document this broker has. Missing kinds are simply absent — the
    screen decides what an empty slot looks like."""
    _safe_broker_or_404(str(broker_id))
    return repo.list_by_broker(str(broker_id)).data or []


def upload(
    broker_id: UUID,
    kind: str,
    file: UploadFile,
    data: bytes,
    admin_broker_id: str | None,
) -> dict:
    """Store a document, replacing any the broker already had of this kind.

    Takes the bytes already read rather than reading them here: the route awaits
    the upload (this is sync code, and UploadFile.read is a coroutine), and
    passing them in keeps the size rule applied to exactly what was received.

    ORDER MATTERS. New object written, then the row pointed at it, then the old
    object deleted. A failure at any step leaves either the old document intact
    or an orphaned object — never a row pointing at a file that is not there,
    which is the one state a broker screen cannot render.
    """
    broker_id = str(broker_id)
    _reject_if_archived(broker_id)

    content_type = _validate(kind, file, data)
    previous = _existing(broker_id, kind)

    path = _object_path(broker_id, kind, content_type)
    supabase_storage.upload(path, data, content_type)

    try:
        result = repo.upsert(
            {
                "broker_id": broker_id,
                "kind": kind,
                "object_path": path,
                # Trimmed to what the column holds. A browser sends the name
                # from the user's disk, which is not something to trust the
                # length of.
                "file_name": (file.filename or f"{kind}.{EXTENSION[content_type]}")[:255],
                "content_type": content_type,
                "size_bytes": len(data),
                "uploaded_by": admin_broker_id,
            }
        )
    except Exception as err:
        # The row never landed, so nothing points at the object just written.
        # Remove it rather than leave a file no record will ever mention.
        supabase_storage.delete(path)
        raise db_error(err)

    if previous:
        # Best-effort. The new document is already stored and recorded; failing
        # the whole request because the SUPERSEDED file lingers would report a
        # successful upload as an error. Logged instead, for a cleanup sweep.
        try:
            supabase_storage.delete(previous["object_path"])
        except supabase_storage.StorageError:
            logger.exception(
                "Replaced %s for broker %s but could not delete the old object %s",
                kind,
                broker_id,
                previous["object_path"],
            )

    audit_log_repo.insert(
        actor_broker_id=admin_broker_id,
        action="document_uploaded",
        entity_type="broker",
        entity_id=broker_id,
        # The file NAME and kind, never the object path — an audit row is read
        # by people, and the path is only useful for reaching the bytes.
        detail={
            "kind": kind,
            "file_name": file.filename,
            "size_bytes": len(data),
            "replaced": bool(previous),
        },
    )

    return result.data[0]


def download_url(broker_id: UUID, kind: str) -> str:
    """A short-lived link to view this document.

    Signed per request rather than stored: the bucket blocks public access, so
    a durable URL is not something that could exist, and a cached one would
    outlive its own expiry.
    """
    document = _existing(str(broker_id), kind)
    if not document:
        raise HTTPException(
            status_code=404,
            # KIND_PHRASE minus its article — "A RIBO licence" -> "RIBO
            # licence" — so acronyms keep their capitals where .lower() would
            # flatten them to "ribo licence".
            detail=f"No {KIND_PHRASE[kind].split(' ', 1)[1]} on file for this broker",
        )

    return supabase_storage.signed_url(document["object_path"])


def remove(broker_id: UUID, kind: str, admin_broker_id: str | None) -> None:
    """Delete a document — the row first, then the object.

    That order deliberately risks an orphaned file rather than the reverse: a
    row still pointing at deleted bytes would show a document that cannot be
    opened, which reads as a broken screen rather than a missing file.
    """
    broker_id = str(broker_id)
    _reject_if_archived(broker_id)

    document = _existing(broker_id, kind)
    if not document:
        raise HTTPException(
            status_code=404,
            # KIND_PHRASE minus its article — "A RIBO licence" -> "RIBO
            # licence" — so acronyms keep their capitals where .lower() would
            # flatten them to "ribo licence".
            detail=f"No {KIND_PHRASE[kind].split(' ', 1)[1]} on file for this broker",
        )

    try:
        repo.delete(broker_id, kind)
    except Exception as err:
        raise db_error(err)

    try:
        supabase_storage.delete(document["object_path"])
    except supabase_storage.StorageError:
        logger.exception(
            "Removed the %s row for broker %s but could not delete %s",
            kind,
            broker_id,
            document["object_path"],
        )

    audit_log_repo.insert(
        actor_broker_id=admin_broker_id,
        action="document_deleted",
        entity_type="broker",
        entity_id=broker_id,
        detail={"kind": kind, "file_name": document["file_name"]},
    )


def photo_url(broker_id: str) -> str | None:
    """This broker's photo, for their own header avatar. None when there is
    no photo, or when storage is unreachable.

    SWALLOWS StorageError on purpose, unlike every other function here. This is
    called while assembling /my-profile: a bucket misconfiguration would
    otherwise take down the profile request, and with it the whole app shell,
    over a decorative avatar. Falling back to initials is the correct failure.
    """
    document = _existing(broker_id, "photo")
    if not document:
        return None

    try:
        return supabase_storage.signed_url(document["object_path"])
    except supabase_storage.StorageError:
        logger.exception("Could not sign a photo URL for broker %s", broker_id)
        return None
