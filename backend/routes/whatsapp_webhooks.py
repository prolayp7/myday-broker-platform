"""Meta's delivery receipts for a WhatsApp notification.

A DELIVERY RECEIPT, NOT INBOUND MESSAGING. This endpoint receives message
status updates — sent/delivered/read/failed — and nothing else. It does not
receive, parse or act on anything a broker types back. The "no inbound
messaging" scope of this feature stands (migration 0109, and the 2026-07-12
research).

WHY IT EXISTS AT ALL. Meta's send API returns a message id and "accepted": the
message has been taken, not delivered. Without this webhook 'Submitted' would
be the terminal state and whether a broker was actually told would be
permanently unknown — in a feature whose failures are otherwise invisible (the
lead is still in the CRM, so nobody complains), that is not good enough.

REPLACES THE TWILIO STATUS CALLBACK. Twilio POSTed one form-encoded status per
request to /webhooks/twilio/status, signed HMAC-SHA1 over the URL plus sorted
parameters. Meta POSTs JSON to /webhooks/whatsapp, signed HMAC-SHA256 over the
RAW body, and BATCHES several statuses into one request. Everything below that
differs from the Twilio version differs for one of those three reasons.

THE SIGNATURE AND HANDSHAKE ARE ROUTES/WEBHOOKS.PY'S, NOT A SECOND SCHEME.
That module already verifies Meta's webhooks for the lead pipeline and records
two traps this endpoint would otherwise rediscover: the hub.* query parameters
need alias= or they silently never bind, and hub.challenge must be echoed as
PLAIN TEXT with no JSON wrapper or Meta rejects the verification.

A DIFFERENT APP, A DIFFERENT SECRET. The WhatsApp app (2051576258799426) is not
the app behind the lead pipeline, so META_APP_SECRET cannot verify these
payloads — WHATSAPP_APP_SECRET does. Sharing one secret would also mean
rotating one channel silently broke the other.
"""

import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from repositories import renewal_notification as renewal_repo
from repositories import whatsapp_notification as repo
from services import renewal_fallback, renewal_inbound, whatsapp_fallback

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])
logger = logging.getLogger("myday.whatsapp")


def _verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """Meta signs the RAW request body — hashing the parsed-then-reserialized
    JSON would produce different bytes (different whitespace and key order) and
    always fail. hmac.compare_digest avoids timing-attack leakage.

    Returns False rather than raising when unconfigured, so a missing secret
    fails closed — an endpoint that skipped verification when misconfigured
    would let anyone mark a broker's message delivered, or forge a failure that
    triggers a fallback email about a real customer.

    Read at call time, not import time: the Twilio version cached its token in a
    module constant, which meant a test or a deploy that set the variable after
    import verified against an empty string and rejected everything.
    """
    app_secret = os.environ.get("WHATSAPP_APP_SECRET", "")
    if not app_secret or not signature_header.startswith("sha256="):
        return False

    provided = signature_header.removeprefix("sha256=")
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


@router.get("")
def verify_whatsapp_webhook(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """One-time handshake when the Callback URL is saved in the Meta dashboard.

    THE TWO TRAPS, both already paid for by routes/webhooks.py. Meta sends
    DOTTED query parameter names (hub.mode, not hub_mode), so the alias= is
    required or these silently never match and every verification falls through
    as a mismatch. And hub.challenge must come back as PLAIN TEXT — Meta
    rejects a JSON wrapper, which is what FastAPI would return by default.

    This will fail until the endpoint is deployed: Meta calls the URL the
    moment Verify-and-save is pressed, so the order is deploy first, then save.
    """
    expected_token = os.environ.get("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")

    if hub_mode == "subscribe" and expected_token and hub_verify_token == expected_token:
        return Response(content=hub_challenge, media_type="text/plain")

    # No detail: a probe learns only that it was refused.
    logger.warning("WhatsApp webhook verification refused")
    return Response(status_code=403)


@router.post("")
async def whatsapp_status_webhook(request: Request):
    """Move notifications to their final state.

    Meta's statuses are not all terminal, so they are mapped explicitly rather
    than treated as "anything that is not delivered is a failure":

        delivered, read   -> Delivered   (it reached the handset)
        failed            -> Failed      (accepted, then did not arrive)
        sent              -> unchanged   (still in flight)

    A BATCH, UNLIKE TWILIO. One request can carry several statuses across
    several entries, so every one is processed and a single unknown id must not
    abandon the others.
    """
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not _verify_signature(raw_body, signature):
        logger.warning("WhatsApp webhook signature verification failed")
        return Response(status_code=403)

    # The signature already proved this is genuinely from Meta, so a body we
    # cannot parse is a real anomaly worth logging — but still ack with 200,
    # never 500. A 500 makes Meta retry the same unparseable body forever.
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("WhatsApp webhook body was not valid JSON: %s", raw_body[:500])
        return {"status": "received"}

    statuses = [
        status
        for entry in payload.get("entry") or []
        for change in entry.get("changes") or []
        for status in (change.get("value") or {}).get("statuses") or []
    ]

    # INBOUND MESSAGES, which this endpoint used to drop on the floor. A broker
    # tapping "Stop Reminder" on a renewal nudge arrives here as
    # value.messages[] with type "button" — a different list from statuses, and
    # one request can carry both.
    #
    # Handled BEFORE the `if not statuses` return below, or a request carrying
    # only a button press would exit as "nothing to do".
    buttons = [
        message
        for entry in payload.get("entry") or []
        for change in entry.get("changes") or []
        for message in (change.get("value") or {}).get("messages") or []
        if message.get("type") == "button"
    ]
    for message in buttons:
        try:
            outcome = await run_in_threadpool(renewal_inbound.handle_button, message)
            logger.info("WhatsApp button press: %s", outcome)
        except Exception:
            # Never let one press abandon the rest of the batch, or the
            # statuses below it. handle_button already swallows its own
            # failures; this is the belt to that braces.
            logger.exception("Could not handle an inbound WhatsApp button press")

    # Nothing to do: Meta also sends message-template and account-update events
    # to the same endpoint, and an inbound message would land here too. None of
    # them is a delivery receipt, and none is an error.
    if not statuses:
        return {"status": "received"}

    unknown = 0
    for status_row in statuses:
        if await _apply_status(status_row):
            continue
        unknown += 1

    if unknown:
        # A RACE, NOT AN ERROR — AND A 5xx ON PURPOSE.
        #
        # Meta can deliver a status before our own worker has finished writing
        # the message id to the row. Answering 200 would tell Meta the receipt
        # was handled, it would never resend, and the row would sit at
        # 'Submitted' forever. A 5xx makes Meta retry, and by then the id is
        # there.
        #
        # RAISED ONLY AFTER THE WHOLE BATCH IS PROCESSED, which is the
        # difference from the Twilio version. Meta will redeliver every status
        # in the batch, and the ones already applied are idempotent — marking a
        # Delivered row Delivered again changes nothing — so processing first
        # and asking for a retry second loses nothing and saves the known rows
        # from waiting on the unknown one.
        logger.info(
            "WhatsApp webhook: %s of %s statuses not yet recorded — asking for a retry",
            unknown, len(statuses),
        )
        return Response(status_code=503)

    return {"status": "received"}


async def _apply_status(status_row: dict) -> bool:
    """One status from the batch. False when its message id is unknown to us.

    Split out so the batch loop reads as what it is — the same decision applied
    to each status — rather than four levels of nesting.
    """
    message_id = status_row.get("id") or ""
    status = (status_row.get("status") or "").lower()

    if not message_id:
        # Nothing to look the row up by. Not a retry candidate: redelivering
        # the same malformed status would not grow an id.
        logger.warning("WhatsApp status with no message id: %s", status_row)
        return True

    # WHICH QUEUE IS THIS? Two tables now carry sent messages —
    # whatsapp_notification (lead alerts, 0109) and renewal_notification
    # (renewal nudges, 0118) — and a receipt names only a message id.
    #
    # The QUEUE TRAVELS WITH THE ROW from here on. Looking the row up and then
    # assuming the lead-alert repository would mark the wrong table and email a
    # broker "New lead assigned" about a renewal.
    rows = repo.get_by_message_sid(message_id).data or []
    queue, fallback = repo, whatsapp_fallback
    if not rows:
        # SECOND QUEUE, AND ITS FAILURE IS NOT THIS RECEIPT'S PROBLEM. An id we
        # do not recognise is the ordinary race — Meta beating our own worker to
        # the write — and the answer to that is the 503 retry below, not a 500.
        # Letting a Supabase error here escape would turn a routine race into a
        # failed webhook and lose the statuses batched alongside it.
        try:
            rows = renewal_repo.get_by_message_sid(message_id).data or []
        except Exception:
            logger.exception(
                "Could not check the renewal queue for message %s", message_id
            )
            rows = []
        queue, fallback = renewal_repo, renewal_fallback
    if not rows:
        return False

    row = rows[0]

    if status in ("delivered", "read"):
        queue.mark_delivered(row["id"])
    elif status == "failed":
        # The error code is what makes this actionable — it is the difference
        # between "this broker's number is not on WhatsApp" and "our template
        # was rejected", which need completely different fixes.
        errors = status_row.get("errors") or []
        code = (errors[0].get("code") if errors else None) or "unknown"
        detail = (errors[0].get("title") if errors else "") or ""
        reason = f"Meta reported failed (code {code}){f': {detail}' if detail else ''}"
        queue.mark_failed_from_callback(row["id"], reason)

        # THE THIRD TERMINAL PATH. The message was accepted and then did not
        # arrive, which no retry from our side fixes — so the broker is told by
        # email instead. This is the path that would have caught the 2026-09-01
        # incident: every one of those rows was accepted and then failed here,
        # silently.
        #
        # ERROR, not warning: WhatsApp is done with this lead.
        logger.error("WhatsApp notification %s failed at delivery: %s", row["id"], reason)

        # By id, not by row: this handler holds only what get_by_message_sid
        # selected (id and status), so the fallback re-reads the lead and broker
        # it needs. Never raises — a fallback failure must not turn into a 500
        # that makes Meta retry a receipt we have already recorded.
        #
        # OFF THE EVENT LOOP: send_for_row_id makes blocking Supabase and HTTP
        # calls, and this handler is async, so calling it directly would stall
        # every other in-flight request. routes/webhooks.py measured that same
        # mistake at a 2.27s stall becoming 0.07s.
        await run_in_threadpool(fallback.send_for_row_id, row["id"], reason)
    # sent: still in flight, leave the row alone.

    return True
