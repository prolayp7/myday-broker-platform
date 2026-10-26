"""Business logic for incoming webhook events. Owns payload-shape parsing
and the dedup decision. Does NOT do lead ingestion itself — see
services/lead_ingestion.py, which reads the row this creates."""

import hashlib
import logging

from repositories import webhook_event as repo

logger = logging.getLogger("myday.webhooks")


def extract_meta_dedup_key(payload: dict) -> str | None:
    """Meta's leadgen webhook payload shape: entry[].changes[].value.leadgen_id.
    Returns None if the shape doesn't match what we expect — the caller
    decides how to handle that (still store it, just without a real key)."""
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]
        return change["value"]["leadgen_id"]
    except (KeyError, IndexError, TypeError):
        return None


def record_meta_event(payload: dict, raw_body: bytes, signature_valid: bool) -> dict | None:
    """Store the raw event, atomically ignoring a duplicate leadgen_id.
    Returns the newly inserted row (so the caller can run ingestion against
    it), or None if this was a dedup hit — ingestion must NEVER re-run for a
    redelivered event that was already processed/failed, or a duplicate
    Meta retry could create a second lead for the same real submission."""
    dedup_key = extract_meta_dedup_key(payload)
    if dedup_key is None:
        # Malformed/unexpected payload shape — still store it (nothing here
        # should ever silently vanish). Key it off a hash of the raw body so
        # a REDELIVERED identical malformed payload still dedups correctly;
        # a per-object id() would differ on every retry and defeat dedup.
        content_hash = hashlib.sha256(raw_body).hexdigest()
        logger.warning("Meta webhook payload missing expected leadgen_id shape: %s", payload)
        dedup_key = f"malformed:{content_hash}"

    result = repo.insert_if_new(
        {
            "source": "meta",
            "dedup_key": dedup_key,
            "raw_payload": payload,
            "signature_valid": signature_valid,
        }
    )

    if not result.data:
        logger.info("Duplicate Meta webhook event ignored: %s", dedup_key)
        return None
    return result.data[0]
