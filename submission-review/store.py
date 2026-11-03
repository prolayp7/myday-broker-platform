"""Saves documents to the review schema."""

import hashlib
import os
import re
import threading
from datetime import datetime, timedelta, timezone

# backend/.env is PRODUCTION. Only the server sets REVIEW_ENV=production, so a
# wrong .env on a PC stops here rather than writing into the wrong database.
_STAGING_REF = "igrqkthanulzfjlyrcdm"
_BUCKET = "review-documents"


_local = threading.local()


def _db():
    """One client per thread: sharing one across threads drops the connection
    on Windows (WinError 10035)."""
    from supabase import create_client

    if not hasattr(_local, "db"):
        url = os.environ["SUPABASE_URL"]
        if _STAGING_REF not in url and os.environ.get("REVIEW_ENV") != "production":
            raise RuntimeError("submission-review only runs against staging")
        _local.client = create_client(url, os.environ["SUPABASE_SERVICE_ROLE_KEY"])
        _local.db = _local.client.schema("review")
    return _local.db


def _bucket():
    _db()
    return _local.client.storage.from_(_BUCKET)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_bucket() -> None:
    _db()
    if _BUCKET not in [b.name for b in _local.client.storage.list_buckets()]:
        _local.client.storage.create_bucket(_BUCKET, options={"public": False})


def put_pdf(path: str, pdf: bytes) -> None:
    _bucket().upload(path, pdf, {"content-type": "application/pdf", "upsert": "true"})


def get_pdf(path: str) -> bytes:
    return _bucket().download(path)


def select(table: str, columns: str = "*", **equal) -> list[dict]:
    query = _db().table(table).select(columns)
    for column, value in equal.items():
        query = query.is_(column, "null") if value is None else query.eq(column, value)
    return query.execute().data


def insert(table: str, row: dict) -> dict:
    return _db().table(table).insert(row).execute().data[0]


def update(table: str, row_id: str, **values) -> None:
    _db().table(table).update(values).eq("id", row_id).execute()


def finished_emails(message_ids: list[str]) -> set[str]:
    if not message_ids:
        return set()
    rows = (
        _db().table("email").select("mailbox_message_id")
        .in_("mailbox_message_id", message_ids).neq("process_status", "pending")
        .execute().data
    )
    return {row["mailbox_message_id"] for row in rows}


def save_email(message: dict) -> dict:
    return _db().table("email").upsert(
        {
            "mailbox_message_id": message["message_id"],
            "thread_id": message["thread_id"],
            "direction": "in",
            "from_address": message["from_address"],
            "subject": message["subject"],
            "occurred_at": message["occurred_at"].isoformat(),
        },
        on_conflict="mailbox_message_id",
    ).execute().data[0]


def documents_at(process_status: str) -> list[dict]:
    return (
        _db().table("document").select("*, email(from_address, subject)")
        .eq("process_status", process_status).order("created_at").execute().data
    )


def saved_reading(sha256: str) -> dict | None:
    rows = select("document", "raw", sha256=sha256, read_status="ok")
    return next((row["raw"] for row in rows if row["raw"]), None)


def clear_readings(document_id: str) -> None:
    """A retry must not hit the one-row-per-document rule. Children cascade."""
    for table in ("application", "dash_report", "mvr_report"):
        _db().table(table).delete().eq("document_id", document_id).execute()


def retire_slot(submission_id: str, doc_type: str, licence_no: str | None) -> None:
    query = (
        _db().table("document").update({"is_latest": False})
        .eq("submission_id", submission_id).eq("doc_type", doc_type).eq("is_latest", True)
    )
    if doc_type != "application":
        query = query.eq("licence_no", licence_no)
    query.execute()


def mark_outdated(submission_id: str) -> None:
    """The moment anything changes, the old result stops counting."""
    _db().table("review_run").update({"outdated": True}).eq(
        "submission_id", submission_id).eq("outdated", False).execute()
    update("submission", submission_id, needs_review=True)


def active_documents(submission_id: str) -> list[dict]:
    return select("document", submission_id=submission_id, is_latest=True, read_status="ok")


def recent_files(broker_id: str, days: int) -> list[dict]:
    """This broker's open files, each with the licences on its application."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    files = (
        _db().table("submission").select("id, reference").eq("broker_id", broker_id)
        .gte("created_at", cutoff).execute().data
    )
    for file in files:
        documents = select(
            "document", "raw", submission_id=file["id"], doc_type="application",
            is_latest=True, read_status="ok",
        )
        file["licences"] = {
            re.sub(r"[\s-]", "", driver.get("licence_no") or "").upper()
            for document in documents for driver in (document["raw"] or {}).get("drivers", [])
        }
    return files


def load_file(application_document_id: str, documents: list[dict]) -> dict:
    """One file as checks.run_checks() takes it."""
    application = select("application", document_id=application_document_id)[0]
    application_id = application["id"]
    file = {
        "application": application,
        "drivers": select("app_driver", application_id=application_id),
        "vehicles": select("app_vehicle", application_id=application_id),
        "driver_use": select("app_driver_use", application_id=application_id),
        "claims": select("app_claim", application_id=application_id),
        "convictions": select("app_conviction", application_id=application_id),
        "coverages": select("app_coverage", application_id=application_id),
        "mvrs": {},
        "dashes": {},
    }
    for document in documents:
        if document["doc_type"] == "mvr":
            report = select("mvr_report", document_id=document["id"])[0]
            file["mvrs"][report["licence_no"]] = {
                "report": report, "document_id": document["id"],
                "entries": select("mvr_entry", mvr_report_id=report["id"]),
            }
        elif document["doc_type"] == "dash":
            report = select("dash_report", document_id=document["id"])[0]
            file["dashes"][report["licence_no"]] = {
                "report": report, "document_id": document["id"],
                "claims": select("dash_claim", dash_report_id=report["id"]),
            }
    return file


def fingerprint(pdf: bytes) -> str:
    return hashlib.sha256(pdf).hexdigest()


def already_read(sha256: str) -> bool:
    rows = (
        _db().table("document").select("id").eq("sha256", sha256)
        .eq("read_status", "ok").limit(1).execute().data
    )
    return bool(rows)


def _email_for_test(sha256: str) -> str:
    """No mailbox yet, so a local test file gets a stand-in email row."""
    row = _db().table("email").upsert(
        {
            "mailbox_message_id": f"local-test:{sha256}",
            "direction": "in",
            "from_address": "local-test",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="mailbox_message_id",
    ).execute().data[0]
    return row["id"]


def save_document(
    filename: str, sha256: str, doc_type: str, raw: dict | None,
    problems: list[str], licence_no: str | None,
) -> str:
    """The document row. A failed reading is kept, marked failed, with why."""
    table = _db().table("document")
    # A re-run replaces an earlier failed attempt; the mvr rows cascade.
    table.delete().eq("sha256", sha256).is_("submission_id", "null").execute()
    row = table.insert({
        "email_id": _email_for_test(sha256),
        "filename": filename,
        "sha256": sha256,
        "doc_type": doc_type,
        "licence_no": licence_no,
        "read_status": "failed" if problems else "ok",
        "read_error": "; ".join(problems) or None,
        "raw": raw,
    }).execute().data[0]
    return row["id"]


def save_application(document_id: str, rows: dict) -> str:
    db = _db()
    saved = db.table("application").insert(
        {**rows["application"], "document_id": document_id}
    ).execute().data[0]
    application_id = saved["id"]
    # Parents first: claims, convictions and coverages point at these rows.
    for table, key in (
        ("app_driver", "drivers"), ("app_vehicle", "vehicles"),
        ("app_driver_use", "driver_use"), ("app_claim", "claims"),
        ("app_conviction", "convictions"), ("app_coverage", "coverages"),
    ):
        if rows[key]:
            db.table(table).insert(
                [{**row, "application_id": application_id} for row in rows[key]]
            ).execute()
    return application_id


def save_dash(document_id: str, report: dict, claims: list[dict]) -> None:
    db = _db()
    saved = db.table("dash_report").insert(
        {**report, "document_id": document_id}
    ).execute().data[0]
    if claims:
        db.table("dash_claim").insert(
            [{**claim, "dash_report_id": saved["id"]} for claim in claims]
        ).execute()


def save_mvr(document_id: str, report: dict, entries: list[dict]) -> None:
    db = _db()
    saved = db.table("mvr_report").insert(
        {**report, "document_id": document_id}
    ).execute().data[0]
    if entries:
        db.table("mvr_entry").insert(
            [{**entry, "mvr_report_id": saved["id"]} for entry in entries]
        ).execute()
