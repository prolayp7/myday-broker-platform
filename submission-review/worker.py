"""The one-minute loop: mailbox, read, match, review, send.

  python worker.py          every minute until stopped
  python worker.py --once   a single pass

Each stage picks up what the last one left in the tables, so a crash resumes
where it stopped, and a saved reading is never paid for twice. Errors record the
exception type only: their text can quote a customer's document.
"""

import hashlib
import sys
import time

import application
import azure_text
import checks
import dash
import identify
import mail
import matching
import mvr
import pdf_text
import settings
import store

_MAX_ATTEMPTS = 3
_OPEN_FILE_DAYS = 90
_POLL_SECONDS = 60
_SENDER_DOMAIN = "fairstarinsurance.ca"

_STATUS_WORDS = {
    "review_complete": "Review complete",
    "needs_manual_review": "Needs manual review",
}


def _log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


def manual_review(reason: str, email_id=None, document_id=None, submission_id=None) -> dict:
    return store.insert("manual_review", {
        "reason": reason, "email_id": email_id,
        "document_id": document_id, "submission_id": submission_id,
    })


def notify(kind: str, to_address: str, subject: str, body: str, **links) -> None:
    store.insert("notification", {
        "kind": kind, "to_address": to_address, "subject": subject, "body": body, **links,
    })


# 1. Mailbox -----------------------------------------------------------------


def allowed_sender(address: str) -> bool:
    """Saved on MyDay's Admin page, Review senders. The address is compared as
    it is stored, so a lookalike domain can never match."""
    address = address.lower()
    return _is_fairstar(address) and bool(
        store.select("broker", "id", email=address, approved=True)
    )


def _is_fairstar(address: str) -> bool:
    return address.lower().rpartition("@")[2] == _SENDER_DOMAIN


def take_email(message: dict) -> None:
    row = store.save_email(message)
    try:
        accepted = message["pdfs"] and _take_attachments(row, message)
        store.update("email", row["id"], process_status="done")
    except Exception as err:
        attempts = row["attempts"] + 1
        status = "failed" if attempts >= _MAX_ATTEMPTS else "pending"
        store.update("email", row["id"], attempts=attempts, process_status=status,
                     last_error=type(err).__name__)
        if status == "failed":
            manual_review("This email could not be saved", email_id=row["id"])
        return
    first_name = message.get("first_name", "Broker")
    if accepted:
        reply = received_email(message["subject"], first_name)
    elif message["pdfs"] and _is_fairstar(message["from_address"]) and message.get("authenticated"):
        # Colleagues only. Answering any address would answer spam and no-reply
        # senders, which can loop, and would show strangers the mailbox is live.
        # A forged From line gets nothing: the reply would land on the real person.
        reply = refused_email(message["subject"], first_name)
    else:
        return
    # Sent now, before the slow reading starts. Only a courtesy, so a failure is
    # not retried: taking the email again would store a resent PDF a second
    # time and send a second result.
    try:
        mail.send(message["from_address"], *reply)
    except Exception as err:
        _log(f"reply not sent: {type(err).__name__}")


def received_email(subject: str, first_name: str) -> tuple[str, str]:
    return f"Received: {subject}", (
        f"Dear {first_name},\n\n"
        "We have received your email and are reviewing it. "
        "Please give us a minute or 2 to review the file.\n\n"
        "Regards,\n"
        "Review Team"
    )


def refused_email(subject: str, first_name: str) -> tuple[str, str]:
    return f"Not authorised: {subject}", (
        f"Dear {first_name},\n\n"
        "Your email address is not authorised to send files for review, "
        "so this email has not been reviewed. "
        "Please ask your administrator to add your address.\n\n"
        "Regards,\n"
        "Review Team"
    )


def _take_attachments(row: dict, message: dict) -> bool:
    """True when the email's PDFs were accepted for review."""
    if not allowed_sender(message["from_address"]):
        # Nothing is stored or read.
        manual_review("The sender is not on the approved list", email_id=row["id"])
        return False
    if not message.get("authenticated"):
        # An approved address on a From line proves nothing by itself.
        manual_review("The sender could not be verified", email_id=row["id"])
        return False
    # Each address is its own broker, with its own files.
    existing ={d["sha256"] for d in store.select("document", "sha256", email_id=row["id"])}
    for filename, pdf in message["pdfs"]:
        sha256 = store.fingerprint(pdf)
        if sha256 in existing:
            continue
        existing.add(sha256)
        path = f"{row['id']}/{sha256}.pdf"
        store.put_pdf(path, pdf)
        store.insert("document", {
            "email_id": row["id"], "filename": filename, "storage_path": path,
            "sha256": sha256, "process_status": "stored",
        })
    return True


# 2. Reading -----------------------------------------------------------------


def _read(document: dict) -> tuple[str, str | None, dict | None, list[str], dict | None]:
    """(type, licence, reading, problems, rows). Problems mean a failed read."""
    pdf = store.get_pdf(document["storage_path"])
    pages = pdf_text.read_pages(pdf)
    has_text = pdf_text.has_text(pages)
    text = pdf_text.plain_text(pdf) if has_text else azure_text.read_pdf(pdf)
    kind = identify.document_type(text)
    if kind == "other":
        return "other", None, None, ["The document type is not recognised"], None
    if kind == "application" and not has_text:
        return kind, None, None, ["This PDF is a scan with no text"], None
    # Azure can read a picture. Text on the other pages would hide that one.
    if kind != "application" and has_text and pdf_text.has_picture_page(pages):
        text = azure_text.read_pdf(pdf)

    # A PDF we have read before is not paid for twice.
    saved = document.get("raw") or store.saved_reading(document["sha256"])
    if kind == "application":
        parsed = saved or application.read_application(pdf)
        problems, unclear = application.self_check(parsed, pages)
        parsed["_unclear"] = unclear
        rows = None if problems else application.to_rows(parsed)
        licence = application._licence(parsed["drivers"][0].get("licence_no")) if parsed["drivers"] else None
    elif kind == "dash":
        parsed = saved or dash.read_dash(text)
        problems, warnings = dash.self_check(parsed, text)
        parsed["_warnings"] = warnings
        rows = None if problems else dash.to_rows(parsed)
        licence = dash.licence(parsed.get("licence_no"))
    else:
        parsed = saved or mvr.read_mvr(text)
        problems = mvr.self_check(parsed, text)
        rows = None if problems else mvr.to_rows(parsed)
        licence = mvr._licence(parsed.get("licence_no"))
    # Saved first: a crash below must not pay for this reading again.
    store.update("document", document["id"], raw=parsed, doc_type=kind)
    return kind, licence or None, parsed, problems, rows


def read_document(document: dict) -> None:
    try:
        kind, licence, _, problems, rows = _read(document)
        store.clear_readings(document["id"])
        if not problems:
            if kind == "application":
                store.save_application(document["id"], rows)
            elif kind == "dash":
                store.save_dash(document["id"], *rows)
            else:
                store.save_mvr(document["id"], *rows)
        store.update(
            "document", document["id"], doc_type=kind, licence_no=licence,
            read_status="failed" if problems else "ok",
            read_error="; ".join(problems) or None, process_status="read",
        )
    except Exception as err:
        attempts = document["attempts"] + 1
        values = {"attempts": attempts, "last_error": type(err).__name__}
        if attempts >= _MAX_ATTEMPTS:
            values.update(read_status="failed", process_status="read",
                          read_error=f"Could not read after {attempts} tries")
        store.update("document", document["id"], **values)


# 3. Matching ----------------------------------------------------------------


def _licences(document: dict) -> set[str]:
    if document["doc_type"] == "application":
        return {application._licence(d.get("licence_no")) for d in document["raw"]["drivers"]}
    return {document["licence_no"]}


def _attach(document: dict, submission_id: str) -> None:
    same = store.select("document", "id", submission_id=submission_id, sha256=document["sha256"])
    if same:
        # The same PDF again keeps the document we have, but every send gets
        # a fresh review and a fresh result.
        store.clear_readings(document["id"])
        store._db().table("document").delete().eq("id", document["id"]).execute()
        store.update("email", document["email_id"], submission_id=submission_id)
        store.mark_outdated(submission_id)
        return
    store.retire_slot(submission_id, document["doc_type"], document["licence_no"])
    store.update("document", document["id"], submission_id=submission_id, is_latest=True,
                 match_status="matched", process_status="done")
    store.update("email", document["email_id"], submission_id=submission_id)
    store.mark_outdated(submission_id)
    if document["doc_type"] == "application":
        parsed = document["raw"]
        store.update("submission", submission_id, customer_name=parsed.get("applicant_name"),
                     effective_date=parsed.get("effective_date"))


def _to_person(document: dict, reason: str, sender: str, file=None, kind="manual_review") -> None:
    submission_id = file["id"] if file else None
    if submission_id and store.select("document", "id", submission_id=submission_id,
                                      sha256=document["sha256"]):
        # The same PDF again: the file already has it and its alert.
        store._db().table("document").delete().eq("id", document["id"]).execute()
        return
    store.update("document", document["id"], match_status="manual_review", process_status="done",
                 is_latest=False, submission_id=submission_id)
    if submission_id:
        store.mark_outdated(submission_id)
    manual_review(reason, document_id=document["id"], submission_id=submission_id)
    notify(kind, sender, f"Submission review: {document['filename']}",
           f"{document['filename']} needs a person.\n\n{reason}", submission_id=submission_id)


def match_document(document: dict) -> bool:
    """True when a held document might now find its file."""
    sender = document["email"]["from_address"]
    subject = document["email"]["subject"]
    broker = store.select("broker", email=sender)[0]
    files = store.recent_files(broker["id"], _OPEN_FILE_DAYS)
    ref = matching.reference(subject)

    if document["read_status"] == "failed":
        # Still names its file if it can, so the old result goes out of date.
        fits = [f for f in files if document["licence_no"] in f["licences"]]
        file = next((f for f in files if f["reference"] == ref), None)
        if not file and len(fits) == 1:
            file = fits[0]
        _to_person(document, document["read_error"] or "The read failed", sender, file)
        return False

    action, file, reason = matching.decide(document["doc_type"], _licences(document), ref, files)
    if action == "hold":
        store.update("document", document["id"], match_status="held", process_status="matched")
    elif action == "manual":
        _to_person(document, reason, sender, file)
    elif action == "which_file":
        _to_person(document, reason, sender, kind="which_file")
    elif action == "attach":
        _attach(document, file["id"])
        return document["doc_type"] == "application"
    else:
        created = store.insert("submission", {"broker_id": broker["id"]})
        _attach(document, created["id"])
        return True
    return False


def _release_held() -> None:
    for held in store.select("document", match_status="held"):
        store.update("document", held["id"], match_status="pending", process_status="read")


def match_all() -> None:
    while True:
        documents = store.documents_at("read")
        if not documents:
            return
        # Applications first, so reports in the same email find the new file.
        documents.sort(key=lambda d: d["doc_type"] != "application")
        release = False
        for document in documents:
            release = match_document(document) or release
        if not release:
            return
        _release_held()


# 4. Review ------------------------------------------------------------------


def result_email(submission: dict, status: str, result: checks.Result, unclear: list[str]) -> tuple[str, str]:
    errors = [f for f in result.findings if f["severity"] == checks.ERROR]
    warnings = [f for f in result.findings if f["severity"] == checks.WARNING]
    lines = [f"File: {submission['reference']}", f"Customer: {submission['customer_name']}",
             f"Status: {_STATUS_WORDS[status]}", ""]

    def section(title: str, rows: list[dict]) -> None:
        if not rows:
            return
        lines.append(f"{title} ({len(rows)})")
        for row in rows:
            where = "".join(
                f"{label} {row[key]}: " for label, key in (("Driver", "driver_no"), ("Vehicle", "auto_no"))
                if row[key]
            )
            line = f"- {where}{row['message']}"
            if row["application_value"] is not None:
                line += f" | Application: {row['application_value']}"
            if row["other_value"] is not None:
                line += f" | Other document: {row['other_value']}"
            lines.append(line)
        lines.append("")

    section("Errors", errors)
    section("Warnings", warnings)
    if unclear:
        lines.append(f"Needs a person to check ({len(unclear)})")
        lines.extend(f"- {item}" for item in unclear)
        lines.append("")
    if result.notes:
        lines.append("Notes")
        lines.extend(f"- {note}" for note in result.notes)
        lines.append("")
    # Only a complete review may say this, never one with a failed read.
    if status == "review_complete" and not result.findings:
        lines.append("No findings.")
    subject = f"{submission['reference']} {submission['customer_name']}: {_STATUS_WORDS[status]}"
    return subject, "\n".join(lines)


def _sender_of(document: dict) -> str:
    """The result goes to whoever sent this customer's application."""
    return store.select("email", "from_address", id=document["email_id"])[0]["from_address"]


def _ask_for_missing(submission: dict, missing: list[str], sender: str) -> None:
    fingerprint = hashlib.sha256("\n".join(missing).encode()).hexdigest()
    sent = store.select("notification", "id", submission_id=submission["id"],
                        kind="missing_documents", fingerprint=fingerprint)
    if sent:
        return
    body = "\n".join([f"To finish reviewing {submission['customer_name']}, please send:", ""]
                     + [f"- {line}" for line in missing]
                     + ["", f"Keep {submission['reference']} in the subject."])
    notify("missing_documents", sender, f"{submission['reference']}: documents needed", body,
           submission_id=submission["id"], fingerprint=fingerprint)


def _run(submission: dict, documents: list[dict], app_document: dict, sender: str) -> None:
    run = store.insert("review_run", {
        "submission_id": submission["id"], "document_ids": [d["id"] for d in documents],
    })
    try:
        file = store.load_file(app_document["id"], documents)
        result = checks.run_checks(file)
        for finding in result.findings:
            store.insert("finding", {**finding, "review_run_id": run["id"]})
        unclear = list((app_document["raw"] or {}).get("_unclear", [])) + result.unclear
        for item in store.select("manual_review", submission_id=submission["id"], status="open"):
            if item["reason"].startswith("Unclear:"):
                store.update("manual_review", item["id"], status="resolved", action="replaced by a new run",
                             resolved_by="worker", resolved_at=store.now())
        if unclear:
            manual_review("Unclear: " + ", ".join(unclear), submission_id=submission["id"])
        still_open = store.select("manual_review", "id", submission_id=submission["id"], status="open")
        status = "needs_manual_review" if still_open else "review_complete"
        subject, body = result_email(submission, status, result, unclear)
        store.update("review_run", run["id"], status="complete", finished_at=store.now())
        store.update("submission", submission["id"], status=status, needs_review=False)
        notify("result", sender, subject, body, submission_id=submission["id"], review_run_id=run["id"])
    except Exception as err:
        store.update("review_run", run["id"], status="failed", finished_at=store.now())
        _log(f"review failed for {submission['reference']}: {type(err).__name__}")
        failed = store.select("review_run", "id", submission_id=submission["id"], status="failed")
        if len(failed) >= _MAX_ATTEMPTS:
            store.update("submission", submission["id"], status="needs_manual_review", needs_review=False)
            manual_review("The review could not run", submission_id=submission["id"])


def review_all() -> None:
    # A report whose read failed stays at "stored" for another try. Its sender
    # has sent it, so the file is not short of it yet.
    retrying = {d["email"]["from_address"] for d in store.documents_at("stored")}
    for submission in store.select("submission", needs_review=True):
        documents = store.active_documents(submission["id"])
        app_document = next((d for d in documents if d["doc_type"] == "application"), None)
        if not app_document:
            store.update("submission", submission["id"], status="needs_manual_review")
            continue
        sender = _sender_of(app_document)
        drivers = store.select("app_driver", "driver_no, name, licence_no",
                               application_id=store.select("application", "id", document_id=app_document["id"])[0]["id"])
        slots = {(d["doc_type"], d["licence_no"]) for d in documents}
        missing = matching.missing(drivers, slots)
        if missing:
            store.update("submission", submission["id"], status="waiting_for_documents")
            if sender not in retrying:
                _ask_for_missing(submission, missing, sender)
        else:
            _run(submission, documents, app_document, sender)


# 5. Sending -----------------------------------------------------------------


def send_all() -> None:
    for row in store.select("notification", status="pending"):
        # Pending first, sent after: a crash can repeat an email, never lose one.
        try:
            mail.send(row["to_address"], row["subject"], row["body"])
        except Exception as err:
            # One bad address must not hold back everyone else's results.
            _log(f"send failed: {type(err).__name__}")
            continue
        store.update("notification", row["id"], status="sent", sent_at=store.now())


def one_pass() -> None:
    for message in mail.new_messages(settings.mailbox_since(), store.finished_emails):
        take_email(message)
    for document in store.documents_at("stored"):
        read_document(document)
    match_all()
    review_all()
    send_all()


def main() -> None:
    settings.load()
    store.ensure_bucket()
    while True:
        try:
            one_pass()
        except Exception as err:
            _log(f"pass failed: {type(err).__name__}")
        if "--once" in sys.argv:
            return
        time.sleep(_POLL_SECONDS)


if __name__ == "__main__":
    main()
