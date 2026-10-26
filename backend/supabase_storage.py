"""Supabase Storage — where broker documents live.

Sits at root alongside gcs_client.py, and deliberately mirrors its shape
(upload / signed_url / delete, one StorageError): this is an outbound storage
client, not a layer of our own domain. It moves bytes and knows nothing about
brokers.

WHY NOT GCS, WHICH WE ALREADY HAVE. The split is by workload, not preference:

  * CALL RECORDINGS -> GCS. Real volume, a 7-year retention sweep, and egress
    that costs money. gcs_client.py stays exactly as it is.
  * BROKER DOCUMENTS -> here. Three small files per broker; a whole brokerage
    is a few dozen. That does not justify a second bucket, a second IAM grant,
    and a service account with wider permissions than the recordings one has.

Documents also sit next to their own rows this way — broker_document is a
Postgres table in the same project — so there is one system to reach for when
a document and its record disagree.

NEVER PUBLIC. The bucket is created private, and stays private: reads go
through a short-lived signed URL, generated per request. A public bucket would
put licence documents and photos of brokers on a guessable URL.
"""

import logging
import os

from db import supabase

logger = logging.getLogger("myday.supabase_storage")

# Which bucket documents live in. Not configurable: unlike the GCS buckets,
# this one is created and owned by this application, in the same project as the
# rows that point at it. An env var would only ever be a way to get it wrong.
BUCKET = "broker-documents"

# DASH reports live in their own bucket, not beside broker documents. The
# retention question is different in kind: a broker's own licence certificate
# is theirs, while a DASH report is a CUSTOMER'S claims history — so keeping
# the two separable is what makes a retention rule possible to write later
# without sorting one from the other.
DASH_BUCKET = "dash-pdfs"

# How long a document link stays valid. Matches gcs_client's playback window —
# long enough to open and read a certificate, short enough that a URL copied
# out of the network tab is worthless by the time anyone else has it.
_SIGNED_URL_SECONDS = 15 * 60


class StorageError(RuntimeError):
    """Storage refused or failed. Raised instead of letting the storage
    client's own exception types reach the service layer, which would couple
    our error handling to this provider — the same reason gcs_client has one."""


def upload(path: str, data: bytes, content_type: str, bucket: str = BUCKET) -> str:
    """Store a document. Returns the object path it was written to.

    Never overwrites: paths carry a random suffix, so a replacement is always
    a new path and the copy being replaced stays intact until the new one is
    confirmed written.

    `bucket` defaults to broker documents, so every existing caller is
    unchanged; DASH passes DASH_BUCKET.
    """
    try:
        supabase.storage.from_(bucket).upload(
            path,
            data,
            {"content-type": content_type},
        )
    except Exception as err:
        logger.exception("Upload failed for %s", path)
        raise StorageError(f"Could not store the file: {err}") from err

    logger.info("Stored %s (%d bytes)", path, len(data))
    return path


def signed_url(path: str, bucket: str = BUCKET) -> str:
    """A short-lived link the browser can open the document from.

    Issued per request rather than stored: the bucket is private, so a durable
    URL is not something that could exist, and a cached one would outlive its
    own expiry.
    """
    try:
        result = supabase.storage.from_(bucket).create_signed_url(
            path, _SIGNED_URL_SECONDS
        )
    except Exception as err:
        logger.exception("Could not sign a URL for %s", path)
        raise StorageError(f"Could not produce a link: {err}") from err

    # The key is camelCase in the API response, unlike everything else we read
    # from Supabase. Checked rather than indexed: a shape change here would
    # otherwise surface as a KeyError inside a request handler.
    url = (result or {}).get("signedURL") or (result or {}).get("signedUrl")
    if not url:
        raise StorageError(f"Storage returned no link for {path}")
    return url


def delete(path: str, bucket: str = BUCKET) -> None:
    """Remove a document — what a replacement and a removal both call.

    A missing object is not an error. The caller may be clearing an object its
    row no longer points at, and failing there would turn a completed
    replacement into a reported failure.
    """
    try:
        supabase.storage.from_(bucket).remove([path])
        logger.info("Deleted %s", path)
    except Exception as err:
        message = str(err)
        if "404" in message or "not_found" in message or "Not Found" in message:
            logger.info("%s was already gone", path)
            return
        logger.exception("Could not delete %s", path)
        raise StorageError(f"Could not delete the file: {err}") from err
