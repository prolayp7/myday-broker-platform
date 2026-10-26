"""Business logic for sending meta_signal rows to Meta's Conversions API.

This is the ONE piece of the whole CAPI build that needs real Meta
credentials (a dataset id + system user access token) — everything upstream
(the triggers writing Pending rows, meta_capi.py's hashing) works and is
already proven without them.

TWO CALLERS. main.py's lifespan hook runs run_drain_forever() every 15
minutes, and POST /meta-signal/send-pending is the manual nudge. This said
"manually, for now" until 2026-08-07, having outlived the drain it was
written before.

A FAILED ROW IS NOT THE END. Every HTTP 400 from Meta is terminal here —
including code 190, an expired access token, where nothing is wrong with the
event at all. Rather than classify error codes in this file, the admin CAPI
screen lists failures and retries them (routes/meta_signal.py::retry_signal),
which is what makes a token-expiry window recoverable instead of lost.
"""

import asyncio
import json
import logging
import math
import os
import threading
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from repositories import audit_log as audit_repo
from repositories import driver as driver_repo
from repositories import lead as lead_repo
from repositories import meta_signal as repo

import meta_capi
import meta_reporting
from services import meta_signal_alert

logger = logging.getLogger("myday.meta_signal")

# A genuine Meta-side rejection (bad hashing, expired token, stale
# event_time) will not fix itself by resending the identical payload — cap
# attempts so a broken row surfaces to an admin instead of retrying forever
# in silence. Matches meta_signal.attempt_count's own purpose.
MAX_ATTEMPTS = 3

# BEING THROTTLED SAYS NOTHING ABOUT WHETHER THE EVENT IS VALID, so a
# throttled row must never become terminal by running out of attempts. These
# are Meta's own rate-limit codes:
#
#   4  — application request limit reached
#   17 — user request limit reached
#   32 — page request limit reached
#
# THEY ARRIVE BY A DIFFERENT ROUTE THAN 429, which is the whole reason this
# set exists. An HTTP 429 falls past meta_capi.py's (400, 401) branch to
# raise_for_status() and surfaces as a plain Exception — the transient handler
# below. These codes arrive INSIDE a 400 response body, so meta_capi.py raises
# MetaCapiError instead, and that handler treated every Meta rejection as
# terminal on the FIRST attempt. One throttle was retried three times and
# expired; the other was killed instantly. Both now converge on the same
# never-terminal branch through _is_rate_limited().
_RATE_LIMIT_CODES = frozenset({4, 17, 32})


def _is_rate_limited(*, status_code: int | None = None, code: int | None = None) -> bool:
    """Is this failure Meta telling us to slow down, rather than telling us the
    event is wrong?

    Takes both signals because the two arrive by different paths and either one
    alone is an incomplete test — see _RATE_LIMIT_CODES. Called from both
    failure handlers in _send_one so the classification cannot drift between
    them, which is how they came to disagree in the first place.
    """
    return status_code == 429 or code in _RATE_LIMIT_CODES


# ONE DATASET: AUTO INSURANCE (0133).
#
# Every signal goes to the dataset in META_CAPI_DATASET_ID. From 0123 to 0133
# the CRM fanned each conversion out to up to five datasets with their own
# rules; that split a thin book of leads so many ways that no dataset could
# reach the volume Meta's Conversion Leads optimisation needs, and it was
# retired on 2026-10-24. meta_signal.dataset_target remains as history: every
# new row is 'legacy' (Auto Insurance), and the 40 August rows labelled
# 'common' went to a different dataset before the switch.
#
# Read in one place so the sender and meta_received cannot disagree about
# where events go.
def dataset_id() -> str | None:
    """The Meta dataset every signal is sent to, or None if this environment
    has none configured — in which case rows stay Pending rather than fail,
    exactly as with a missing token."""
    return os.environ.get("META_CAPI_DATASET_ID") or None


# How often the background drain runs. 15 minutes is chosen against Meta's
# own two constraints, not picked arbitrarily:
#   * event_time older than 7 DAYS is rejected outright — so the whole
#     retry sequence (3 attempts, ~45 min) has to finish far inside that,
#     and it does, by three orders of magnitude.
#   * Meta reports "Data Freshness" as a quality metric — the delay between
#     an event happening and Meta receiving it. Minutes keeps that healthy;
#     a once-a-day drain would not.
DRAIN_INTERVAL_SECONDS = 15 * 60


# ONE SEND PASS AT A TIME IN THIS PROCESS.
#
# THREADING, NOT ASYNCIO. The two callers reach this from different threads:
# run_drain_forever hands send_pending_signals to asyncio.to_thread (it makes
# blocking httpx and Supabase calls, so running it on the event loop would stall
# every request the app is serving), while POST /meta-signal/send-pending is a
# synchronous route that FastAPI runs in its own threadpool worker. An
# asyncio.Lock would guard neither of those against the other.
#
# WHAT THE RACE COSTS. list_pending has no locking, so an overlapping pass reads
# the same Pending rows and sends them again. Meta deduplicates on event_id
# (meta_signal.id), so the conversion itself was never at risk — the DATABASE
# RECORD was: the slower pass's failure write could land on a row the faster one
# had already marked Sent, leaving a delivered event recorded as Failed. That is
# the precise state 0112's header identifies as the door to double-counting.
#
# Defence in depth with the status guard in mark_attempt_failed: this lock stops
# the overlap inside one process, and the guard makes the write safe even if an
# overlap happens anyway (a second process, a future multi-worker deployment).
# Neither alone is sufficient — the lock is process-local, and the guard cannot
# prevent the wasted duplicate requests.
#
# NON-BLOCKING. A caller that finds the lock held returns immediately rather
# than queueing: the rows are still Pending and the next pass will take them, so
# waiting would only hold an HTTP request open to do work that is already being
# done.
_SEND_LOCK = threading.Lock()


def send_pending_signals(limit: int = 50) -> dict:
    """Reads every Pending row (oldest first) and attempts to send it.
    Never raises past this function — one bad row must not stop the rest of
    the batch from being tried. Returns a small summary for the caller
    (route/admin) to report.

    Returns skipped_locked=True and does nothing if another send pass is already
    running in this process — see _SEND_LOCK.
    """
    if not _SEND_LOCK.acquire(blocking=False):
        logger.info("A send pass is already running — this one does nothing")
        return {
            "sent": 0, "failed": 0, "skipped": 0, "checked": 0,
            "skipped_locked": True,
        }
    try:
        return _send_pending_signals_locked(limit=limit)
    finally:
        _SEND_LOCK.release()


def _send_pending_signals_locked(limit: int = 50) -> dict:
    """The actual pass. Split out so the lock is released on EVERY exit path,
    including an unexpected raise, without wrapping the whole body in a
    try/finally that would bury it.

    Nothing to send to means nothing is fetched: without a dataset every row
    would come back 'skipped' anyway, so the read is skipped too.
    """
    sent, failed, skipped, checked = 0, 0, 0, 0

    if dataset_id() is None:
        logger.debug("No Meta dataset configured — nothing to send")
        return {"sent": sent, "failed": failed, "skipped": skipped, "checked": checked}

    rows = repo.list_pending(limit=limit).data or []
    checked = len(rows)

    for row in rows:
        try:
            outcome = _send_one(row)
        except Exception:
            logger.exception("Unexpected error sending meta_signal %s", row["id"])
            outcome = "failed"

        if outcome == "sent":
            sent += 1
        elif outcome == "failed":
            failed += 1
        else:
            skipped += 1

    return {"sent": sent, "failed": failed, "skipped": skipped, "checked": checked}


def _send_one(row: dict) -> str:
    """Sends a single meta_signal row. Returns 'sent', 'failed', or
    'skipped' (skipped = the CAPI credentials are not configured — not an
    error in the row itself, so it stays Pending for a later attempt).

    A CompleteRegistration with no premium USED to be a fourth outcome,
    'skipped' — which meant Pending forever. It is 'failed' now; see the
    branch itself for why."""
    # GATHERING THE DATA COUNTS AS AN ATTEMPT TOO. These three lines used to
    # sit outside any handler, so a lookup that failed or a payload that could
    # not be built left the row Pending with attempt_count untouched — and the
    # drain retried it every 15 minutes FOREVER. A malformed row would cycle
    # for the life of the process with nothing but a log line to show for it.
    #
    # Same accounting as a network failure: count the attempt, give up after
    # MAX_ATTEMPTS, and let it surface on the admin screen as Failed rather
    # than hiding in a loop.
    try:
        lead = _get_lead(row["lead_id"])
        if lead is None:
            # The lead was deleted after the signal was queued. Nothing to
            # send about — fail it outright rather than spend three attempts
            # on a lead that will never come back.
            repo.mark_attempt_failed(
                row["id"], "Lead no longer exists", row["attempt_count"] + 1, terminal=True
            )
            return "failed"

        driver = _get_principal_driver(row["lead_id"])
        user_data = meta_capi.build_user_data(lead=lead, driver=driver)
    except Exception as err:
        attempts = row["attempt_count"] + 1
        exhausted = attempts >= MAX_ATTEMPTS
        logger.exception("meta_signal %s could not be prepared", row["id"])
        repo.mark_attempt_failed(
            row["id"],
            f"Could not prepare the event: {err}",
            attempts,
            terminal=exhausted,
        )
        return "failed"

    # EVERY EVENT, not just the valued one. event_source and lead_event_source
    # are required by Meta on every Conversion Leads event — their verification
    # guide asks you to inspect each one for them. Built here for Lead too, and
    # given the premium only where there is one.
    #
    # Lead and CompleteRegistration both went out with custom_data=None until
    # 2026-08-07, because this was built inside the (then) Purchase branch.
    # build_custom_data was correct the whole time and had its own tests — but
    # every one of them called it DIRECTLY, so nothing checked that the sender
    # used it. CompleteRegistration, the event the optimisation is aimed at,
    # was one of the two broken ones.
    #
    # ONLY PURCHASE CARRIES MONEY: the YEARLY premium.
    # ensure_conversion_signals (0133) snapshots total_new_premium_annual into
    # payload.total_new_premium_at_purchase at the sale, and this is where it
    # becomes Meta's value. That column is the annual figure whatever unit the
    # broker typed, so a monthly entry still reaches Meta annualised. The read
    # went missing for a whole release once (0100 retired Purchase, 0123
    # restored it under a new key with nothing here reading it) —
    # test_meta_capi.py now covers it.
    total_new_premium = None
    if row["event_name"] == "Purchase":
        total_new_premium = _payload(row).get("total_new_premium_at_purchase")
        # NEVER A PURCHASE WORTH NOTHING. 0133 stops new rows like this, but a
        # row queued by the older SQL could carry 0 (total_new_premium_annual
        # sums with coalesce, so an empty premium row reads 0) or nothing at
        # all. Sent, it would tell Meta the sale was worthless. Failed outright
        # rather than retried: resending the same payload cannot fix it, and
        # Failed puts it in front of an admin.
        if not _positive(total_new_premium):
            repo.mark_attempt_failed(
                row["id"],
                "Purchase has no yearly premium above 0, so it was not sent",
                row["attempt_count"] + 1,
                terminal=True,
            )
            return "failed"

    # total_new_premium is None on Lead and CompleteRegistration, and
    # build_custom_data omits value/currency for them — a Lead carrying
    # value: 0 would tell Meta the conversion was worth nothing, which is a
    # different claim from making no claim at all.
    custom_data = meta_capi.build_custom_data(total_new_premium=total_new_premium)

    target_dataset = dataset_id()
    access_token = os.environ.get("META_CAPI_ACCESS_TOKEN")
    if not target_dataset or not access_token:
        # SKIPPED, not failed: the row is fine, the configuration is not. Stays
        # Pending so it sends once the dataset and token are configured.
        logger.error(
            "META_CAPI_DATASET_ID or META_CAPI_ACCESS_TOKEN is unset — "
            "meta_signal %s stays Pending",
            row["id"],
        )
        return "skipped"

    try:
        response = meta_capi.send_event(
            dataset_id=target_dataset,
            access_token=access_token,
            event_name=row["event_name"],
            # .timestamp() on a tz-aware datetime, NOT time.mktime(): mktime
            # reads a time tuple as LOCAL time, so feeding it a UTC value
            # shifts the result by the machine's offset — on a UTC-4 host
            # that produced an event_time 4 hours in the FUTURE, which Meta
            # rejects outright as an invalid parameter.
            event_time=int(_parse_ts(row["created_at"]).timestamp()),
            event_id=row["id"],
            user_data=user_data,
            custom_data=custom_data,
        )
    except meta_capi.MetaCapiError as err:
        attempts = row["attempt_count"] + 1

        # A THROTTLE IS NOT A REJECTION, even though it arrives down the same
        # pipe. Codes 4/17/32 come back INSIDE a 400 body, so meta_capi.py
        # raises MetaCapiError for them exactly as it does for a bad hash — and
        # this branch used to mark every one of those terminal on the FIRST
        # attempt. A perfectly valid event, refused because we were sending too
        # fast, was thrown away and never retried.
        #
        # No status_code here: a 429 cannot reach this branch at all
        # (meta_capi.py only raises MetaCapiError on 400/401), so err.code is
        # the whole test.
        if _is_rate_limited(code=err.code):
            repo.mark_attempt_failed(
                row["id"],
                f"Meta is rate limiting us (code {err.code}), attempt "
                f"{attempts} — still queued: {err.reason}",
                attempts,
                # NEVER terminal, however many attempts have been spent. See
                # _RATE_LIMIT_CODES.
                terminal=False,
                error_code=err.code,
            )
            return "failed"

        # Meta itself rejected the payload — TERMINAL. Resending the identical
        # body cannot start working (a stale event_time stays stale, a bad
        # hash stays bad), so this needs an admin, not a retry.
        repo.mark_attempt_failed(
            row["id"],
            str(err.reason),
            attempts,
            terminal=True,
            # Meta's own code, so the admin screen can say "the token expired"
            # (190 — replace it and Retry these rows) rather than showing an
            # admin raw error text and leaving them to work out which failures
            # share a cause.
            error_code=err.code,
        )
        return "failed"
    except Exception as err:
        # Network/timeout/5xx — transient. Left as Pending so the next pass
        # retries it, until MAX_ATTEMPTS is reached and it becomes terminal.
        attempts = row["attempt_count"] + 1

        # AN HTTP 429 ARRIVES HERE, not in the branch above: it is not 400/401,
        # so meta_capi.py lets it fall through to raise_for_status() and it
        # surfaces as httpx.HTTPStatusError. Read structurally rather than by
        # importing httpx — this service otherwise never speaks HTTP, and the
        # attribute chain is absent on every other exception type reaching here.
        status_code = getattr(getattr(err, "response", None), "status_code", None)
        if _is_rate_limited(status_code=status_code):
            repo.mark_attempt_failed(
                row["id"],
                f"Meta is rate limiting us (HTTP 429), attempt {attempts} — "
                f"still queued",
                attempts,
                # Same rule as the code-4/17/32 path above, which is the point
                # of routing both through _is_rate_limited: a throttle must not
                # expire just because it was signalled by status instead of by
                # error code.
                terminal=False,
            )
            return "failed"

        exhausted = attempts >= MAX_ATTEMPTS
        message = (
            f"Gave up after {MAX_ATTEMPTS} attempts: {err}"
            if exhausted
            else f"Attempt {attempts} failed, will retry: {err}"
        )
        repo.mark_attempt_failed(row["id"], message, attempts, terminal=exhausted)
        return "failed"

    # BOTH HALVES OF THE CONVERSATION. What we sent, and what Meta said back.
    #
    # The reply is the only place a SILENT failure shows up. migrations/0056
    # states the problem in this table's own header: a hashing or
    # normalisation mistake is invisible, because Meta accepts the event,
    # matches nobody, and still reports success. `messages` carries warnings
    # on an ACCEPTED event — the event is counted, matched to no one, and
    # without this line recorded here as a clean success. `fbtrace_id` is the
    # id Meta support asks for when an event is disputed.
    #
    # send_event's return value was discarded at this call site until
    # 2026-08-07, so every successful send kept only our own side of it.
    #
    # WRAPPED, because THE EVENT HAS ALREADY GONE. If this write fails the row
    # stays Pending with attempt_count untouched, so the drain resends the same
    # event every 15 minutes forever — Meta deduplicates on event_id, so the
    # damage is a permanent loop rather than double-counting, but nothing would
    # ever record the success or surface the problem.
    #
    # Marked Failed rather than left Pending: the send itself worked, so a
    # retry cannot help, and a row an admin can see beats one quietly cycling.
    # attempt_count is advanced too, so a stuck row cannot look untried.
    try:
        repo.mark_sent(
            row["id"],
            # WHICH DATASET THIS WENT TO. Recorded because it is the first
            # thing anyone asks when Meta's Events Manager disagrees with us —
            # "did it go to the dataset I am looking at?" — and it was
            # previously unanswerable: the id is read from the environment at
            # send time, so a row proved only that SOMETHING accepted the
            # event. Diagnosing a reporting gap meant checking Railway's env
            # vars by hand and trusting they had not changed since.
            #
            # In the payload rather than its own column: no migration, and
            # this is diagnostic detail about one send, which is exactly what
            # the payload already holds.
            #
            # MERGED OVER THE QUEUED PAYLOAD, never replacing it. The row was
            # queued with total_new_premium_at_purchase (0133's snapshot), and
            # a plain replace here erased it on every successful send — so no
            # Sent Purchase could ever show its amount on the Meta Quality
            # screen. Found 2026-10-25 by the screen's own live test. The send
            # record's keys win on any collision; today they never collide.
            {
                **_payload(row),
                "dataset_id": target_dataset,
                "user_data": user_data,
                "custom_data": custom_data,
                "response": response,
            },
        )
    except Exception as err:
        logger.exception(
            "meta_signal %s WAS SENT to Meta but could not be marked Sent", row["id"]
        )
        try:
            repo.mark_attempt_failed(
                row["id"],
                f"Sent to Meta, but recording it failed: {err}",
                row["attempt_count"] + 1,
                terminal=True,
            )
        except Exception:
            # Both writes failing means the database is unreachable, which the
            # next pass will surface on its own. Swallowed so one bad row
            # cannot stop the rest of the batch — the same rule
            # send_pending_signals follows.
            logger.exception("meta_signal %s could not be marked at all", row["id"])
        return "failed"

    return "sent"


async def run_drain_forever() -> None:
    """The background loop that makes CAPI hands-off. Started by main.py's
    lifespan hook and cancelled on shutdown.

    WHY THIS EXISTS AT ALL: a Postgres trigger cannot make an HTTP call, so
    the triggers in 0057 can only ever write a Pending row — something has to
    come along afterward and actually send it. Without this loop that
    "something" was only a manual admin route, which meant a signal nobody
    remembered to drain would sit until it passed Meta's 7-day limit and
    became permanently unsendable.

    NEVER RAISES OUT OF THE LOOP. An exception escaping here would kill the
    task silently and CAPI would stop draining with the app still looking
    perfectly healthy — so every cycle is wrapped. A failing cycle logs and
    waits for the next one; the Pending rows are still in the table, so
    nothing is lost by a bad pass.

    Runs in a worker thread (asyncio.to_thread), not directly on the event
    loop: send_pending_signals does BLOCKING httpx and Supabase calls, and
    running those inline would stall every API request the app is serving
    for the duration of the drain.

    Safe for exactly one process, which is how this app is deployed
    (single uvicorn worker). Under multiple workers each would start its own
    copy of this loop and they would race for the same Pending rows — if
    that ever changes, this must move to a host-level scheduler or take a
    database lock first.
    """
    logger.info(
        "Meta CAPI drain started — every %s seconds", DRAIN_INTERVAL_SECONDS
    )
    while True:
        # Wait FIRST, so app startup isn't competing with a send pass.
        await asyncio.sleep(DRAIN_INTERVAL_SECONDS)
        try:
            result = await asyncio.to_thread(send_pending_signals)
            if result["checked"]:
                logger.info("Meta CAPI drain: %s", result)

            # AFTER the pass, not inside it. Sending is the job; telling someone
            # about what could not be sent is a separate concern, and running it
            # here means one alert per pass at most rather than one per row.
            #
            # Its own to_thread for the same reason send_pending_signals needs
            # one: it makes blocking Supabase and Resend calls, and running those
            # on the event loop would stall every request the app is serving.
            #
            # NEVER RAISES (see the module), so it cannot break the drain — but
            # it is inside the same try/except regardless, because a drain that
            # dies from its own alerting would be the worst possible failure.
            await asyncio.to_thread(meta_signal_alert.alert_new_failures)
        except asyncio.CancelledError:
            # Normal shutdown — let it propagate so the task actually ends.
            raise
        except Exception:
            logger.exception("Meta CAPI drain cycle failed — will try again next interval")


def list_recent(status: str | None = None, limit: int = 100) -> list[dict]:
    """What the admin CAPI screen renders.

    Flattens each row into exactly what the table shows — including
    fbtrace_id, dug out of payload here rather than making the screen know the
    shape of what we stored. payload itself is NOT returned: it holds the
    hashed user data, and a screen has no use for a page of digests.
    """
    rows = repo.list_recent(status=status, limit=limit).data or []
    return [
        {
            "id": r["id"],
            "event_name": r["event_name"],
            "status": r["status"],
            "error_code": r.get("error_code"),
            "error_message": r.get("error_message"),
            "attempt_count": r.get("attempt_count", 0),
            "sent_at": r.get("sent_at"),
            "created_at": r["created_at"],
            "lead": r.get("lead"),
            "dataset_target": r.get("dataset_target"),
            "fbtrace_id": _payload({"payload": _payload(r).get("response")}).get("fbtrace_id"),
        }
        for r in rows
    ]


# The three things a lead can be reported for, in the order they happen.
_MILESTONES = ("Lead", "CompleteRegistration", "Purchase")


def _milestone_state(signals: list[dict], configured: bool) -> str:
    """One word for one milestone of one lead.

      none        nothing queued — the milestone has not happened (a lead not
                  yet qualified has no CompleteRegistration to send)
      failed      Meta refused it; needs a person
      waiting     still to be sent
      not_set_up  no dataset is configured here, so it cannot be sent yet and
                  is nobody's fault
      sent        it has been sent
    """
    if not signals:
        return "none"
    if any(s["status"] == "Failed" for s in signals):
        return "failed"
    if any(s["status"] == "Pending" for s in signals):
        return "waiting" if configured else "not_set_up"
    return "sent"


def lead_progress(days: int = 90) -> dict:
    """One row per Meta lead: who has it, when it arrived, and how far its
    reporting to Meta has got — what the admin Meta Conversions screen lists.

    A lead's overall status is its worst milestone: failed beats waiting beats
    sent. "sent" therefore means everything queued so far has arrived, not that
    the lead has reached Purchase.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = lead_repo.list_meta_with_signals(since.isoformat()).data or []
    configured = dataset_id() is not None

    leads = []
    for r in rows:
        # Retired-dataset rows (before 0133) count only where they reached
        # Meta. A Pending or Failed one is never sent or retried now, so
        # counting it would leave the lead stuck at "waiting" or "failed".
        signals = [
            s for s in (r.get("meta_signal") or [])
            if (s.get("dataset_target") or "legacy") == "legacy" or s["status"] == "Sent"
        ]
        milestones = {
            event: _milestone_state([s for s in signals if s["event_name"] == event], configured)
            for event in _MILESTONES
        }
        states = set(milestones.values())
        status = (
            "failed" if "failed" in states
            else "waiting" if "waiting" in states
            else "sent" if "sent" in states
            # Nothing queued at all, or nowhere to send it: there is nothing
            # to report on yet, which is not the same as waiting.
            else "none"
        )

        # SALE AMOUNT — only from a Sent legacy Purchase. Pending or Failed
        # rows show "Sent" in the milestone when another legacy Purchase sent
        # already, but the AMOUNT only comes from one that actually went.
        # NEVER returns anything from payload besides this one numeric key —
        # user_data and the stored Meta response stay inside the backend.
        sale_value = None
        for s in signals:
            if (
                s["event_name"] == "Purchase"
                and s["status"] == "Sent"
                and (s.get("dataset_target") or "legacy") == "legacy"
            ):
                sale_value = _amount(_payload(s).get("total_new_premium_at_purchase"))
                if sale_value is not None:
                    break

        broker = r.get("broker") or {}
        group_row = r.get("broker_group") or {}
        community_row = (group_row.get("community") or {}) if group_row else {}

        leads.append({
            "lead_id": r["id"],
            "name": r["name"],
            "stage": r["stage"],
            "arrived_at": r["created_at"],
            "broker_name": broker.get("name"),
            "broker_id": broker.get("id"),
            # qualified is three-state. `is True` / `is False` preserves the
            # distinction a frontend caller needs: "Not qualified" (false) and
            # "Not asked yet" (null) are different answers.
            "qualified": r.get("is_qualified"),
            "group": {"id": group_row["id"], "name": group_row["name"]} if group_row.get("id") else None,
            "community": (
                {"id": community_row["id"], "name": community_row["name"]}
                if community_row.get("id") else None
            ),
            "sale_value": sale_value,
            "status": status,
            "milestones": milestones,
            # Enough to retry from the row, and to say why in Meta's own words.
            "failures": [
                {
                    "id": s["id"],
                    "event_name": s["event_name"],
                    "dataset_target": s.get("dataset_target"),
                    "error_code": s.get("error_code"),
                    "error_message": s.get("error_message"),
                }
                for s in signals
                if s["status"] == "Failed"
            ],
        })

    # LEARNING COUNT. CompleteRegistration events the drain sent in the last
    # seven days — Meta's rough weekly need to leave the learning phase.
    # Strictly 'legacy': the retired-dataset history must not inflate it.
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    qualified_sent_7d = repo.count_sent_since("CompleteRegistration", seven_days_ago).count or 0

    return {"days": days, "leads": leads, "qualified_sent_7d": qualified_sent_7d}

def health() -> dict:
    """Is Meta still accepting us? Asked directly, not inferred from failures.

    Combines two things the admin screen shows side by side:

      * the TOKEN — valid, revoked, restricted, or wrong scopes, straight from
        Meta's debug_token. Proactive: every other warning in this system
        arrives as a failed conversion, by which point a lead has already gone
        unreported.
      * what has ALREADY failed — the count and the Meta error code behind it,
        so "the token is fine" and "nine conversions failed an hour ago" are
        never shown as though they were the same news.

    The drain is included because a valid token means nothing if nothing is
    sending: a Pending row older than an hour is not waiting for the 15-minute
    drain, it is stuck.
    """
    token = meta_capi.check_token()

    failed = repo.list_recent(status="Failed", limit=200).data or []
    codes: dict[str, int] = {}
    for row in failed:
        if row.get("error_code") is not None:
            key = str(row["error_code"])
            codes[key] = codes.get(key, 0) + 1

    # With no dataset configured, rows stay Pending by design (see _send_one),
    # so nothing can be stuck: zero rather than a false alarm.
    stuck = 0
    if dataset_id() is not None:
        stuck_since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        stuck = repo.count_pending_before(stuck_since).count or 0

    return {
        "token": token,
        "failed_count": len(failed),
        "failed_by_code": codes,
        "stuck_count": stuck,
    }


def summary(days: int = 14) -> dict:
    """The numbers the admin screen draws: totals, a split by event, and a
    per-day sent/failed series.

    Counted in PYTHON over one read rather than in five separate count
    queries. At this volume (a few hundred signals) that is one round trip
    instead of five, and the alternative — a SQL view — would be a schema
    object to keep in step with a screen that will change shape again.

    THE DAILY SERIES IS WHAT MAKES A TOKEN EXPIRY OBVIOUS: a wall of failures
    starting on one day looks nothing like the occasional single failure, and
    a chart shows that difference faster than any list.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = repo.list_since(since.isoformat()).data or []

    by_status: dict[str, int] = {"Sent": 0, "Pending": 0, "Failed": 0}
    by_event: dict[str, int] = {}
    by_day: dict[str, dict[str, int]] = {}
    error_codes: dict[str, int] = {}

    for r in rows:
        status = r["status"]
        by_status[status] = by_status.get(status, 0) + 1
        by_event[r["event_name"]] = by_event.get(r["event_name"], 0) + 1

        # Bucket on the DAY the signal was created, not when it was sent — a
        # failure has no sent_at, and those are exactly the ones worth seeing
        # on the chart.
        day = r["created_at"][:10]
        bucket = by_day.setdefault(day, {"day": day, "sent": 0, "failed": 0, "pending": 0})
        bucket[status.lower()] = bucket.get(status.lower(), 0) + 1

        if status == "Failed" and r.get("error_code") is not None:
            key = str(r["error_code"])
            error_codes[key] = error_codes.get(key, 0) + 1

    # Every day in the window, including the empty ones — a chart with gaps
    # silently rescales and makes a quiet week look like a busy one.
    today = datetime.now(timezone.utc).date()
    series = []
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        series.append(by_day.get(day, {"day": day, "sent": 0, "failed": 0, "pending": 0}))

    return {
        "total": len(rows),
        "by_status": by_status,
        "by_event": [{"event": k, "count": v} for k, v in sorted(by_event.items())],
        "series": series,
        "error_codes": error_codes,
    }


# The matching fields Meta accepts, in a FIXED order so a field absent from
# every event still reports 0/N rather than vanishing from the table — the
# zero IS the finding. Ordered strongest first: lead_id is Meta's own id for
# the form submission (an exact match), then the contact fields, then the
# demographics that only lift an already-matched event.
_REPORTED_FIELDS = (
    "lead_id", "em", "ph", "external_id",
    "fn", "ln", "db", "ge", "ct", "st", "zp", "country",
)


# ONE HUMAN NAME PER FIELD, and the two vocabularies that map onto it.
#
# WE AND META CALL FIVE OF THESE DIFFERENT THINGS. We send Meta's own short
# parameter names (em, ph, db, ge, zp) because that is what the Conversions
# API accepts; Meta's /stats?aggregation=match_keys reports the same fields
# back as email, phone, dob, gen and zip. Stacking the two rows raw would put
# "em 35/35" above "email 49" and read as two unrelated fields.
#
# The other seven agree already (fn, ln, ct, st, country, external_id,
# lead_id), which is exactly why the mismatch is easy to miss.
_FIELD_LABELS: dict[str, tuple[str, str]] = {
    # ours -> (human label, what Meta calls it)
    "lead_id": ("Lead ID", "lead_id"),
    "em": ("Email", "email"),
    "ph": ("Phone", "phone"),
    "external_id": ("External ID", "external_id"),
    "fn": ("First name", "fn"),
    "ln": ("Last name", "ln"),
    "db": ("Date of birth", "dob"),
    "ge": ("Gender", "gen"),
    "ct": ("City", "ct"),
    "st": ("Province", "st"),
    "zp": ("Postal code", "zip"),
    "country": ("Country", "country"),
}


def field_labels() -> list[dict]:
    """The display vocabulary, in _REPORTED_FIELDS order so a field absent
    from every event still holds its place rather than vanishing."""
    return [
        {"field": f, "label": _FIELD_LABELS[f][0], "meta_key": _FIELD_LABELS[f][1]}
        for f in _REPORTED_FIELDS
    ]


def send_quality(days: int = 14) -> dict:
    """What our sent events actually CARRIED, grouped by event type.

    NOT ONE COMBINED PERCENTAGE. Lead fires at lead creation and is
    legitimately thin; CompleteRegistration fires at Quote Done, by which time
    the applicant is filled in. Averaging the two produces a number that
    describes neither and hides both.

    Auto Insurance's events only (dataset_target 'legacy'): the August rows
    labelled 'common' went to another dataset and would muddy the figures.

    READS ONLY THE STORED PAYLOAD — the record of what was sent at the time.
    An earlier design compared each payload against the CRM's CURRENT lead and
    driver rows to report "held but not sent"; that was measured and dropped,
    because 32 of 35 driver rows were updated AFTER their event was sent. A
    broker filling in a date of birth two days later would have been reported
    as a sending gap it never was.

    NEVER RETURNS A HASH. payload.user_data holds only hashed values; this
    reports field NAMES and counts, so nothing identifying leaves the
    endpoint.

    Malformed rows are absorbed one by one — a single bad payload must not
    blank the panel. A DATABASE failure is deliberately NOT absorbed: it
    propagates as a 500, because a dashboard quietly reporting zeros when the
    read failed is worse than one that says it broke.
    """
    now = datetime.now(timezone.utc)
    rows = repo.list_sent_payloads_between(
        (now - timedelta(days=days)).isoformat(),
        now.isoformat(),
    ).data or []

    by_event: dict[str, dict] = {}
    datasets: dict[str, int] = {}
    warnings: dict[str, dict] = {}

    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        user_data = payload.get("user_data")
        if not isinstance(user_data, dict):
            user_data = {}

        # "not recorded" for rows sent before the dataset was stored — a real
        # state, not a mismatch, and it must not read as one.
        key = payload.get("dataset_id") or "not recorded"
        datasets[key] = datasets.get(key, 0) + 1

        bucket = by_event.setdefault(
            row["event_name"],
            {"event_name": row["event_name"], "total": 0, "fields": 0,
             "coverage": {f: 0 for f in _REPORTED_FIELDS}},
        )
        bucket["total"] += 1
        for field in _REPORTED_FIELDS:
            # .get(), not `in`: a present-but-empty value is not coverage, and
            # Meta scores one worse than an absent field anyway.
            if user_data.get(field):
                bucket["coverage"][field] += 1
                bucket["fields"] += 1

        # Meta's own words on an accepted event — stored since 2026-08-07 and
        # read by nothing until now. Grouped rather than listed per event: the
        # same warning on forty events is one problem, not forty.
        response = payload.get("response")
        messages = response.get("messages") if isinstance(response, dict) else None
        for message in messages or []:
            # Meta has returned both strings and objects here, so coerce
            # rather than assume a shape.
            text = message if isinstance(message, str) else json.dumps(message, default=str)
            seen = warnings.setdefault(text, {"message": text, "count": 0, "last_seen": None})
            seen["count"] += 1
            if row.get("sent_at") and (seen["last_seen"] or "") < row["sent_at"]:
                seen["last_seen"] = row["sent_at"]

    events = []
    for bucket in by_event.values():
        total = bucket["total"]
        events.append({
            "event_name": bucket["event_name"],
            "total": total,
            # Guarded: an empty window must not divide by zero.
            "average_fields": round(bucket["fields"] / total, 1) if total else 0.0,
            "coverage": {
                f: {"sent": bucket["coverage"][f], "total": total}
                for f in _REPORTED_FIELDS
            },
        })

    return {
        "events": sorted(events, key=lambda e: e["event_name"]),
        "datasets": datasets,
        "warnings": sorted(warnings.values(), key=lambda w: -w["count"]),
        # The display vocabulary travels WITH the data rather than being
        # duplicated in the frontend: we and Meta name five of these fields
        # differently, and a second copy of that mapping is a second place for
        # it to drift.
        "fields": field_labels(),
    }


def meta_received(days: int = 14) -> dict:
    """What META says it received, for the same window as send_quality.

    ITS OWN FUNCTION AND ITS OWN ROUTE, deliberately not folded into
    send_quality. That one is a single fast database read; this makes up to
    three external calls at ten seconds each, and the dashboard awaits every
    request together before it renders. Merging them would let a Meta outage
    hold up figures that came from our own database and were ready
    immediately.

    `available` is the honest state: false means we could not ask, NOT that
    nothing arrived. The panel says "unavailable" rather than showing zeros,
    because those two readings look identical and mean opposite things.

    WHAT THE NUMBERS MEAN. match_keys counts events in which Meta RECEIVED an
    identifier. It is not a count of successful matches — Meta does not
    expose that for conversion-leads integrations (see meta_reporting.py).
    """
    target_dataset = dataset_id()
    if target_dataset is None:
        # Not configured is not the same as unreachable, but the panel treats
        # both as "unavailable" — which is correct, and better than zeros.
        return {"available": False, "days": days}

    snapshot = meta_reporting.dataset_snapshot(days=days, dataset_id=target_dataset)
    if snapshot is None:
        return {"available": False, "days": days}

    return {"available": True, "days": days, **snapshot}


def retry(signal_id: str, admin_id: str) -> dict:
    """Put ONE failed signal back in the queue. The next drain sends it.

    ONLY A FAILED ROW. A Sent one has already reached Meta, and asking to send
    it again is a different request from "this failed, try again" — refused
    rather than quietly obliged. A Pending one is already queued, so retrying
    it means nothing.

    The id is deliberately unchanged: send_event passes it to Meta as
    event_id, and Meta deduplicates on that. So even a retry of something Meta
    DID receive (a response lost in transit, say) cannot count one conversion
    twice.
    """
    rows = repo.get_by_id(signal_id).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Signal not found")

    # FROM THE RETIRED FIVE-DATASET SETUP (before 0133). The sender only picks
    # up 'legacy' rows, so re-queueing one of these would leave it Pending for
    # ever while the screen said it had been retried.
    if (rows[0].get("dataset_target") or "legacy") != "legacy":
        raise HTTPException(
            status_code=409,
            detail="This signal was for a dataset that is no longer used, so it cannot be retried",
        )

    result = repo.reset_to_pending(signal_id)
    if not result.data:
        # The status guard is in the UPDATE itself, so an empty result means
        # the row was not Failed — someone else retried it, or the drain
        # already sent it while this screen was open.
        raise HTTPException(
            status_code=409,
            detail=f"Only a failed signal can be retried — this one is {rows[0]['status']}",
        )

    # An outbound event to an ad platform is worth a record of who caused it.
    try:
        audit_repo.insert(
            actor_broker_id=admin_id,
            action="retry_meta_signal",
            entity_type="meta_signal",
            entity_id=signal_id,
            detail={"event_name": rows[0]["event_name"], "lead_id": rows[0]["lead_id"]},
        )
    except Exception:
        # The retry itself succeeded. Losing the audit row is worth a log line,
        # not a failed request that would leave the admin unsure whether the
        # retry happened.
        logger.exception("retry of meta_signal %s was not audited", signal_id)

    return result.data[0]


def retry_dead_token_failures(admin_id: str) -> dict:
    """Re-queue every signal that failed because the access token was dead.

    THE RECOVERY PATH FOR THE ONE FAILURE THAT IS NOT THE EVENT'S FAULT. Code
    190 means Meta rejected the credential, not the payload — so every row that
    failed on it is still perfectly sendable once the token is replaced. Before
    this existed the only cure was POST /meta-signal/{id}/retry, one row at a
    time, which is unusable for the case it matters in: a token dies overnight
    and every conversion queued in that window fails together.

    DELIBERATELY NARROW. Not "retry all failed" — a stale event_time or a
    hashing error sitting in the same Failed list has been judged on its merits
    and resending it repeats a request Meta already refused. Meta's dedup window
    is also finite, so blindly resending days-old uncertain deliveries is its own
    risk.

    Every row keeps its id, which is the event_id Meta deduplicates on, so this
    cannot double-count a conversion Meta actually received.
    """
    result = repo.reset_code_190_to_pending()
    rows = result.data or []

    # An outbound bulk action against an ad platform is worth a record of who
    # caused it — same reasoning as the single-row retry above, and more so
    # here, since one click can re-queue every failure in the table.
    try:
        audit_repo.insert(
            actor_broker_id=admin_id,
            action="retry_meta_signal_dead_token",
            entity_type="meta_signal",
            # A SENTINEL, NOT None. audit_log.entity_id is `text not null`
            # (0012:33), so passing None raises a not-null violation — and this
            # call is wrapped in a handler that only logs, so the retry would
            # have succeeded while the audit row silently never appeared. That
            # is the invisible-failure pattern this whole change set exists to
            # remove, so it must not be introduced here.
            #
            # The column is text rather than uuid, so a descriptive value is
            # legitimate: this action genuinely has no single entity, and the
            # ids it touched are recoverable from detail below plus the rows'
            # own error_code history.
            entity_id="bulk:code-190",
            detail={"requeued": len(rows), "error_code": 190},
        )
    except Exception:
        # The re-queue succeeded. Losing the audit row is worth a log line, not
        # a failed request that would leave the admin unsure whether it happened.
        logger.exception("bulk code-190 retry was not audited")

    return {"requeued": len(rows)}


def _payload(row: dict) -> dict:
    """A row's payload as a dict, or {} when it is anything else. Our SQL
    always writes a jsonb object, but a hand-edited row (a list, a string) must
    not crash the sender or the Meta Quality screen."""
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


def _amount(value) -> float | None:
    """A premium as a finite number above zero, or None. Accepts the number or
    the string form a jsonb numeric can take. Rejects NaN and infinity, which
    float() accepts but JSON cannot carry and Meta would refuse."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and n > 0 else None


def _positive(value) -> bool:
    """True for a finite number above zero (see _amount)."""
    return _amount(value) is not None


def _get_lead(lead_id: str) -> dict | None:
    rows = lead_repo.get_by_id(lead_id).data
    return rows[0] if rows else None


def _get_principal_driver(lead_id: str) -> dict | None:
    rows = driver_repo.get_principal(lead_id).data
    return rows[0] if rows else None


def _parse_ts(value):
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
