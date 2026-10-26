"""Turns a stored Meta webhook_event into a real lead row. This is the
actual "leads start flowing" step: webhook notification -> Graph API fetch
of the real answers -> lead_form lookup for routing -> lead created.

CORE RULE: a real customer's lead submission must NEVER be silently lost.
Every failure path here marks the webhook_event as failed WITH A REASON
(repositories/webhook_event.py::mark_failed) instead of raising past this
function — the raw payload is already permanently stored (see
migrations/0007_webhook_event.sql), so a "failed" event is always
recoverable later (map the form, wait for Graph API access, fix a bug, then
re-run ingestion against this same stored event), never lost data.

This deliberately does NOT go in audit_log — ingestion has no human actor
(Meta calls us automatically), and audit_log is actor-shaped. webhook_event's
own status + failure_reason IS the permanent record for this, by design
(decided 2026-07-12)."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import meta_graph
from meta_lead_parser import parse_field_data
from repositories import lead as lead_repo
from repositories import lead_form as lead_form_repo
from repositories import webhook_event as webhook_event_repo
from schemas.lead import CreateLeadRequest
from services import lead as lead_service

logger = logging.getLogger("myday.lead_ingestion")

# How many times an event is ingested before it stops trying on its own.
# Matches meta_signal's MAX_ATTEMPTS and its reasoning: a permanent failure —
# an unmapped form, a payload that is not a leadgen event — fails identically
# every time, so retrying forever only hides it. Four attempts spans about 36
# minutes with the backoff below (1 + 5 + 30), which covers a brief Meta outage
# or a token being rotated — but NOT a form left unmapped overnight, which is
# deliberate: that needs a person, and the row stays stored with its reason.
MAX_ATTEMPTS = 4

# Backoff per attempt, in minutes: 1, 5, 30. The first retry is almost
# immediate because the common transient failure (a network blip, a brief Meta
# 5xx) is usually over within seconds; the later ones give a real outage or a
# human fix — mapping a form — time to happen.
_BACKOFF_MINUTES = (1, 5, 30)


def _fail(event_id: str, reason: str, attempt_count: int = 0) -> None:
    """Record a failed ingestion and schedule the next attempt.

    attempt_count is what the event had BEFORE this attempt, so the row is
    written with attempt_count + 1. The webhook passes 0 (a first attempt);
    the drain passes what it read.

    When the attempts are spent, next_retry_at is set to None and the row
    stops moving on its own — it stays stored, with its reason, for a person
    to look at. That is the design's "never lost, but not always automatic"
    line: the system retries what is worth retrying and gives up loudly rather
    than quietly looping.
    """
    attempts = attempt_count + 1
    exhausted = attempts >= MAX_ATTEMPTS

    next_retry_at = None
    if not exhausted:
        delay = _BACKOFF_MINUTES[min(attempts - 1, len(_BACKOFF_MINUTES) - 1)]
        next_retry_at = (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat()

    if exhausted:
        # Escalated to error: this one will not fix itself, so it is the line
        # worth noticing in the log.
        logger.error(
            "Lead ingestion GAVE UP on webhook_event %s after %s attempts: %s",
            event_id, attempts, reason,
        )
    else:
        logger.warning(
            "Lead ingestion failed for webhook_event %s (attempt %s/%s, retrying): %s",
            event_id, attempts, MAX_ATTEMPTS, reason,
        )

    webhook_event_repo.mark_failed(
        event_id, reason, attempt_count=attempts, next_retry_at=next_retry_at
    )


def ingest_meta_event(event: dict) -> None:
    """Turn one stored webhook_event into a lead. NEVER RAISES — and now that
    is enforced rather than asserted.

    THE BOUNDARY EXISTS BECAUSE THE CLAIM WAS FALSE. The docstring used to say
    "never raises" while three calls sat outside any try: the form lookup, the
    parser, and mark_processed. Proven reachable —

        parse_field_data(['not-a-dict']) -> AttributeError

    — and the consequence was the worst kind. An escape on the first attempt
    left the row at status='received', and the retry drain selects
    status='failed' only, so the event was stranded PERMANENTLY. The one
    mechanism built to rescue failed ingestions could not see it.

    Wrapping the whole thing means an unforeseen failure becomes an ordinary
    retryable one: recorded, reasoned, and picked up again by the drain.

    SAFE TO CALL TWICE, which is what makes the drain possible. Not because
    nothing is written before the end — that was the old, wrong justification —
    but because a lead that already exists is treated as SUCCESS (see
    _already_ingested below) rather than as a duplicate-key failure.
    """
    try:
        _ingest(event)
    except Exception as err:
        # Anything the specific handlers below did not anticipate. Recorded as
        # a normal failure so the drain can try again, rather than escaping and
        # leaving the row wherever it happened to be.
        _fail(
            event["id"],
            f"Unexpected error during ingestion: {err}",
            event.get("attempt_count") or 0,
        )


def _already_ingested(leadgen_id: str) -> bool:
    """Has this submission already produced a lead?

    create_lead and mark_processed are separate transactions. If the lead is
    created but the marking fails, the row stays 'failed' — and every retry
    then hits the unique index on lead.platform_leadgen_id (migration 0054),
    is recorded as another failure, and eventually gives up. The customer's
    lead exists the whole time while the event claims permanent failure.

    "This lead already exists" means ingestion SUCCEEDED. Reading it as a
    failure is what burns the attempts.
    """
    return bool(lead_repo.get_by_platform_leadgen_id(leadgen_id).data)


def _ingest(event: dict) -> None:
    """The actual work. May raise; ingest_meta_event above is the boundary."""
    event_id = event["id"]
    payload = event["raw_payload"]
    # Attempts already spent. Absent on a fresh webhook insert (this is the
    # first try); present when the drain read the row back.
    attempts = event.get("attempt_count") or 0

    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]
        form_id = change["value"]["form_id"]
        leadgen_id = change["value"]["leadgen_id"]
    except (KeyError, IndexError, TypeError):
        _fail(event_id, "Payload did not match the expected Meta leadgen shape (missing form_id/leadgen_id)", attempts)
        return

    # Meta's created_time is a Unix seconds timestamp — when the CUSTOMER
    # actually requested the lead, distinct from when we happen to process
    # this event. Optional field on the schema: absent/malformed here must
    # never fail ingestion over a reporting nicety.
    requested_at = None
    raw_created_time = change["value"].get("created_time")
    if raw_created_time is not None:
        try:
            requested_at = datetime.fromtimestamp(int(raw_created_time), tz=timezone.utc)
        except (TypeError, ValueError):
            requested_at = None

    mapping_rows = lead_form_repo.get_by_platform_form_id(form_id).data
    if not mapping_rows:
        _fail(event_id, f"No lead_form mapping for form_id={form_id} — map this form to a group, then retry", attempts)
        return
    group_id = mapping_rows[0]["group_id"]

    try:
        graph_response = meta_graph.fetch_lead_data(leadgen_id)
    except meta_graph.MetaLeadFetchError as err:
        _fail(event_id, f"Meta rejected the leadgen_id: {err.reason}", attempts)
        return
    except Exception as err:
        # Network error, missing/expired access token, etc. — all recoverable
        # by fixing the underlying cause and re-running ingestion against this
        # same stored event.
        _fail(event_id, f"Could not fetch lead data from Meta: {err}", attempts)
        return

    field_data = graph_response.get("field_data") or []
    parsed = parse_field_data(field_data)

    try:
        create_request = CreateLeadRequest(
            group_id=group_id,
            ad_id=None,  # ad_id enrichment is separate downstream work, see ad.py's own docstring
            source="meta",
            name=parsed["name"],
            phone=parsed["phone"],
            email=parsed["email"],
            extra_data=parsed["raw"],
            licensed_in_canada=parsed["licensed_in_canada"],
            requested_at=requested_at,
            platform_leadgen_id=leadgen_id,
        )
    except Exception as err:
        # Pydantic validation failure — e.g. a value so malformed it can't
        # even fit the schema's length limits. Extremely unlikely given
        # parse_field_data's placeholders, but still must not raise past here.
        _fail(event_id, f"Parsed lead data failed validation: {err}", attempts)
        return

    try:
        lead_service.create_lead(create_request)
    except Exception as err:
        # A retry after a PARTIAL success lands here: the lead was created on
        # an earlier attempt but the event was never marked. Ask the database
        # rather than trying to read the error — the failure text depends on
        # the driver, and a wrong guess would mark a genuine failure as done.
        if _already_ingested(leadgen_id):
            logger.info(
                "webhook_event %s already has its lead (%s) — a previous "
                "attempt succeeded and only the marking failed",
                event_id, leadgen_id,
            )
            webhook_event_repo.mark_processed(event_id)
            return
        _fail(event_id, f"Could not create the lead row: {err}", attempts)
        return

    webhook_event_repo.mark_processed(event_id)
    logger.info("Lead ingested from webhook_event %s (form_id=%s, group_id=%s)", event_id, form_id, group_id)


# ── Retrying what failed ─────────────────────────────────────────────────
# The webhook ingests the lead it just received and nothing else. Before this,
# an event that failed — Meta briefly unreachable, a form not yet mapped — was
# stored with a reason and then never touched again. Meta's own redelivery
# could not rescue it either, because record_meta_event dedups on leadgen_id
# and discards the retry before ingestion is reconsidered.
#
# Interval chosen against the backoff (1, 5, 30 minutes): a two-minute sweep
# is frequent enough that the first retry happens close to its scheduled time,
# and cheap because the query is a partial index over the handful of rows
# actually waiting.
DRAIN_INTERVAL_SECONDS = 2 * 60


# How long an event may sit at 'received' before the drain treats it as
# abandoned. Ingestion normally completes in the same request, so anything
# still there after this either crashed mid-flight or predates the exception
# boundary. Generous enough that a slow Graph call in progress is never
# mistaken for a stalled one.
STALE_RECEIVED_MINUTES = 15


def retry_failed_events(limit: int = 50) -> dict:
    """Re-ingest every event that needs another attempt.

    Two sources, deliberately:

      * status='failed' with next_retry_at due — the ordinary path;
      * status='received' and old — abandoned mid-ingestion. The drain used to
        look only at 'failed', so a process dying between storing the event
        and writing an outcome left the row permanently invisible to it.

    Never raises past this function — one unrecoverable row must not stop the
    rest of the batch, which is the whole reason a stranded lead went unnoticed
    in the first place.
    """
    now = datetime.now(timezone.utc)
    rows = webhook_event_repo.list_due_for_retry(
        now.isoformat(), limit=limit
    ).data or []

    stale_before = (now - timedelta(minutes=STALE_RECEIVED_MINUTES)).isoformat()
    rows += webhook_event_repo.list_stale_received(
        stale_before, limit=limit
    ).data or []

    recovered = 0
    for row in rows:
        try:
            ingest_meta_event(row)
        except Exception:
            # ingest_meta_event is documented never to raise, so reaching here
            # means a bug in it rather than a bad event. Log and carry on; the
            # row keeps its previous state and will come round again.
            logger.exception("Unexpected error retrying webhook_event %s", row["id"])
            continue

        # ingest_meta_event writes the outcome itself, so ask the row rather
        # than inferring it from a return value it does not have.
        after = webhook_event_repo.get_status(row["id"]).data
        if after and after[0]["status"] == "processed":
            recovered += 1

    if rows:
        logger.info(
            "Webhook retry drain: %s recovered of %s due", recovered, len(rows)
        )
    return {"recovered": recovered, "checked": len(rows)}


async def run_retry_drain_forever() -> None:
    """The background loop that makes a failed ingestion self-healing.
    Started by main.py's lifespan hook, alongside the CAPI drain.

    NEVER RAISES OUT OF THE LOOP. An exception escaping here would kill the
    task silently and leads would stop being recovered with the app still
    looking healthy — so every cycle is wrapped. A failing cycle logs and waits
    for the next one; the rows are still in the table, so nothing is lost.

    Runs in a worker thread (asyncio.to_thread), not on the event loop:
    retry_failed_events does blocking Supabase and Graph API calls, and
    running those inline would stall every request the app is serving.

    Safe for exactly one process, which is how this app is deployed (single
    uvicorn worker). Under multiple workers each would start its own copy and
    they would race for the same rows — the same caveat meta_signal's drain
    carries, and the same fix if that ever changes.
    """
    logger.info(
        "Webhook retry drain started — every %s seconds", DRAIN_INTERVAL_SECONDS
    )
    while True:
        # Wait FIRST, so startup is not competing with a drain pass.
        await asyncio.sleep(DRAIN_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(retry_failed_events)
        except asyncio.CancelledError:
            # Normal shutdown — let it propagate so the task actually ends.
            raise
        except Exception:
            logger.exception(
                "Webhook retry drain cycle failed — will try again next interval"
            )
