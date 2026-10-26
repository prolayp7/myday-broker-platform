"""Business logic for follow-ups.

Owns the lifecycle rules the database can only half-enforce: that snoozing
MOVES a due date rather than setting a status, that closing a reminder always
stamps who and when, and that a reminder belongs to whoever holds the lead now.
"""

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from db_errors import db_error
from repositories import lead as lead_repo
from repositories import reminder as repo

logger = logging.getLogger("myday.reminder")

# The brokerage is Ontario. My Day's "overdue" and "due today" are answers to a
# question a broker asks in their own timezone — computing them in UTC would
# move the boundary by five hours and put this evening's follow-ups in
# tomorrow's count.
_TZ = ZoneInfo("America/Toronto")

OPEN = "Open"
DONE = "Done"
DISMISSED = "Dismissed"


def list_for_lead(lead_id: str) -> list[dict]:
    if not lead_repo.get_by_id(lead_id).data:
        raise HTTPException(status_code=404, detail="Lead not found")
    return repo.list_for_lead(lead_id).data or []


def create(
    lead_id: str,
    broker_id: str,
    due_at: str,
    note: str,
    kind: str = "Callback",
    source_contact_log_id: str | None = None,
) -> dict:
    """Set a follow-up on a lead.

    broker_id comes from the session — a broker sets their own reminders. When
    a lead is reassigned the open reminders move with it (a promise is a debt
    against the future, owed by whoever holds the lead now), which is the
    opposite of contact_log, where a call Sarah made stays Sarah's forever
    because it is a record of the past.
    """
    if not lead_repo.get_by_id(lead_id).data:
        raise HTTPException(status_code=404, detail="Lead not found")

    try:
        result = repo.insert(
            {
                "lead_id": lead_id,
                "broker_id": broker_id,
                "kind": kind,
                "due_at": due_at,
                "note": note,
                "source_contact_log_id": source_contact_log_id,
            }
        )
    except Exception as err:
        raise db_error(
            err,
            on_foreign_key=HTTPException(
                status_code=404, detail="That lead, broker or contact no longer exists"
            ),
        )

    created = repo.get_by_id(result.data[0]["id"]).data
    return created[0] if created else result.data[0]


def _get_open_or_404(reminder_id: str) -> dict:
    rows = repo.get_by_id(reminder_id).data
    if not rows:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return rows[0]


def complete(reminder_id: str, broker_id: str) -> dict:
    """The broker did the thing.

    completed_at is what makes "was this done on time?" answerable at all —
    status alone says the work happened but never whether it happened the same
    day or three weeks late, which is the only thing worth measuring about a
    follow-up.
    """
    reminder = _get_open_or_404(reminder_id)
    if reminder["status"] != OPEN:
        raise HTTPException(status_code=409, detail="This follow-up is already closed")

    repo.update(
        reminder_id,
        {
            "status": DONE,
            "completed_at": datetime.now(_TZ).isoformat(),
            "completed_by": broker_id,
        },
    )
    return _get_open_or_404(reminder_id)


def dismiss(reminder_id: str, broker_id: str) -> dict:
    """No longer relevant — the lead went Lost, the customer bought elsewhere.

    Deliberately NOT folded into complete(): counting an abandoned follow-up as
    completed work would flatter every follow-through report in the system.
    """
    reminder = _get_open_or_404(reminder_id)
    if reminder["status"] != OPEN:
        raise HTTPException(status_code=409, detail="This follow-up is already closed")

    repo.update(
        reminder_id,
        {
            "status": DISMISSED,
            "completed_at": datetime.now(_TZ).isoformat(),
            "completed_by": broker_id,
        },
    )
    return _get_open_or_404(reminder_id)


def snooze(reminder_id: str, due_at: str) -> dict:
    """Push a follow-up to a later time.

    Snoozing MOVES due_at rather than setting a 'Snoozed' status. A status
    would leave the row with its old due_at still in the past, so it would keep
    counting as overdue forever — the bug that makes people stop trusting a
    reminder system.

    attempt_count goes up, because a snooze is usually a failed attempt: rang,
    no answer, try tomorrow. That keeps one unkept promise as ONE row rather
    than a new row per attempt, and makes "chased six times" visible — a
    different conversation from "chased once".
    """
    reminder = _get_open_or_404(reminder_id)
    if reminder["status"] != OPEN:
        raise HTTPException(status_code=409, detail="This follow-up is already closed")

    repo.update(
        reminder_id,
        {"due_at": due_at, "attempt_count": (reminder.get("attempt_count") or 0) + 1},
    )
    return _get_open_or_404(reminder_id)


def my_day(broker_id: str) -> dict:
    """What My Day's Follow-ups block needs: overdue, due today, and the rows.

    Both counts are computed against Ontario's end-of-today, in one query —
    "overdue" and "due today" differ only by which side of now they fall, so
    fetching them separately would be two round trips for one list.
    """
    now = datetime.now(_TZ)
    end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    rows = repo.list_open_for_broker(broker_id, end_of_today.isoformat()).data or []

    overdue, due_today = [], []
    for row in rows:
        due = datetime.fromisoformat(row["due_at"])
        (overdue if due < now else due_today).append(row)

    return {
        "overdue_count": len(overdue),
        "due_today_count": len(due_today),
        "reminders": rows,
    }


# ── The reminders the system sets itself ────────────────────────────────
#
# Every rule keys off the principal driver's renewal_date (0098) — the date the
# customer's CURRENT cover runs out. Miss it and they auto-renewed with their
# existing insurer and are gone for a year, which is why 0061 made 'Renewal' a
# kind of its own rather than another callback.

# Leads still being worked. A Sold or Issued policy needs no chase, and a Lost
# one has its own rule below.
_ACTIVE_STAGES = ("New", "In Progress", "Quote Done", "Pending")

# BANDS, NOT EXACT DAYS, and they MUST NOT OVERLAP.
#
# The original rules fired only when the gap was exactly days_before. With one
# rung that was survivable; with three it drops reminders silently — a day of
# downtime loses a rung for everyone, and a renewal date first typed when the
# customer is already 12 days out misses the 30 and the 15 forever.
#
# An inclusive (lo, hi) per rung fixes that, but only while the bands are
# disjoint. `delta <= days_before` was tried and is WRONG: at delta 12 it is
# true for both the 30- and the 15-rung, so a late-entered lead gets a burst of
# nudges instead of one.
#
# An exact-day rule is just a band where lo == hi, which is how renewal_lost
# keeps its 0104 behaviour without a second code path.
_RULES = (
    {"auto_rule": "renewal_30", "stages": _ACTIVE_STAGES, "band": (16, 30)},
    {"auto_rule": "renewal_15", "stages": _ACTIVE_STAGES, "band": (6, 15)},
    {"auto_rule": "renewal_5", "stages": _ACTIVE_STAGES, "band": (0, 5)},
    # UNCHANGED FROM 0104, and kept deliberately. A Lost lead is re-approached
    # once, 30 days out: re-approaching someone who already said no is a longer
    # conversation, and their own renewal is the only reason it is worth having
    # again. Its note is fixed rather than derived — the message is about their
    # renewal approaching, not about a countdown.
    #
    # It queues NO WhatsApp: 0118's trigger lists the three ladder rungs and
    # not this one.
    {
        "auto_rule": "renewal_lost",
        "stages": ("Lost",),
        "band": (30, 30),
        "note": "Renewal approaching — they may be open to a quote now",
    },
)

# renewal_pending (5 days, stage Pending) is retired: the ladder's 5-day rung
# covers Pending already, and two rules firing on the same lead on the same day
# would be two reminders for one fact. Rows already carrying it keep displaying
# — 0118's constraint still accepts the name.


def _note(delta: int) -> str:
    """What the reminder says, built from the REAL gap.

    NOT the rung's nominal name. A catch-up rung firing at 12 days must not
    tell the broker 15 — they are looking at the same date it is.
    """
    if delta == 0:
        return "Renewal is today — last chance to quote"
    if delta == 1:
        return "Renewal tomorrow — last chance to quote"
    return f"Renewal in {delta} days — worth a call"

# When a system-set reminder lands. NOT the hour the sweep happened to run: it
# runs hourly, and a reminder created at 03:17 should not be due at 03:17. A
# broker's day starts at nine.
_DUE_HOUR = 9

# Hourly, and deliberately not daily. A once-a-day sweep has a one-day window,
# so a lead moved to Pending at 10am — after the 1am run — is four days out by
# the next one and the five-day rule never fires at all. Hourly catches it the
# same morning, and the unique index (0104) makes a re-run free.
_SWEEP_INTERVAL_SECONDS = 60 * 60


def _principal_renewal_date(lead: dict) -> date | None:
    """The renewal date off this lead's principal driver, if it has one.

    driver arrives as a list even though at most one row is principal —
    PostgREST does not know idx_driver_one_principal_per_lead exists.
    """
    for driver in lead.get("driver") or []:
        if driver.get("is_principal") and driver.get("renewal_date"):
            return date.fromisoformat(str(driver["renewal_date"])[:10])
    return None


def sweep() -> dict:
    """Create the renewal reminders that are due to exist today.

    IDEMPOTENT, and that is the whole design. Every insert goes through
    repo.insert_auto, which upserts on (lead_id, auto_rule,
    auto_for_renewal_date) and ignores duplicates — so running this every hour
    creates nothing after the first time, and re-running it after a restart
    cannot recreate a reminder the broker has already dismissed.

    WHOSE REMINDER IT IS comes from lead.assigned_broker_id, read per lead.
    There is no session here, and caching a broker id would be wrong the moment
    a lead is reassigned — 0076 moves open reminders with the lead, so the
    holder can change between sweeps.

    Returns a small tally rather than logging alone, so the caller can say what
    happened without re-querying.
    """
    today = datetime.now(_TZ).date()
    due_at = datetime.combine(
        today, time(hour=_DUE_HOUR), tzinfo=_TZ
    ).isoformat()

    created = 0
    skipped_no_broker = 0
    skipped_opted_out = 0

    for rule in _RULES:
        lo, hi = rule["band"]
        rows = repo.leads_due_for_renewal_reminder(list(rule["stages"])).data or []
        for lead in rows:
            renewal = _principal_renewal_date(lead)
            if renewal is None:
                # No date on file. Most of the book is in this state, and a
                # rule that guessed would be worse than one that stays quiet.
                continue

            delta = (renewal - today).days
            if not lo <= delta <= hi:
                continue

            if lead.get("renewal_reminders_off"):
                # The broker tapped Stop Reminder on an earlier rung (0119).
                # Checked here so no further rung is even created; the drain
                # checks again for rows queued before the tap landed.
                skipped_opted_out += 1
                continue

            broker_id = lead.get("assigned_broker_id")
            if not broker_id:
                # Cannot happen today — every lead is assigned — but the
                # column is nullable, and one unassigned row must not stop the
                # rest of the sweep.
                skipped_no_broker += 1
                continue

            try:
                result = repo.insert_auto(
                    {
                        "lead_id": lead["id"],
                        "broker_id": broker_id,
                        "kind": "Renewal",
                        "due_at": due_at,
                        # A fixed note where the rule carries one (renewal_lost
                        # speaks about the renewal, not a countdown), else the
                        # real gap — never the rung's nominal name.
                        "note": rule.get("note") or _note(delta),
                        "auto_rule": rule["auto_rule"],
                        "auto_for_renewal_date": renewal.isoformat(),
                    }
                )
                created += len(result.data or [])
            except Exception:
                # Per lead, not per sweep. One bad row — a lead deleted
                # mid-run, a broker deactivated — must not cost every other
                # reminder this hour.
                logger.exception(
                    "Could not create the %s reminder for lead %s",
                    rule["auto_rule"],
                    lead["id"],
                )

    if created or skipped_no_broker or skipped_opted_out:
        logger.info(
            "Reminder sweep: created %d, skipped %d unassigned, %d opted out",
            created,
            skipped_no_broker,
            skipped_opted_out,
        )
    return {
        "created": created,
        "skipped_no_broker": skipped_no_broker,
        "skipped_opted_out": skipped_opted_out,
    }


async def run_sweep_forever() -> None:
    """Set renewal reminders, once at startup and then every hour.

    NO 'ALREADY RAN TODAY' STATE. An earlier design tracked the last date it
    swept and skipped the rest of the day, which loses any lead whose stage
    changes after the run — see _SWEEP_INTERVAL_SECONDS. The unique index makes
    repetition free, so there is nothing to remember and nothing to get wrong
    across a restart.

    IMMEDIATELY, THEN SLEEP, unlike the other loops in this codebase: a deploy
    should not delay today's reminders by an hour.

    to_thread because everything below it is a blocking Supabase call. Running
    it on the event loop would stall every in-flight request for the duration —
    the same lesson routes/dash.py records about run_in_threadpool.
    """
    while True:
        try:
            await asyncio.to_thread(sweep)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let one failure end the loop. The next hour is a fresh
            # attempt, and the index means it will not double-create.
            logger.exception("Reminder sweep failed")
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)


def _flatten(row: dict) -> dict:
    """Lift the principal driver's renewal date onto the lead summary.

    The embed returns lead(… driver(…)) because renewal_date lives on driver
    (0098) while stage lives on lead. The screen wants one flat customer, so
    the nesting is undone here rather than in the component.
    """
    lead = row.get("lead")
    if lead:
        lead = {**lead}
        lead["renewal_date"] = (
            _principal_renewal_date(lead).isoformat()
            if _principal_renewal_date(lead)
            else None
        )
        lead.pop("driver", None)
        row = {**row, "lead": lead}
    return row


def list_mine(broker_id: str) -> list[dict]:
    """Every open reminder this broker holds — the Reminders page.

    NOT my_day(), which stops at end-of-today because it answers "what is due
    now". This is a worklist: a follow-up set for next month is still owed, and
    a page that hid it would send the broker back to guessing.
    """
    rows = repo.list_all_open_for_broker(broker_id).data or []
    return [_flatten(row) for row in rows]


def _ics_escape(text: str) -> str:
    """iCalendar's own escaping (RFC 5545 §3.3.11).

    Backslash first — escaping it after the others would double-escape the
    backslashes they just introduced. A raw comma or semicolon in a note would
    otherwise split the field and produce a file some calendars silently refuse
    to import.
    """
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def to_ics(reminder_id: str) -> str:
    """One follow-up as an .ics file, for the Add to calendar button.

    Generated on the fly rather than stored — everything it needs is already on
    the row. No calendar sync, no OAuth, no per-broker setup: the file lands in
    whichever calendar the broker actually uses.

    UID is the reminder's own id, so re-adding after a snooze UPDATES the
    existing event in most calendars instead of creating a duplicate.
    """
    reminder = _get_open_or_404(reminder_id)
    lead = reminder.get("lead") or {}

    due = datetime.fromisoformat(reminder["due_at"]).astimezone(ZoneInfo("UTC"))
    stamp = "%Y%m%dT%H%M%SZ"

    # A follow-up is a moment, not a span, but calendars need an end — 15
    # minutes reads as a task rather than blocking out an hour of the day.
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MyDay CRM//Follow-up//EN",
        "BEGIN:VEVENT",
        f"UID:{reminder['id']}@myday-crm",
        f"DTSTAMP:{datetime.now(ZoneInfo('UTC')).strftime(stamp)}",
        f"DTSTART:{due.strftime(stamp)}",
        f"DTEND:{(due + timedelta(minutes=15)).strftime(stamp)}",
        f"SUMMARY:{_ics_escape(f'{reminder['kind']}: {lead.get('name') or 'Lead'}')}",
        f"DESCRIPTION:{_ics_escape(reminder['note'])}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    # CRLF, not \n — RFC 5545 requires it, and Outlook is the one that actually
    # rejects a file without it.
    return "\r\n".join(lines) + "\r\n"
