import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from schemas.website_lead import WebsiteLeadPayload
from services import lead_ingestion, website_lead, webhook_event as service

logger = logging.getLogger("myday.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_meta_signature(raw_body: bytes, signature_header: str) -> bool:
    """Meta signs the RAW request body — hashing the parsed-then-reserialized
    JSON would produce different bytes (different whitespace/key order) and
    always fail. hmac.compare_digest avoids timing-attack leakage.

    Accepts a match against EITHER the real META_APP_SECRET (real Meta, always
    checked) OR META_APP_SECRET_SIM (only checked when that env var is set —
    used by the local meta-lead-simulator subproject so its requests are
    verified through this exact same real code path, not a bypass). Production
    never sets META_APP_SECRET_SIM, so there this function's behavior is
    byte-for-byte identical to checking the real secret alone."""
    if not signature_header.startswith("sha256="):
        return False

    provided = signature_header.removeprefix("sha256=")

    app_secret = os.environ["META_APP_SECRET"]
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, provided):
        return True

    sim_secret = os.environ.get("META_APP_SECRET_SIM")
    if sim_secret:
        expected_sim = hmac.new(sim_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected_sim, provided):
            return True

    return False


@router.get("/meta")
def verify_meta_webhook(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """One-time handshake when the Callback URL is configured/re-verified in
    the developer dashboard. Meta sends dotted query param names (hub.mode,
    not hub_mode) — the alias= is required, or these silently never match
    and every request falls through as a mismatch. Must echo hub.challenge
    back as PLAIN TEXT with no JSON wrapper — Meta rejects anything else."""
    expected_token = os.environ["META_WEBHOOK_VERIFY_TOKEN"]

    if hub_mode == "subscribe" and hub_verify_token == expected_token:
        return Response(content=hub_challenge, media_type="text/plain")

    return Response(status_code=403)


@router.post("/meta")
async def receive_meta_webhook(request: Request):
    """Real lead events land here. Verify signature over the RAW body first
    (before any JSON parsing), store the event, then ingest it (Graph API
    fetch + lead_form lookup + lead creation) SYNCHRONOUSLY before returning
    200 — Meta's own guidance is to process leads "as soon as possible", and
    at our volume a Graph API round-trip adds well under the couple-second
    budget Meta tolerates before retrying. Ingestion never raises past this
    point (see services/lead_ingestion.py) — any failure is recorded on the
    webhook_event row itself, not surfaced as a failed webhook response, so
    Meta is never told to retry an event we've already durably stored."""
    raw_body = await request.body()
    signature_header = request.headers.get("X-Hub-Signature-256", "")
    signature_valid = _verify_meta_signature(raw_body, signature_header)

    if not signature_valid:
        logger.warning("Meta webhook signature verification failed")
        return Response(status_code=403)

    # The signature already proved this is genuinely from Meta, so a body we
    # can't parse is a real anomaly worth logging — but still ack with 200,
    # never 500. A 500 makes Meta retry the same unparseable body forever.
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("Meta webhook body was not valid JSON: %s", raw_body[:500])
        return {"status": "received"}

    # OFF THE EVENT LOOP. Everything below is synchronous — a Supabase write,
    # then a Graph API call on the SYNC httpx client with a 10-second timeout
    # (meta_graph.py). This handler is `async def`, which means it runs ON the
    # event loop, so calling that directly froze the loop for the whole
    # request: every other in-flight request across the entire API waited.
    #
    # Measured before and after, with a heartbeat that should tick every 50ms:
    #     direct call        worst concurrent stall 2.27s (at a 2s timeout)
    #     run_in_threadpool  worst concurrent stall 0.07s
    #
    # At the real 10s Graph timeout that was a 10-second stall of the whole
    # app, triggered by one slow webhook.
    #
    # STILL SYNCHRONOUS FROM META'S POINT OF VIEW, deliberately: the lead is
    # fully created before we answer 200. That is worth keeping — acknowledging
    # first and processing later opens a window where Meta believes we have the
    # lead and we only have a job row. The threadpool fixes who is blocked, not
    # the ordering.
    #
    # The handler stays `async def` only because `await request.body()` above
    # which is the ordinary pattern everywhere else in this codebase.
    def _store_and_ingest() -> None:
        event = service.record_meta_event(payload, raw_body, signature_valid)
        if event is not None:
            # None = a duplicate delivery of an event we've already handled
            # (or are already handling) — never re-run ingestion for it, or a
            # Meta retry could create a second lead for the same submission.
            lead_ingestion.ingest_meta_event(event)

    await run_in_threadpool(_store_and_ingest)

    return {"status": "received"}


def _verify_website_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    """The website's server signs the RAW body with WEBSITE_WEBHOOK_SECRET —
    the same scheme as Meta's X-Hub-Signature-256, for the same reason: the
    bytes signed must be the bytes checked, never a re-serialisation.

    A secret of its own, not META_APP_SECRET. The two are held by different
    systems, and rotating one must never break the other."""
    if not signature_header.startswith("sha256="):
        return False
    provided = signature_header.removeprefix("sha256=")
    # compare_digest raises TypeError on a non-ASCII str rather than answering
    # False — a 500 anyone could provoke without the secret.
    if not provided.isascii():
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def _refuse_constant(name: str):
    """json.loads accepts NaN and Infinity, which are not JSON and which the
    database's jsonb refuses — a stored-then-failed write, retried forever by
    the website. Refused at parse time instead."""
    raise ValueError(f"{name} is not valid JSON")


@router.post("/website")
async def receive_website_submission(request: Request, background_tasks: BackgroundTasks):
    """A quote-form submission from the website's server.

    STORE, THEN ANSWER, THEN ASSIGN. The 200 means one thing: the submission is
    durably stored. It does NOT wait for a broker — if every switched-on broker
    is paused or at their limit, the submission waits and is still "received",
    because the customer's details are safe either way. Assignment runs after
    the response, as a background task, with the 60-second drain behind it
    (services/website_lead.py).

    The status codes are part of the contract with the website:
      200  stored (or already stored — a retry with the same submission_id)
      403  bad signature              — the website does not retry
      422  payload fails the contract — the website does not retry
      503  not configured here        — the website retries, then shows the phone
      500  could not store            — the website retries, then shows the phone
    """
    secret = os.environ.get("WEBSITE_WEBHOOK_SECRET")
    if not secret:
        # Refused rather than accepted unsigned. 503, not 403: this is our
        # misconfiguration, not the caller's fault, and a retry may succeed
        # once it is set.
        logger.error("WEBSITE_WEBHOOK_SECRET is not set — website submissions are being refused")
        return Response(status_code=503)

    raw_body = await request.body()
    signature_header = request.headers.get("X-PolicyScanner-Signature", "")
    if not _verify_website_signature(raw_body, signature_header, secret):
        logger.warning("Website webhook signature verification failed")
        return Response(status_code=403)

    # A NUL character is valid JSON and is refused by Postgres in both text and
    # jsonb, so it would fail at storage — a 500 the website retries forever.
    # Checked on the escaped form, the only way it can appear in a JSON body.
    if b"\\u0000" in raw_body:
        return JSONResponse(status_code=422, content={"detail": "Body contains a NUL character"})

    try:
        raw = json.loads(raw_body, parse_constant=_refuse_constant)
        payload = WebsiteLeadPayload.model_validate(raw)
    except ValidationError as err:
        # WHERE and WHAT, never the value. A validation error's `input` is the
        # customer's own name, phone or email, and this response is logged by
        # the website's server.
        #
        # Email gets a FIXED message: email-validator's own text can quote the
        # decoded domain back (an internationalised-domain error does).
        #
        # Only its VALUE errors, though: "Field required" is not about the
        # address, and rewriting it would misreport a contract drift.
        problems = [
            {
                "loc": list(e["loc"]),
                "msg": (
                    "Not a valid email address"
                    if e["loc"][:1] == ("email",) and e["type"] == "value_error"
                    else e["msg"]
                ),
            }
            for e in err.errors()
        ]
        logger.warning("Website submission rejected: %s", problems)
        return JSONResponse(status_code=422, content={"detail": problems})
    except (ValueError, RecursionError):
        # ValueError covers JSONDecodeError, a body that is not UTF-8, and a
        # NaN/Infinity constant; RecursionError a body nested thousands deep.
        # All are the caller's malformed body — a 422, never a 500.
        return JSONResponse(status_code=422, content={"detail": "Body is not valid JSON"})

    # The body is stored whole, ignored keys included. A lone UTF-16 surrogate
    # ("\ud83d") in a key the model ignores passes validation but cannot be
    # encoded to send to the database — a 500 the caller would retry forever.
    try:
        json.dumps(raw, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return JSONResponse(status_code=422, content={"detail": "Body contains invalid Unicode"})

    # Off the event loop, as the Meta handler is: a Supabase write is blocking.
    # A storage failure arrives as an HTTPException(500) — see
    # record_submission — so the website retries and never hears "received".
    await run_in_threadpool(website_lead.record_submission, payload, raw)

    # A duplicate still triggers a drain. Harmless (it finds nothing new or
    # re-tries what is waiting), and it means a retry can never be the reason
    # a stuck submission stays stuck.
    background_tasks.add_task(website_lead.drain)

    return {"status": "received"}
