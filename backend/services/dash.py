"""Business logic for reading a DASH report. Owns every HTTPException here.

The three pieces below it each do one thing — dash_azure reads the PDF,
dash_parse finds the fields, dash_apply writes the blanks — so this is only
the ownership guard, the order, and turning a failure into something a broker
can act on.
"""

import logging
from uuid import uuid4

from fastapi import HTTPException

import supabase_storage
from repositories import driver as driver_repo
from repositories import lead as lead_repo
from services import dash_apply, dash_azure

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

    WHY A COPY EXISTS AT ALL. The PDF used to be read and dropped, so a parse
    was one-shot: an Azure timeout, a bad scan or an outage lost the upload and
    the broker started over with the same file. The copy is what makes the
    report re-readable, and it is why this runs BEFORE dash_azure rather than
    after — storing only on a successful parse would keep exactly the reports
    that never needed keeping.

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
    driver = _driver_on_my_lead(driver_id, broker_id)
    path = driver.get("dash_report_path")
    if not path:
        raise HTTPException(status_code=404, detail="No DASH report has been uploaded yet")

    try:
        return supabase_storage.signed_url(path, bucket=supabase_storage.DASH_BUCKET)
    except supabase_storage.StorageError as err:
        logger.exception("Could not sign the DASH report for driver %s", driver_id)
        raise HTTPException(status_code=502, detail="Could not open that report") from err


def apply_pdf(driver_id: str, broker_id: str, pdf_bytes: bytes) -> dict:
    """Read a DASH PDF and fill this driver's blank fields.

    THE GUARD COMES FIRST, before a page is spent on Azure: a broker may only
    touch a driver on a lead they currently hold. Reading the document for
    someone else's customer and then refusing to save it would still have sent
    that customer's report to a third party.
    """
    driver = _driver_on_my_lead(driver_id, broker_id)

    # AFTER THE GUARD, BEFORE AZURE. After, because a broker must not be able
    # to store a report for a customer they do not hold. Before, because that
    # is the whole point: a parse that times out still leaves the copy.
    _store_copy(driver, pdf_bytes)

    try:
        markdown = dash_azure.read_pdf(pdf_bytes)
    except RuntimeError as err:
        # Missing credentials — a deployment problem, not the broker's.
        logger.error("DASH reading is not configured: %s", err)
        raise HTTPException(
            status_code=503,
            detail="Reading DASH reports is not set up yet — the broker can still type the details",
        )
    except TimeoutError:
        # Azure took longer than dash_azure's deadline. Said separately from
        # the failure below because it is a different fact and a different
        # action: the report is probably fine and trying again may work,
        # whereas "could not be read" invites the broker to give up on it.
        logger.warning("Azure timed out reading a DASH report for driver %s", driver_id)
        raise HTTPException(
            status_code=504,
            detail="Reading that report is taking too long — try again, or type the details",
        )
    except Exception as err:
        logger.exception("Azure could not read a DASH report for driver %s", driver_id)
        raise HTTPException(
            status_code=502,
            detail=f"That report could not be read: {err}",
        )

    if not markdown.strip():
        # A scanned or empty PDF. Not an error — the form is simply left for
        # the broker, which is what they would have done anyway.
        return dash_apply.empty_result()

    return dash_apply.apply_report(driver["id"], markdown)


def _driver_on_my_lead(driver_id: str, broker_id: str) -> dict:
    """The driver, if this broker currently holds their lead.

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

    return driver
