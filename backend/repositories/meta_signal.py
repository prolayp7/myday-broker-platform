"""Data access for meta_signal. Only talks to Supabase."""

from db import supabase

# WHICH ROWS ARE LIVE. Since 0133 every signal is 'legacy' (Auto Insurance).
# Rows under the retired five-dataset labels are history: they count where
# they reached Meta (Sent), but a Pending or Failed one is never sent or
# retried now, so showing it as a problem would be a problem nobody can fix.
_LIVE_OR_SENT = "dataset_target.eq.legacy,status.eq.Sent"


def list_pending(limit: int = 50):
    """Rows due for a send attempt: freshly written by a trigger (0057) and
    never yet tried. Ordered oldest first so a backlog drains in the order
    events actually happened — relevant given Meta's 7-day event_time limit,
    where the oldest row is the one closest to becoming unsendable.

    'legacy' ONLY. Every row since 0133 is 'legacy' (Auto Insurance); the
    other labels are history from the five-dataset setup. Filtering here means
    a leftover or retried row from that setup can never be sent to Auto
    Insurance as if it belonged there.
    """
    return (
        supabase.table("meta_signal")
        .select("*")
        .eq("status", "Pending")
        .eq("dataset_target", "legacy")
        .order("created_at")
        .limit(limit)
        .execute()
    )


def list_recent(status: str | None = None, limit: int = 100):
    """What the admin CAPI screen shows: recent signals, newest first, with the
    lead each one is about.

    Embeds the lead rather than making the screen fetch names separately — a
    row reading "CompleteRegistration / Failed" tells an admin nothing
    without knowing WHOSE. Only the four lead fields the screen renders.

    Retired-dataset rows only where Sent (see _LIVE_OR_SENT): this list feeds
    the dashboard's Problems panel and its Retry buttons, and the health count.
    """
    query = (
        supabase.table("meta_signal")
        .select(
            "id, event_name, status, error_message, error_code, attempt_count, "
            "sent_at, created_at, payload, dataset_target, "
            "lead(id, name, email, source)"
        )
        .or_(_LIVE_OR_SENT)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if status:
        query = query.eq("status", status)
    return query.execute()


def get_by_id(signal_id: str):
    return supabase.table("meta_signal").select("*").eq("id", signal_id).execute()


def reset_to_pending(signal_id: str):
    """Put a Failed signal back in the queue.

    THE ID IS UNCHANGED, which is what makes this safe: send_event passes
    meta_signal.id as Meta's event_id, and Meta deduplicates on it. So retrying
    something Meta actually received cannot count the same conversion twice.

    The error is cleared because it describes an attempt that is now history;
    attempt_count is NOT reset, so a row that has failed repeatedly still looks
    like one.

    Guarded on status = 'Failed' in the same statement rather than checked
    first: a check-then-write has a window where the drain could pick the row
    up in between, and this way the database refuses instead.
    """
    return (
        supabase.table("meta_signal")
        .update({
            "status": "Pending",
            "error_message": None,
            "error_code": None,
            # CLEARED, so a row that fails AGAIN is new news. A token replaced on
            # Monday and dying on Friday is two incidents; leaving this stamped
            # would make the second one silent — the same invisibility 0113
            # exists to remove, in a subtler form.
            "failure_alerted_at": None,
        })
        .eq("id", signal_id)
        .eq("status", "Failed")
        .execute()
    )


def mark_sent(signal_id: str, payload: dict):
    """Records a successful send.

    DELIBERATELY UNGUARDED ON STATUS, unlike mark_attempt_failed below. The
    event has already reached Meta by the time this runs, so this write is the
    truth regardless of what a racing drain pass wrote a moment earlier — it is
    allowed to win. A guard here would let a concurrent failure write leave a
    DELIVERED event recorded as Failed, which is exactly the double-counting
    door migration 0112 closed (a Failed row that Meta actually received, then
    re-queued under a new id).
    """
    return (
        supabase.table("meta_signal")
        .update({
            "status": "Sent",
            "payload": payload,
            "sent_at": "now()",
        })
        .eq("id", signal_id)
        .execute()
    )


def reset_code_190_to_pending():
    """Re-queue every signal that failed on a DEAD TOKEN, and only those.

    NAMED FOR WHAT IT DOES rather than "retry all failed", because the two are
    different operations and conflating them is how a blanket resend happens by
    accident. Code 190 is Meta's OAuthException: the event was never judged at
    all, the credential was simply rejected, so once a token is replaced every
    one of these is worth sending again. A hashing error or a stale event_time
    sitting in the same Failed list is NOT — resending those repeats a request
    Meta has already refused on its merits.
    error_code is the discriminator rather than the error text, which Meta
    rewords freely.

    EVERY ROW KEEPS ITS ID, which is what makes a bulk re-queue safe: the id is
    the event_id Meta deduplicates on, so a row Meta actually received despite
    the failure cannot be counted twice. This is an UPDATE for the same reason
    — under 0112's all-status unique constraint an insert would collide, and
    the collision is the point: there is exactly one row per
    (lead, event, dataset) and retry moves it rather than adding to it.

    Guarded on status in the statement, so a row the drain is sending right now
    is not yanked out from under it.
    """
    return (
        supabase.table("meta_signal")
        .update({
            "status": "Pending",
            "error_message": None,
            "error_code": None,
            # Same reasoning as reset_to_pending above: a re-queued row that
            # fails again must be able to alert again.
            "failure_alerted_at": None,
        })
        .eq("status", "Failed")
        .eq("error_code", 190)
        # Only rows the sender will pick up again (see list_pending).
        .eq("dataset_target", "legacy")
        .execute()
    )


def mark_attempt_failed(
    signal_id: str,
    error_message: str,
    attempt_count: int,
    *,
    terminal: bool,
    error_code: int | None = None,
):
    """Records a failed attempt. The SERVICE decides whether this failure is
    terminal; this only writes the outcome it was told.

    terminal=True  -> status 'Failed'. Will not be picked up again by
                      list_pending, and needs an admin to look at it.
    terminal=False -> status stays 'Pending', so the next send pass retries
                      it. Without this distinction nothing would ever be
                      retried at all: 'Failed' rows are excluded from
                      list_pending by design (the partial unique index in
                      migration 0056 also excludes them, so a genuine
                      failure can be superseded later).
    """
    values = {
        "error_message": error_message,
        # Meta's own code, so the admin screen can group failures by CAUSE.
        # None for a timeout or a DNS failure — those never reached Meta, so
        # there is no code to record and guessing one would be worse.
        "error_code": error_code,
        "attempt_count": attempt_count,
    }
    if terminal:
        values["status"] = "Failed"

    return (
        supabase.table("meta_signal")
        .update(values)
        .eq("id", signal_id)
        # ONLY A ROW THAT IS STILL PENDING. The manual POST /send-pending can
        # overlap the 15-minute drain, and without this guard the loser of that
        # race overwrites the winner: drain A sends the event and marks it Sent,
        # drain B's slower attempt then fails and marks the same row Failed — a
        # DELIVERED event recorded as a failure, which 0112's header identifies
        # as the exact state that re-opens double-counting.
        #
        # Meta itself deduplicates on event_id so the conversion was never at
        # risk; the database record was. mark_sent is deliberately NOT guarded
        # this way, so a real success always wins over a racing failure write.
        .eq("status", "Pending")
        .execute()
    )


def list_unalerted_failures(limit: int = 50):
    """Terminal failures nobody has been told about yet (0113).

    THE EDGE, NOT THE LEVEL. "Rows are Failed" is true again on every drain pass
    until someone fixes them, fifteen minutes apart, forever — which is how an
    alert channel gets muted and then misses the one that matters. This asks the
    narrower question: which failures are NEW since the last time we spoke up.

    failure_alerted_at is that memory. Null means unreported, which is also the
    correct state for rows that were already Failed when 0113 shipped: they are
    real, currently unreported failures and they should produce one alert.

    Only the columns the message needs, plus the lead's name so the email can say
    WHOSE conversion was lost rather than listing opaque ids.
    """
    return (
        supabase.table("meta_signal")
        .select(
            "id, event_name, error_code, error_message, attempt_count, "
            "created_at, dataset_target, lead(id, name)"
        )
        .eq("status", "Failed")
        # Live failures only: an old-dataset one cannot be retried any more.
        .eq("dataset_target", "legacy")
        .is_("failure_alerted_at", "null")
        .order("created_at")
        .limit(limit)
        .execute()
    )


def mark_failures_alerted(signal_ids: list[str]):
    """Stamp exactly the rows that were just described in an alert.

    TAKES IDS RATHER THAN REPEATING THE FILTER. A blanket
    "update ... where status = 'Failed' and failure_alerted_at is null" would
    also silence any failure that arrived between the read and this write — rows
    the alert never mentioned, now permanently marked as reported.

    No-ops on an empty list rather than issuing an unfiltered update, which is
    the shape of that mistake at its worst.
    """
    if not signal_ids:
        return None
    return (
        supabase.table("meta_signal")
        .update({"failure_alerted_at": "now()"})
        .in_("id", signal_ids)
        .execute()
    )


def list_since(since_iso: str):
    """Every signal created since a timestamp — the admin screen's chart data.

    Only the four columns the counts need. Deliberately NOT select('*'):
    payload holds the full hashed user_data for every row, which would make
    this read many times larger for numbers that never touch it.
    """
    return (
        supabase.table("meta_signal")
        .select("status, event_name, error_code, created_at")
        .or_(_LIVE_OR_SENT)
        .gte("created_at", since_iso)
        .execute()
    )


def count_sent_since(event_name: str, since_iso: str):
    """How many signals of this event reached Meta since a moment — the Meta
    Quality screen's "qualified sent, last 7 days / 50" head count.

    LEGACY ONLY (0133). The August 'common' rows are history; counting them
    would inflate the figure that measures CURRENT learning progress. Same
    reason list_pending filters on 'legacy'.

    A HEAD COUNT, not a fetched list: the screen only needs the number.
    """
    return (
        supabase.table("meta_signal")
        .select("id", count="exact")
        .eq("status", "Sent")
        .eq("event_name", event_name)
        .eq("dataset_target", "legacy")
        .gte("sent_at", since_iso)
        .limit(1)
        .execute()
    )


def count_pending_before(iso: str):
    """How many signals have been Pending since before a moment.

    A row younger than the drain interval is simply waiting its turn; an older
    one means nothing is sending, which is a different problem and worth its
    own number on the admin screen. 'legacy' only, matching list_pending: a
    row the sender never picks up is not a stopped sender.
    """
    return (
        supabase.table("meta_signal")
        .select("id", count="exact")
        .eq("status", "Pending")
        .eq("dataset_target", "legacy")
        .lt("created_at", iso)
        .execute()
    )


def list_sent_payloads_between(start_iso: str, end_iso: str):
    """Sent signals with their payloads — the send-quality report's one read.

    FILTERED ON sent_at, NOT created_at: this answers "what did we SEND during
    this window", so a signal queued last week and sent today belongs to
    today. list_since above buckets on created_at because it charts when
    events were raised, which is a different question.

    Its own function rather than widening list_since, whose docstring
    deliberately excludes payload: that read is for counts that never touch
    it, and pulling every hashed user_data into it would make the chart's
    query many times larger for nothing.

    AUTO INSURANCE ONLY ('legacy'). The 40 August rows labelled 'common' went
    to a different dataset before 0133 and would mix into the figures.
    """
    return (
        supabase.table("meta_signal")
        .select("id, event_name, payload, sent_at")
        .eq("status", "Sent")
        .eq("dataset_target", "legacy")
        .gte("sent_at", start_iso)
        .lte("sent_at", end_iso)
        .execute()
    )
