"""Tells an admin ONCE when conversion signals have failed for good.

THE SILENCE THIS BREAKS. A Failed meta_signal row lands on the CAPI dashboard
and waits for someone to have a reason to open it. Nothing provides that reason,
so the failure that matters most — a dead access token, quietly dropping every
conversion queued behind it — gets discovered days later by noticing the campaign
has gone quiet. The WhatsApp channel went 12 days and 77 consecutive failures
before anyone looked.

A CAPI FAILURE IS A REPORTING PROBLEM, NOT AN OPERATIONAL ONE, which is why this
is one message to an admin and NOT the per-lead email that
services/whatsapp_fallback.py sends to brokers. There, a failed notification
means a broker is sitting on a lead they don't know about and every minute counts.
Here, the broker has the lead and is working it; what is lost is Meta's view of
which ads produce real customers. Worth telling someone promptly, not worth
interrupting fifteen people.

EDGE-TRIGGERED, which is the whole design. "Failed rows exist" is true again on
the next drain pass and the one after that, forever, until a human intervenes —
fifteen minutes apart. Migration 0111 made this argument about WhatsApp and acted
on it: "a channel that sends noise is a channel people mute. Losing the real
alerts to habituation is a far worse outcome than missing this one." So this
reports only failures that are NEW since it last spoke, using
meta_signal.failure_alerted_at (0113) as the memory.

READ, SEND, THEN STAMP — deliberately NOT the claim-before-send order that
whatsapp_fallback uses. That module claims each row first because there the harm
is a DUPLICATE email to a broker. Here one message covers N rows and the harm is
a MISSED incident, so the ordering is inverted: if the send fails, nothing is
stamped and the next pass tries again. A crash between the send and the stamp
re-alerts once. One duplicate admin email is a far better failure than a
permanently silenced token expiry.

NEVER RAISES. Called from inside the drain pass, which has rows to finish
sending. An alerter that could break its caller would turn a reporting gap into
a stalled queue.
"""

import logging
import os

import email_client
from repositories import meta_signal as repo

logger = logging.getLogger("myday.meta_signal.alert")

# Who hears about it. Its own variable rather than reusing a broker address:
# these failures are nobody's lead in particular, and the person who can fix a
# dead token or a bad dataset id is whoever runs the integration.
_ALERT_TO = os.environ.get("CAPI_ALERT_EMAIL", "").strip()

# Where an admin lands to see the failures themselves. Configured, not derived —
# the backend has no reliable idea what public URL the frontend is served from,
# and a guessed link that 404s is worse than no link. Same reasoning, and the
# same variable, as services/whatsapp_fallback.py.
_CRM_URL = os.environ.get("CRM_PUBLIC_URL", "").rstrip("/")

# How many failures one message will name individually. Past this the email says
# "and N more" rather than becoming a wall nobody reads — the count is the
# finding at that point, not the individual rows.
_MAX_LISTED = 15

# Meta's own words for the codes worth naming, so the subject line can say what
# is wrong instead of making an admin look it up. Only the ones this system has
# actually seen or can act on; anything else is reported by number.
_CODE_MEANINGS = {
    190: "the access token is dead — replace it, then use Retry dead-token failures",
    100: "a parameter Meta rejected",
    2: "a temporary Meta outage",
}


def _describe(row: dict) -> str:
    """One line per failed signal: who, what, and why."""
    lead = row.get("lead") or {}
    name = lead.get("name") or "(unnamed lead)"
    code = row.get("error_code")
    reason = row.get("error_message") or "no reason recorded"
    code_part = f"code {code}" if code is not None else "no code"
    return f"  - {name} — {row.get('event_name')} — {code_part}: {reason}"


def _compose(rows: list[dict]) -> tuple[str, str]:
    """The subject and body. Returns (subject, text).

    THE SUBJECT CARRIES THE DIAGNOSIS where there is one. A mail saying
    "3 conversion signals failed" prompts a click; one saying the token is dead
    prompts the actual fix, which is the entire point of sending it.
    """
    codes = {r.get("error_code") for r in rows if r.get("error_code") is not None}

    if len(codes) == 1:
        code = next(iter(codes))
        meaning = _CODE_MEANINGS.get(code)
        headline = f"code {code}" if meaning is None else meaning
        subject = f"MyDay CRM: {len(rows)} conversion signal(s) failed — {headline}"
    else:
        subject = f"MyDay CRM: {len(rows)} conversion signal(s) failed"

    lines = [
        f"{len(rows)} conversion signal(s) have failed and will not be retried "
        "automatically.",
        "",
        "WHAT THIS MEANS: the leads themselves are unaffected — brokers have them "
        "and are working them. What is lost is Meta's record of these "
        "conversions, so the ad optimisation is learning from incomplete data "
        "until they are re-sent.",
        "",
    ]

    for row in rows[:_MAX_LISTED]:
        lines.append(_describe(row))
    if len(rows) > _MAX_LISTED:
        lines.append(f"  ... and {len(rows) - _MAX_LISTED} more.")

    if 190 in codes:
        lines += [
            "",
            "A dead token is recoverable in bulk: replace the credential, then "
            "use Retry dead-token failures on the CAPI screen. Every row keeps "
            "its event id, so nothing can be counted twice.",
        ]

    if _CRM_URL:
        lines += ["", f"CAPI dashboard: {_CRM_URL}/admin/capi"]

    lines += [
        "",
        "You are receiving this once per new batch of failures, not once per "
        "pass — a failure already reported is not repeated.",
    ]

    return subject, "\n".join(lines)


def alert_new_failures() -> dict:
    """Send one alert for every terminal failure not yet reported.

    Returns a small summary for the caller to log. NEVER RAISES.
    """
    try:
        rows = repo.list_unalerted_failures().data or []
        if not rows:
            return {"alerted": 0}

        if not email_client.is_configured() or not _ALERT_TO:
            # NOT AN ERROR, and deliberately NOT stamped. An environment without
            # Resend credentials or without CAPI_ALERT_EMAIL simply has no alert
            # channel — the same stance whatsapp_fallback takes. Leaving the rows
            # unstamped means configuring it later still surfaces them, rather
            # than starting from a silence that has already swallowed everything.
            logger.debug(
                "CAPI alerting not configured — %s failure(s) left unreported",
                len(rows),
            )
            return {"alerted": 0, "unconfigured": True}

        subject, text = _compose(rows)

        try:
            email_client.send(to=_ALERT_TO, subject=subject, text=text)
        except email_client.EmailError as err:
            # Nothing stamped, so the next pass tries again. Logged at error
            # because this is the channel that exists to break a silence, and it
            # just failed to.
            logger.error("CAPI failure alert could not be sent: %s", err)
            return {"alerted": 0, "error": str(err)}

        # STAMPED ONLY NOW, and only the ids actually described. A failure that
        # arrived between the read above and this write is untouched, so it
        # alerts on the next pass instead of being silently marked as reported.
        repo.mark_failures_alerted([r["id"] for r in rows])

        logger.info("CAPI failure alert sent for %s signal(s)", len(rows))
        return {"alerted": len(rows)}

    except Exception:
        logger.exception("CAPI failure alerting raised unexpectedly")
        return {"alerted": 0}
