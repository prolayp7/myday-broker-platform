"""Business logic for reading a DASH report. Owns every HTTPException here.

The pieces below it each do one thing — dash_text takes the text out of the PDF
(dash_azure for a scan), dash_claude reads it (dash_parse if Claude fails),
dash_apply writes it — so this is only the ownership guard, the wrong-lead
check, the order, and turning a failure into something a broker can act on.
"""

import hashlib
import logging
import time
from datetime import date
from uuid import uuid4

from fastapi import HTTPException

import supabase_storage
from repositories import dash_history as history_repo
from repositories import driver as driver_repo
from repositories import lead as lead_repo
from services import dash_apply, dash_azure, dash_claude, dash_rules, dash_text
from services import dash_rules as rules

logger = logging.getLogger("myday.dash")


def _object_path(driver_id: str) -> str:
    """Where this driver's report belongs in the bucket.

    DRIVER-FIRST, so removing one person's report is a prefix delete — the
    same reasoning broker_document._object_path gives for keying by broker.

    RANDOM SUFFIX so a replacement never writes over the file it replaces. The
    new object is confirmed written and the row repointed before the old one
    is deleted, so an upload that fails halfway leaves the existing good copy
    untouched; with a fixed path it would already have been overwritten by a
    partial file.
    """
    return f"{driver_id}/{uuid4().hex}.pdf"


def _store_copy(driver: dict, pdf_bytes: bytes) -> None:
    """Keep a copy of the report, and never let that break the parse.

    WHY A COPY EXISTS AT ALL. The copy is what makes a report re-readable after
    an Azure timeout, a bad scan or an outage — so it is stored when reading
    FAILS as well as when it succeeds. The one time it is not stored is a
    report the broker cancelled as belonging to someone else.

    EVERY FAILURE HERE IS SWALLOWED. This is an optional extra beside the job
    the broker actually asked for; a bucket problem must not turn a report
    Azure read perfectly into a 502 with no fields filled. Each case is
    handled on its own terms rather than under one blanket except:

      upload fails          -> log, carry on. No copy, everything else works.
      row update fails      -> DELETE the object first. It is written but
                               nothing points at it, and an orphan in a
                               private bucket is a customer's claims history
                               with no owner.
      row update writes 0   -> the same. driver_repo.update returns a result
         rows                 rather than raising when no row matches, so a
                              silent no-op has to be caught by looking.
      old object delete     -> log only. The new copy is already stored and
                               pointed at; failing the request because a
                               SUPERSEDED file lingers would report a working
                               upload as broken.
    """
    driver_id = str(driver["id"])
    previous = driver.get("dash_report_path")
    path = _object_path(driver_id)

    try:
        supabase_storage.upload(
            path, pdf_bytes, "application/pdf", bucket=supabase_storage.DASH_BUCKET
        )
    except supabase_storage.StorageError:
        logger.exception("Could not store the DASH copy for driver %s", driver_id)
        return

    try:
        result = driver_repo.update(driver_id, {"dash_report_path": path})
        wrote = bool(getattr(result, "data", None))
    except Exception:
        logger.exception("Stored a DASH copy for driver %s but could not record it", driver_id)
        wrote = False

    if not wrote:
        # Nothing points at the object just written, so remove it rather than
        # leave a file no record will ever mention.
        try:
            supabase_storage.delete(path, bucket=supabase_storage.DASH_BUCKET)
        except supabase_storage.StorageError:
            logger.exception("Could not clean up the unrecorded DASH copy %s", path)
        return

    if previous:
        try:
            supabase_storage.delete(previous, bucket=supabase_storage.DASH_BUCKET)
        except supabase_storage.StorageError:
            logger.exception(
                "Replaced the DASH copy for driver %s but could not delete %s",
                driver_id,
                previous,
            )


def report_url(driver_id: str, broker_id: str) -> str:
    """A short-lived link to this driver's stored report.

    NOT swallowed, unlike _store_copy. A broker who clicks Download and gets
    silence has no way to tell whether the report exists — so a storage
    failure here is a real error, and a driver with no report is a 404.
    """
    driver, _ = _driver_on_my_lead(driver_id, broker_id)
    path = driver.get("dash_report_path")
    if not path:
        raise HTTPException(status_code=404, detail="No DASH report has been uploaded yet")

    try:
        return supabase_storage.signed_url(path, bucket=supabase_storage.DASH_BUCKET)
    except supabase_storage.StorageError as err:
        logger.exception("Could not sign the DASH report for driver %s", driver_id)
        raise HTTPException(status_code=502, detail="Could not open that report") from err


def apply_pdf(
    driver_id: str, broker_id: str, pdf_bytes: bytes, confirm: bool = False
) -> dict:
    """Read a DASH PDF and fill this driver.

    THE GUARD COMES FIRST, before anything is sent out: a broker may only
    touch a driver on a lead they currently hold. Reading the document for
    someone else's customer and then refusing to save it would still have sent
    that customer's report to a third party.

    `confirm` is the broker's "Use it anyway" after a wrong-lead warning.
    """
    driver, lead = _driver_on_my_lead(driver_id, broker_id)
    key = (str(driver["id"]), hashlib.sha256(pdf_bytes).hexdigest())

    # "Use it anyway" on the SAME report reuses the reading the warning was
    # based on: instant, and no second Azure and Claude charge.
    report = _take_pending(key) if confirm else None
    if report is None:
        text = dash_text.read_pdf(pdf_bytes)
        read_by_azure = not text
        if read_by_azure:
            text = _read_text(driver, pdf_bytes)
        if not text.strip():
            # An empty PDF, or a scan Azure found nothing in. Not an error —
            # the form is left for the broker, which is what they would have
            # done anyway.
            _store_copy(driver, pdf_bytes)
            return dash_apply.empty_result()

        try:
            report = dash_apply.from_claude(
                dash_claude.read_report(text, lead.get("name"))
            )
        except dash_claude.DashClaudeError as err:
            logger.warning("DASH for driver %s read by fallback: %s", driver_id, err)
            # The regex parser is built on Azure's markdown, so Azure runs
            # here only when it has not already.
            markdown = text if read_by_azure else _read_text(driver, pdf_bytes)
            report = dash_apply.from_fallback(markdown)

    if not confirm:
        mismatch = _lead_mismatch(driver, lead, report)
        if mismatch:
            # Nothing saved, no copy kept. The screen asks the broker and, on
            # "Use it anyway", sends the same report again with confirm.
            _keep_pending(key, report)
            raise HTTPException(status_code=409, detail=mismatch)

    _store_copy(driver, pdf_bytes)
    return dash_apply.apply_report(driver["id"], report)


def _read_text(driver: dict, pdf_bytes: bytes) -> str:
    """Azure's text of the report. Every failure keeps the copy, so the
    report can be read again later without the broker uploading it twice."""
    driver_id = driver["id"]
    try:
        return dash_azure.read_pdf(pdf_bytes)
    except RuntimeError as err:
        # Missing credentials — a deployment problem, not the broker's.
        _store_copy(driver, pdf_bytes)
        logger.error("DASH reading is not configured: %s", err)
        raise HTTPException(
            status_code=503,
            detail="Reading DASH reports is not set up yet — the broker can still type the details",
        )
    except TimeoutError:
        # Said separately from the failure below: the report is probably fine
        # and trying again may work, whereas "could not be read" invites the
        # broker to give up on it.
        _store_copy(driver, pdf_bytes)
        logger.warning("Azure timed out reading a DASH report for driver %s", driver_id)
        raise HTTPException(
            status_code=504,
            detail="Reading that report is taking too long — try again, or type the details",
        )
    except Exception as err:
        _store_copy(driver, pdf_bytes)
        logger.exception("Azure could not read a DASH report for driver %s", driver_id)
        raise HTTPException(status_code=502, detail=f"That report could not be read: {err}")


def _lead_mismatch(driver: dict, lead: dict, report: dict) -> dict | None:
    """Why this report may belong to someone else, or None if it fits.

    Licence: exact, when the lead already has one. Name: Claude's judgement,
    because a Meta lead-form name is often a nickname, a first name or a typo
    — a word match would cry wolf. The fallback reader has no name judgement,
    so only the licence is checked then.
    """
    reason = None
    on_lead = rules.normalize_dln(driver.get("license_number"))
    on_report = rules.normalize_dln(report.get("subject_license_number"))
    if on_lead and on_report and on_lead != on_report:
        reason = "The licence number on this lead is different"
    else:
        match = report.get("lead_match") or {}
        if lead.get("name") and match.get("result") in ("mismatch", "unsure"):
            reason = match.get("reason") or "The names don't match"
    if not reason:
        return None
    return {
        "code": "dash_lead_mismatch",
        "subject_name": report.get("subject_name"),
        "subject_license_number": report.get("subject_license_number"),
        "lead_name": lead.get("name"),
        "reason": reason,
    }


# Readings held while the broker answers a wrong-lead warning, keyed by driver
# and the report's hash. In memory: a restart only means "Use it anyway" reads
# the report again, which is what it did before this existed.
_PENDING: dict[tuple[str, str], tuple[float, dict]] = {}
_PENDING_SECONDS = 600


def _keep_pending(key: tuple[str, str], report: dict) -> None:
    now = time.monotonic()
    for old in [k for k, (at, _) in _PENDING.items() if now - at > _PENDING_SECONDS]:
        _PENDING.pop(old, None)
    _PENDING[key] = (now, report)


def _take_pending(key: tuple[str, str]) -> dict | None:
    held = _PENDING.pop(key, None)
    if held and time.monotonic() - held[0] <= _PENDING_SECONDS:
        return held[1]
    return None


def dismiss_notes(driver_id: str, broker_id: str) -> None:
    """The broker's Dismiss on "Check these"."""
    _driver_on_my_lead(driver_id, broker_id)
    driver_repo.update(driver_id, {"dash_notes": None})


def set_claim_left_out(driver_id: str, broker_id: str, claim_id: str, left_out: bool) -> None:
    """The broker's "Leave out" / "Undo" on a claim."""
    _driver_on_my_lead(driver_id, broker_id)
    result = history_repo.set_left_out(driver_id, claim_id, left_out)
    if not (getattr(result, "data", None) or []):
        raise HTTPException(status_code=404, detail="Claim not found")


def history(driver_id: str, broker_id: str) -> dict:
    """The gaps and claims saved from this driver's last full DASH reading."""
    _driver_on_my_lead(driver_id, broker_id)
    gaps = history_repo.list_gaps(driver_id).data or []
    claims = history_repo.list_claims(driver_id).data or []
    today = date.today()
    for gap in gaps:
        gap["days"] = (date.fromisoformat(gap["gap_to"]) - date.fromisoformat(gap["gap_from"])).days
        # A gap counts from the day it ended, the same as the claim's date of loss.
        gap["past_window"] = not dash_rules.within_years(
            date.fromisoformat(gap["gap_to"]), today, dash_apply.GAP_YEARS
        )
    for claim in claims:
        claim["past_window"] = not dash_rules.within_years(
            date.fromisoformat(claim["date_of_loss"]), today, dash_apply.CLAIM_YEARS
        )
    return {"gaps": gaps, "claims": claims}


def _driver_on_my_lead(driver_id: str, broker_id: str) -> tuple[dict, dict]:
    """The driver and their lead, if this broker currently holds the lead.

    Two hops rather than one, because driver carries lead_id and the lead
    carries the holder — there is no single row saying "this broker may touch
    this driver".
    """
    rows = driver_repo.get_by_id(driver_id).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Driver not found")
    driver = rows[0]

    leads = lead_repo.get_by_id(str(driver["lead_id"])).data or []
    if not leads:
        raise HTTPException(status_code=404, detail="Driver not found")

    if str(leads[0].get("assigned_broker_id")) != str(broker_id):
        raise HTTPException(status_code=403, detail="You do not currently hold this lead")

    return driver, leads[0]
