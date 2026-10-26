"""Website leads: store the submission, then hand it to a switched-on broker.

TWO HALVES, and the order is the point.

  1. record_submission stores what the website sent as a 'waiting' row in
     webhook_event. That is all the webhook waits for before answering
     "received" — so the customer's thank-you screen depends only on the
     submission being durably stored, never on a broker being free.
  2. drain runs the SQL that turns waiting submissions into leads, oldest
     first (migration 0127, assign_waiting_website_leads). It runs straight
     after a submission, after an admin change on the Website leads screen,
     and every 60 seconds.

A SEPARATE SYSTEM FROM FACEBOOK'S. Nothing here touches lead_form, assign_lead
or lead_delivery; see 0127's header for why that separation is deliberate.
"""

import asyncio
import logging
import threading
from uuid import UUID

from fastapi import HTTPException

from db_errors import db_error
from repositories import website_lead as repo
from schemas.website_lead import (
    UpdateWebsiteBrokerRequest,
    UpdateWebsiteBrokersRequest,
    WebsiteLeadPayload,
)

logger = logging.getLogger("myday.website_lead")

# The fallback. The drain also runs after every submission and every admin
# change, so this pass only matters for what nothing else triggers: a broker
# unpausing, the daily reset freeing up limits, or a run that failed.
DRAIN_INTERVAL_SECONDS = 60


def record_submission(payload: WebsiteLeadPayload, raw: dict) -> bool:
    """Store the submission. True if it was new, False if this submission_id
    was already stored — a retry, or a second click on Send.

    A DUPLICATE IS STILL A SUCCESS to the caller: the customer's details are
    stored either way, which is all "received" promises.

    RAISES HTTPException(500) on a database error, deliberately. The webhook
    must answer 5xx then, so the website retries and, failing that, shows the
    customer the phone number — a "received" for a submission that was never
    stored is the one outcome this whole design exists to prevent.

    THROUGH db_error, not a bare raise. An unhandled APIError is logged by
    uvicorn with str(err), which includes Postgres's `details` — and on a
    check violation that is "Failing row contains (...)": this customer's
    name, phone and email, in the server log. db_error logs the code and the
    constraint name only.
    """
    try:
        result = repo.insert_submission_if_new({
            "source": "website",
            # Namespaced so a website id can never collide with a Meta
            # leadgen_id in the same unique column.
            "dedup_key": f"website:{payload.submission_id}",
            # The body exactly as the website sent it, kept forever. This is
            # the record of the consent notice the customer was shown and when
            # — the lead itself carries only what a broker acts on.
            "raw_payload": raw,
            # Only reachable past the signature check in routes/webhooks.py.
            "signature_valid": True,
            "status": "waiting",
        })
    except Exception as err:
        raise db_error(err)

    if not result.data:
        logger.info("Duplicate website submission ignored: %s", payload.submission_id)
        return False
    return True


# How many submissions one SQL call may take. The drain is all-or-nothing per
# call — a temporary error rolls the whole call back — so a call must stay well
# inside the database's statement timeout. Unbatched, a large enough backlog
# (a weekend with nobody switched on) could time out on every run, the same
# work each time, and the queue would never move again.
DRAIN_BATCH = 100

# One drain at a time IN THIS PROCESS. Every submission and every admin change
# schedules a drain; in a burst, all but one would only queue on the database
# lock, each holding a worker thread and a connection to find nothing to do.
# Instead, a caller that finds a drain already running leaves a note, and the
# running one goes round again. (Across processes the advisory lock in 0127
# still orders them.)
_drain_lock = threading.Lock()
_drain_again = threading.Event()


def drain() -> dict | None:
    """Give every waiting submission a broker, oldest first, until nobody is
    free. Returns the last batch's {assigned, failed, waiting}, or None when a
    run failed or another drain in this process is already doing the work.

    NEVER RAISES. Every caller is a background task or the timed loop, and a
    failed run loses nothing: the SQL rolls a batch back on a temporary error,
    so every submission in it is still 'waiting' for the next run.
    """
    _drain_again.set()
    result = None
    while True:
        if not _drain_lock.acquire(blocking=False):
            # The running drain will see the note and go round again.
            return None
        try:
            while _drain_again.is_set():
                _drain_again.clear()
                result = _drain_all_batches()
        finally:
            _drain_lock.release()
        # A note left between the last check and the release would otherwise
        # wait for the 60-second loop. Take it now if nobody else has.
        if not _drain_again.is_set():
            return result


def _drain_all_batches() -> dict | None:
    """Batch after batch until one comes back short — a short batch means the
    queue emptied or nobody is free, and either way there is no more to do."""
    result = None
    while True:
        try:
            result = repo.assign_waiting(DRAIN_BATCH).data
        except Exception as err:
            # code and message only — see record_submission for why not the
            # traceback. Nothing is lost: the batch rolled back to 'waiting'.
            logger.error(
                "Website lead drain failed [%s]: %s — submissions stay waiting for the next run",
                getattr(err, "code", None), getattr(err, "message", None) or type(err).__name__,
            )
            return None

        if not result:
            return result
        if result.get("assigned") or result.get("failed"):
            logger.info("Website lead drain: %s", result)
        if result.get("failed"):
            # Failed means a submission that can never become a lead as sent.
            # It is on the admin screen's count too; this is the server-side
            # trail.
            logger.warning("Website lead drain marked %s submission(s) failed", result["failed"])
        if result.get("assigned", 0) + result.get("failed", 0) < DRAIN_BATCH:
            return result


async def run_drain_forever() -> None:
    """The 60-second fallback, for the life of the app.

    WORKS FIRST, THEN SLEEPS. A submission that arrived while the app was
    restarting may have missed its immediate drain, and there is no reason to
    make that customer wait an extra minute.

    to_thread because the drain is a blocking Supabase call; on the event loop
    it would stall every in-flight request.
    """
    logger.info("Website lead drain started — every %ss", DRAIN_INTERVAL_SECONDS)
    while True:
        try:
            await asyncio.to_thread(drain)
            await asyncio.sleep(DRAIN_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            # Shutdown. Re-raised so the lifespan's await completes.
            raise
        except Exception:
            # drain() does not raise, so this is a bug in the loop itself.
            # Never let it end the loop.
            logger.exception("Website lead drain loop failed")
            await asyncio.sleep(DRAIN_INTERVAL_SECONDS)


def get_board() -> dict:
    """What the Website leads screen shows: every broker's switch, limit and
    today's count, and how many submissions are waiting or failed."""
    return repo.board().data


def update_broker(broker_id: UUID, request: UpdateWebsiteBrokerRequest) -> dict:
    """Switch a broker on or off, or change their website limit.

    Runs under the drain's lock (0127, set_website_broker_setting): once a
    switch-off returns, no new website lead can go to that broker. The caller
    runs the drain afterwards, so switching someone on releases the queue
    immediately rather than at the next minute.
    """
    try:
        result = repo.set_broker_setting(str(broker_id), request.is_on, request.daily_cap)
    except Exception as err:
        raise db_error(
            err,
            on_foreign_key=HTTPException(status_code=404, detail="Broker not found"),
        )

    row = result.data
    # A composite-returning RPC can come back as the row itself or a one-item
    # list depending on the PostgREST version; accept both.
    if isinstance(row, list):
        row = row[0] if row else None
    if not row:
        raise HTTPException(status_code=500, detail="Could not save the setting")
    return row


def update_brokers(request: UpdateWebsiteBrokersRequest) -> list[dict]:
    """The same switch and/or limit for many brokers, all or nothing.

    One SQL call under the drain's lock (0129, set_website_broker_settings),
    so a bulk switch-off holds for everyone listed from the same moment, and a
    failure changes nobody — there is no half-done state to describe to the
    admin. The caller runs the drain once afterwards.
    """
    try:
        result = repo.set_broker_settings(
            [str(broker_id) for broker_id in request.broker_ids],
            request.is_on,
            request.daily_cap,
        )
    except Exception as err:
        raise db_error(
            err,
            on_foreign_key=HTTPException(
                status_code=404, detail="One of those brokers was not found — nobody was changed"
            ),
        )

    rows = result.data or []
    # Every id was distinct and every one upserts a row, so fewer rows back
    # than ids sent means the write did not do what was asked.
    if len(rows) != len(request.broker_ids):
        raise HTTPException(status_code=500, detail="Could not save the settings")
    return rows
