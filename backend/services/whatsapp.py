"""Sends the "a lead was assigned to you" WhatsApp message to a broker.

WHY THIS IS A BACKGROUND PASS AND NOT PART OF ASSIGNMENT. Leads are assigned by
a Postgres function (assign_lead, migrations/0019 and 0024), and a Postgres
function cannot make an HTTP call. The trigger in 0109 can only record that a
message is DUE; something outside the transaction has to send it. That is also
the safer arrangement: calling Meta inside the assignment transaction would
let an httpx timeout fail a lead's assignment, and a lead that fails to be
assigned is a far worse outcome than a notification that arrives late.

THE TRANSPORT IS META'S CLOUD API (whatsapp_client.py). It was Twilio until
Twilio was disconnected on the account side, after which every send returned
`Twilio 404 (20404)` and 77 consecutive notifications failed over eleven days
in silence. NOTHING IN THIS FILE CHANGED FOR THAT SWAP except which client it
imports: the queue, the ownership re-check, the consent re-check, the retry
accounting and the fallback are all transport-agnostic, and that is the point
of keeping the client at arm's length.

Same shape as services/meta_signal.py — the
fourth instance of one house pattern, not a new idea.

ONE MECHANISM: THE POLL. An earlier draft also fired a send inline right after
assign_lead() to shave off the wait. It was dropped for two reasons, both worth
recording so it is not reintroduced:

  1. create_lead and assign_lead are SYNCHRONOUS (services/lead.py:221) — an
     unawaited asyncio.create_task() from there has no running event loop to
     attach to, so the send would raise or silently never happen.
  2. It would only cover the Python path. The nightly reset drain (0020) and
     any other SQL-side assignment would still wait for the poll, so how
     quickly a broker heard about a lead would depend on which path assigned
     it — the hardest kind of behaviour to reason about later.

A five-second poll over a partial index of Pending rows (0109) is cheap at
this scale, is the same latency in practice, and treats every assignment path
identically.

WHAT IS NOT HERE: no inbound messages, no replies, no customer-facing chat.
Deliberately (see migration 0109's header and the 2026-07-12 research).

WHEN THIS CHANNEL GIVES UP, services/whatsapp_fallback.py emails the broker the
lead instead — only on a TERMINAL failure (permanent error, or attempts spent),
never on a retryable one. The rule it serves is that every assigned lead reaches
its broker through at least one channel; see migration 0110.
"""

import asyncio
import logging

import whatsapp_client
from repositories import whatsapp_notification as repo
from services import whatsapp_fallback

logger = logging.getLogger("myday.whatsapp")

# How often the queue is checked. Short, unlike the other drains (5
# min, CAPI 15 min), because this one IS the feature: a broker hearing about a
# lead ten minutes late has already lost the race to whoever called first. The
# query is one indexed read against a table that is almost always empty.
DRAIN_INTERVAL_SECONDS = 5

# Attempts before a row stops trying on its own. FIXED RETRIES, NOT BACKOFF —
# a failed row simply waits for the next pass, so four attempts span about
# fifteen seconds. There is no next_attempt_at column and no schedule; at this
# scale neither earns its complexity, and calling it backoff without them
# would just be untrue.
MAX_ATTEMPTS = 4

# Shown when a lead has no licensing answer — a website or phone lead, or a
# Meta form without the question. A missing answer must never suppress the
# notification: the broker still needs to know the lead exists, and an empty
# template variable is rejected by Meta anyway.
_NOT_PROVIDED = "Not provided"

# The stored values are the Meta form's own vocabulary ("2021_or_later"), kept
# that way on purpose so the column and extra_data never disagree about what
# the customer answered (see migration 0108). Turning them into English is a
# PRESENTATION job, and this is the presentation edge — a broker reading their
# phone should not be shown a database value with underscores in it.
_LICENSED_LABELS = {
    "2020_or_before": "2020 or before",
    "2021_or_later": "2021 or later",
}


def _variables(lead: dict) -> dict[str, str]:
    """The three template variables, by position.

    Matches the approved template EXACTLY — its shape is frozen at approval,
    so this order is not free to change without submitting a new template and
    updating WHATSAPP_TEMPLATE_NAME.

        {{1}} customer name
        {{2}} customer phone
        {{3}} licensed in Canada

    THE LABELS LIVE IN THE TEMPLATE, NOT HERE. The message is deliberately
    bare — three facts on three lines under a two-word heading — because a
    broker who gets a WhatsApp from PolicyScanner already knows what it is,
    and every word of preamble pushes the customer's name further down a lock
    screen. So this function sends VALUES ONLY; anything that reads like a
    label ("Phone:") is static text in the approved template.

    phone_e164 in preference to phone so the broker can tap to call; falls back
    to the raw phone when the number could not be normalised, since a badly
    formatted number a human can still read beats showing them nothing.

    NO PRODUCT VARIABLE. lead.combined_policy_type is null at this moment — a
    broker sets it later (services/lead.py:1091), it is never set at ingestion —
    so a Product line would be blank on nearly every message.
    """
    return {
        "1": lead.get("name") or _NOT_PROVIDED,
        "2": lead.get("phone_e164") or lead.get("phone") or _NOT_PROVIDED,
        # .get on the map, not a lookup that assumes: a value the form starts
        # sending tomorrow shows through as-is rather than becoming
        # "Not provided", which would hide a real answer from the broker.
        "3": _LICENSED_LABELS.get(lead.get("licensed_in_canada"),
                                  lead.get("licensed_in_canada")) or _NOT_PROVIDED,
    }


def send_pending() -> dict:
    """One pass: send every queued notification that is still valid.

    NEVER RAISES. A failure on one row must not abandon the rest of the batch,
    and must not escape into the loop that calls this.
    """
    if not whatsapp_client.is_configured():
        return {"checked": 0}

    rows = repo.claim_pending().data or []
    sent = cancelled = failed = 0

    for row in rows:
        lead = row.get("lead") or {}
        broker = row.get("broker") or {}
        row_id = row["id"]

        # ── still theirs? ────────────────────────────────────────────────
        # Re-read the lead's CURRENT owner rather than trusting the queue row.
        # A lead reassigned between queueing and now would otherwise deliver a
        # customer's name and phone to a broker who no longer owns it — the one
        # failure here that reaches a real person and cannot be taken back.
        if lead.get("assigned_broker_id") != row["broker_id"]:
            repo.mark_cancelled(row_id, "Lead was reassigned before the message was sent")
            cancelled += 1
            continue

        # ── still consenting? ────────────────────────────────────────────
        # Checked again even though the trigger checked it: consent can be
        # withdrawn — or revoked automatically by a phone-number change (0109)
        # — in the seconds between queueing and sending.
        if not broker.get("whatsapp_opted_in_at"):
            repo.mark_cancelled(row_id, "Broker is not opted in to WhatsApp notifications")
            cancelled += 1
            continue

        attempt = row.get("attempt_count", 0) + 1
        try:
            sid = whatsapp_client.send_template(broker.get("phone_e164"), _variables(lead))
        except whatsapp_client.WhatsAppError as err:
            # WHETHER TO TRY AGAIN IS DECIDED HERE, not in the repository.
            # A permanent failure — the number is not on WhatsApp, the
            # credentials are wrong — fails identically every time, so it goes
            # straight to Failed rather than spending three more attempts
            # delaying the moment a person sees it.
            exhausted = err.permanent or attempt >= MAX_ATTEMPTS
            repo.mark_failed(row_id, str(err), attempt_count=attempt, exhausted=exhausted)
            failed += 1
            if exhausted:
                # ERROR, not warning: WhatsApp has given up on this lead, and
                # until the fallback lands this is the only trace of it.
                logger.error(
                    "WhatsApp notification %s failed terminally: %s", row_id, err
                )
                # ONLY WHEN EXHAUSTED. A retryable failure is tried again in
                # five seconds and usually works; emailing on the first stumble
                # would double-notify on every hiccup.
                whatsapp_fallback.send_for_row(row, str(err))
            continue
        except Exception as err:
            # Anything unmodelled — a Supabase hiccup, a bug in here. Treated as
            # transient so it retries, and logged loudly because it is not a
            # failure mode we have accounted for.
            logger.exception("Unexpected error sending WhatsApp notification %s", row_id)
            exhausted = attempt >= MAX_ATTEMPTS
            repo.mark_failed(row_id, f"Unexpected error: {err}",
                             attempt_count=attempt, exhausted=exhausted)
            failed += 1
            if exhausted:
                logger.error(
                    "WhatsApp notification %s failed terminally after %s attempts: %s",
                    row_id, attempt, err,
                )
                whatsapp_fallback.send_for_row(row, f"Unexpected error: {err}")
            continue

        # SUBMITTED, NOT DELIVERED. Meta has accepted the message; whether it
        # reached the handset is only known when the status callback arrives
        # (routes/whatsapp_webhooks.py).
        repo.mark_submitted(row_id, sid)
        sent += 1

    return {"checked": len(rows), "submitted": sent, "cancelled": cancelled, "failed": failed}


async def run_send_forever() -> None:
    """The background loop. Started by main.py's lifespan hook, cancelled on
    shutdown.

    NEVER RAISES OUT OF THE LOOP. An exception escaping here would kill the
    task silently and notifications would stop with the app still looking
    perfectly healthy — which for this feature is the worst possible failure:
    nothing visibly breaks (the leads are all still in the CRM, so no broker
    complains) and the channel simply goes quiet.

    Runs its work in a worker thread (asyncio.to_thread): send_pending makes
    blocking httpx and Supabase calls, and running those inline would stall
    every API request the app is serving for the duration of the pass.

    Safe for exactly one process, which is how this app is deployed (Procfile,
    no --workers) — the same assumption the other four background loops make.
    Under multiple workers each would start its own copy and they would race
    for the same Pending rows, sending a broker the same message twice.
    """
    if not whatsapp_client.is_configured():
        logger.info("WhatsApp notifications are not configured — loop not started")
        return

    logger.info("WhatsApp notifications started — every %s seconds", DRAIN_INTERVAL_SECONDS)

    while True:
        # Wait FIRST, so app startup is not competing with a send pass.
        await asyncio.sleep(DRAIN_INTERVAL_SECONDS)
        try:
            result = await asyncio.to_thread(send_pending)
            # Only when something happened: at a five-second interval an
            # unconditional log line would write ~17k lines a day saying
            # nothing, and bury the ones that matter.
            if result["checked"]:
                logger.info("WhatsApp notifications: %s", result)
        except asyncio.CancelledError:
            # Normal shutdown — let it propagate so the task actually ends.
            raise
        except Exception:
            logger.exception("WhatsApp notification pass failed — continuing")
