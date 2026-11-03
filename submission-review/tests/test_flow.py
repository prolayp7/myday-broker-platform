"""Matching, email parsing and the result email. No mailbox, no database."""

import os
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import application
import checks
import mail
import matching
import worker

FILES = [
    {"id": "a", "reference": "SR-1001", "licences": {"L1"}},
    {"id": "b", "reference": "SR-1002", "licences": {"L2", "L3"}},
    {"id": "c", "reference": "SR-1003", "licences": {"L3"}},
]


def test_reference_found_in_subject():
    assert matching.reference("Re: sr-1001 Jane Doe") == "SR-1001"
    assert matching.reference("Jane Doe") is None


def test_reference_that_fits_attaches():
    assert matching.decide("mvr", {"L1"}, "SR-1001", FILES)[0] == "attach"


def test_reference_of_another_broker_goes_to_a_person():
    assert matching.decide("mvr", {"L1"}, "SR-9999", FILES)[0] == "manual"


def test_reference_with_the_wrong_licence_goes_to_a_person():
    assert matching.decide("mvr", {"L9"}, "SR-1001", FILES)[0] == "manual"


def test_no_reference_matches_by_licence():
    action, file, _ = matching.decide("dash", {"L1"}, None, FILES)
    assert (action, file["id"]) == ("attach", "a")


def test_two_files_ask_which():
    assert matching.decide("dash", {"L3"}, None, FILES)[0] == "which_file"


def test_new_application_starts_a_file():
    assert matching.decide("application", {"L9"}, None, FILES)[0] == "new"


def test_report_before_its_application_is_held():
    assert matching.decide("mvr", {"L9"}, None, FILES)[0] == "hold"


def test_missing_lists_each_driver_report():
    drivers = [{"driver_no": 1, "name": "A", "licence_no": "L1"},
               {"driver_no": 2, "name": "B", "licence_no": "L2"}]
    assert matching.missing(drivers, {("dash", "L1"), ("mvr", "L1"), ("mvr", "L2")}) == [
        "DASH for driver 2, B"
    ]


def _raw(results=None, sender="Jane Broker <Jane@Example.com>"):
    message = EmailMessage()
    message["From"], message["Subject"], message["Message-ID"] = sender, "Doe", "<1@x>"
    if results:
        message["Authentication-Results"] = results
    message.set_content("see attached")
    message.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="a.pdf")
    message.add_attachment(b"x", maintype="image", subtype="png", filename="b.png")
    return message.as_bytes()


WHEN = datetime(2026, 10, 31, tzinfo=timezone.utc)


def test_parse_keeps_pdfs_only_and_lowers_the_sender():
    parsed = mail.parse(_raw(), WHEN)
    assert parsed["from_address"] == "jane@example.com"
    assert [name for name, _ in parsed["pdfs"]] == ["a.pdf"]


def test_no_results_header_is_not_authenticated():
    assert not mail.parse(_raw(), WHEN)["authenticated"]


def test_dmarc_pass_is_authenticated():
    assert mail.parse(_raw("mx.google.com; dmarc=pass header.from=example.com"), WHEN)["authenticated"]


def test_a_forged_header_from_elsewhere_is_ignored():
    assert not mail.parse(_raw("evil.example; dmarc=pass"), WHEN)["authenticated"]


def test_dkim_for_another_domain_is_not_enough():
    results = "mx.google.com; dkim=pass header.i=@other.com; dmarc=fail"
    assert not mail.parse(_raw(results), WHEN)["authenticated"]


SUBMISSION = {"reference": "SR-1001", "customer_name": "Jane Doe"}


def test_no_findings_only_on_a_complete_review():
    _, body = worker.result_email(SUBMISSION, "review_complete", checks.Result(), [])
    assert "No findings." in body
    _, body = worker.result_email(SUBMISSION, "needs_manual_review", checks.Result(), ["q_fraud"])
    assert "No findings." not in body and "q_fraud" in body


def test_errors_come_before_warnings():
    result = checks.Result()
    result.add(1, "s", checks.WARNING, "later")
    result.add(9, "s", checks.ERROR, "first", application_value=0, other_value=1)
    _, body = worker.result_email(SUBMISSION, "review_complete", result, [])
    assert body.index("first") < body.index("later") and "Application: 0" in body


def test_a_middle_initial_fits_the_full_name():
    assert checks._same_name("Raval, Jignesh D", "RAVAL, JIGNESH, DASHARATHBHAI")
    assert checks._same_name("Raval, Jignesh", "RAVAL, JIGNESH, DASHARATHBHAI")
    assert checks._applicant_is("RAVAL, JIGNESH & RAVAL, NISHABAHEN", "RAVAL, NISHABAHEN, JIGNESH")


def test_a_different_person_does_not_fit():
    assert not checks._same_name("Raval, Jignesh D", "RAVAL, KIRAN, DASHARATHBHAI")
    assert not checks._same_name("Raval, Jignesh D", "RAVAL, JIGNESH, KUMAR")
    assert not checks._same_name("Jignesh", "RAVAL, JIGNESH")


def _conviction_file(row):
    return {
        "application": {"effective_date": "2026-01-29"},
        "drivers": [{"driver_no": 2, "licence_no": "L2"}],
        "convictions": [row],
        "mvrs": {"L2": {"document_id": None, "entries": [
            {"entry_type": "conviction", "entry_date": "2024-03-07"}
        ]}},
    }


def test_conviction_with_no_driver_number_gets_its_own_warning():
    result = checks.Result()
    checks._convictions_rule(_conviction_file({"driver_no": None, "convicted_date": "2024-03-07"}), result)
    assert [f["message"] for f in result.findings] == [
        "An MVR entry is listed with no driver number on the application"
    ]


def test_conviction_under_its_driver_passes():
    result = checks.Result()
    checks._convictions_rule(_conviction_file({"driver_no": 2, "convicted_date": "2024-03-07"}), result)
    assert not result.findings


def test_conviction_absent_is_not_listed():
    result = checks.Result()
    checks._convictions_rule(_conviction_file({"driver_no": 2, "convicted_date": "2020-01-01"}), result)
    assert "not listed" in result.findings[0]["message"]


def test_only_a_saved_fairstar_address_may_send():
    saved = {"jane@fairstarinsurance.ca", "jane@gmail.com"}

    def select(_table, _columns, email, approved):
        return [{"id": "b"}] if approved and email in saved else []

    with patch.object(worker.store, "select", select):
        assert worker.allowed_sender("Jane@FairstarInsurance.ca")
        assert not worker.allowed_sender("other@fairstarinsurance.ca")
        assert not worker.allowed_sender("jane@gmail.com")
        assert not worker.allowed_sender("jane@fairstarinsurance.ca.evil.com")
        assert not worker.allowed_sender("jane@notfairstarinsurance.ca")


def _take(message, monkey_sent):
    saved = (worker.store, worker.mail)
    class Store:
        save_email = staticmethod(lambda m: {"id": "e", "attempts": 0})
        select = staticmethod(lambda *a, **k: [{"sha256": "x"}] if a[0] == "broker" else [])
        insert = staticmethod(lambda *a, **k: {})
        update = staticmethod(lambda *a, **k: None)
        put_pdf = staticmethod(lambda *a: None)
        fingerprint = staticmethod(lambda pdf: "sha")
    class Mail:
        send = staticmethod(lambda to, subject, body: monkey_sent.append((to, subject)))
    worker.store, worker.mail = Store, Mail
    try:
        worker.take_email(message)
    finally:
        worker.store, worker.mail = saved


def test_a_fairstar_pdf_gets_a_confirmation():
    sent = []
    _take({"from_address": "jane@fairstarinsurance.ca", "authenticated": True, "subject": "Doe", "pdfs": [("a.pdf", b"x")]}, sent)
    assert sent == [("jane@fairstarinsurance.ca", "Received: Doe")]


def test_no_confirmation_for_an_outsider_or_mail_with_no_pdf():
    sent = []
    _take({"from_address": "jane@gmail.com", "authenticated": True, "subject": "Doe", "pdfs": [("a.pdf", b"x")]}, sent)
    _take({"from_address": "jane@fairstarinsurance.ca", "authenticated": True, "subject": "Hello", "pdfs": []}, sent)
    assert sent == []


def test_the_same_pdf_again_gets_a_fresh_review():
    calls = []
    deleted = []
    class Query:
        def table(self, name): return self
        def delete(self): return self
        def eq(self, column, value): deleted.append(value); return self
        def execute(self): return self
    class Store:
        select = staticmethod(lambda *a, **k: [{"id": "old"}])
        clear_readings = staticmethod(lambda document_id: None)
        update = staticmethod(lambda *a, **k: None)
        mark_outdated = staticmethod(lambda submission_id: calls.append(submission_id))
        _db = staticmethod(lambda: Query())
    saved = worker.store
    worker.store = Store
    try:
        worker._attach({"id": "new", "sha256": "x", "email_id": "e"}, "file")
    finally:
        worker.store = saved
    assert calls == ["file"] and deleted == ["new"]


def test_the_greeting_uses_the_first_name():
    assert mail.first_name("Eldho George", "x@fairstarinsurance.ca") == "Eldho"
    assert mail.first_name("George, Eldho", "x@fairstarinsurance.ca") == "Eldho"
    assert mail.first_name("", "eldho.george@fairstarinsurance.ca") == "Eldho"
    assert mail.first_name("", "a1@fairstarinsurance.ca") == "Broker"


def test_the_confirmation_is_a_proper_letter():
    subject, body = worker.received_email("Doe", "Eldho")
    assert subject == "Received: Doe"
    assert body.startswith("Dear Eldho,\n\n") and body.endswith("Regards,\nReview Team")


# Which route each document takes to Claude ------------------------------------

WORDS = [{"text": "wording"}] * 60


def _route(kind, pages):
    """Runs worker._read with everything outside it stubbed. What was called, and with what."""
    calls = {"azure": 0, "text": []}

    def read(name):
        def reader(value):
            calls["text"].append((name, value))
            return {"licence_no": "", "drivers": []}
        return reader

    def azure(_pdf):
        calls["azure"] += 1
        return "azure text"

    stubs = [
        patch.object(worker.store, "get_pdf", lambda _path: b"%PDF"),
        patch.object(worker.store, "saved_reading", lambda _sha: None),
        patch.object(worker.store, "update", lambda *_a, **_k: None),
        patch.object(worker.pdf_text, "read_pages", lambda _pdf: pages),
        patch.object(worker.pdf_text, "plain_text", lambda _pdf: "pdf text"),
        patch.object(worker.azure_text, "read_pdf", azure),
        patch.object(worker.identify, "document_type", lambda _text: kind),
        patch.object(worker.dash, "read_dash", read("dash")),
        patch.object(worker.dash, "self_check", lambda _p, _t: ([], [])),
        patch.object(worker.dash, "to_rows", lambda _p: None),
        patch.object(worker.mvr, "read_mvr", read("mvr")),
        patch.object(worker.mvr, "self_check", lambda _p, _t: []),
        patch.object(worker.mvr, "to_rows", lambda _p: None),
        patch.object(worker.application, "read_application", read("application")),
        patch.object(worker.application, "self_check", lambda _p, _t: ([], [])),
        patch.object(worker.application, "to_rows", lambda _p: None),
    ]
    for stub in stubs:
        stub.start()
    try:
        result = worker._read({"storage_path": "p", "sha256": "s", "id": "d", "raw": None})
    finally:
        for stub in stubs:
            stub.stop()
    return calls, result


def test_dash_goes_to_claude_as_text_without_azure():
    calls, _ = _route("dash", [WORDS, WORDS])
    assert calls == {"azure": 0, "text": [("dash", "pdf text")]}


def test_mvr_goes_to_claude_as_text_without_azure():
    calls, _ = _route("mvr", [WORDS])
    assert calls == {"azure": 0, "text": [("mvr", "pdf text")]}


def test_a_report_with_a_picture_page_goes_to_azure():
    for kind in ("dash", "mvr"):
        calls, _ = _route(kind, [WORDS, []])
        assert calls == {"azure": 1, "text": [(kind, "azure text")]}


def test_a_scanned_report_goes_to_azure_once():
    for kind in ("dash", "mvr"):
        calls, _ = _route(kind, [[], []])
        assert calls == {"azure": 1, "text": [(kind, "azure text")]}


def test_an_application_keeps_its_nearly_blank_last_page_away_from_azure():
    calls, _ = _route("application", [WORDS, []])
    assert calls["azure"] == 0
    assert calls["text"] == [("application", b"%PDF")]


def test_a_scanned_application_is_refused():
    calls, result = _route("application", [[], []])
    assert calls["text"] == []
    assert result[3] == ["This PDF is a scan with no text"]


# When sending or reading goes wrong ---------------------------------------------

MESSAGE = {"pdfs": [("a.pdf", b"%PDF")], "from_address": "b@fairstarinsurance.ca", "authenticated": True, "subject": "s"}


def test_a_failed_confirmation_does_not_take_the_email_again():
    updates = []

    def fail(*_args):
        raise OSError

    with patch.object(worker.store, "save_email", lambda _m: {"id": "e", "attempts": 0}), \
            patch.object(worker, "_take_attachments", lambda _r, _m: True), \
            patch.object(worker.store, "update", lambda _t, _i, **v: updates.append(v)), \
            patch.object(worker.mail, "send", fail):
        worker.take_email(MESSAGE)
    assert updates == [{"process_status": "done"}]


def test_one_bad_address_does_not_stop_the_other_results():
    rows = [{"id": n, "to_address": n, "authenticated": True, "subject": "s", "body": "b"} for n in ("bad", "good")]
    marked = []

    def send(to_address, _subject, _body):
        if to_address == "bad":
            raise OSError

    with patch.object(worker.store, "select", lambda *_a, **_k: rows), \
            patch.object(worker.store, "update", lambda _t, row_id, **_v: marked.append(row_id)), \
            patch.object(worker.mail, "send", send):
        worker.send_all()
    assert marked == ["good"]


def _review_with(stored_documents):
    asked = []
    application = {"id": "d", "doc_type": "application", "licence_no": "L1", "email_id": "e"}

    def select(table, *_a, **_k):
        return {"submission": [{"id": "s"}], "email": [{"from_address": "b"}],
                "application": [{"id": "a"}]}.get(table, [])

    with patch.object(worker.store, "documents_at", lambda _s: stored_documents), \
            patch.object(worker.store, "select", select), \
            patch.object(worker.store, "update", lambda *_a, **_k: None), \
            patch.object(worker.store, "active_documents", lambda _s: [application]), \
            patch.object(worker.matching, "missing", lambda _d, _s: ["DASH for driver 1"]), \
            patch.object(worker, "_ask_for_missing", lambda *a: asked.append(a)):
        worker.review_all()
    return asked


def test_a_report_on_its_retry_is_not_asked_for():
    assert not _review_with([{"email": {"from_address": "b"}}])


def test_a_report_nobody_sent_is_asked_for():
    assert _review_with([])
    assert _review_with([{"email": {"from_address": "someone else"}}])


def test_a_use_percent_with_no_vehicle_is_an_incomplete_answer():
    incomplete = application._use_row_incomplete
    assert incomplete([{"driver_no": 2, "auto_no": "", "pct": "100"}])
    assert incomplete([{"driver_no": "", "auto_no": 1, "pct": "100"}])
    assert not incomplete([{"driver_no": 2, "auto_no": 1, "pct": "100"}])
    assert not incomplete([{"driver_no": 1, "auto_no": 1, "pct": ""}])


# A sender who is not on the approved list ---------------------------------------


def _unapproved(address, pdfs=(("a.pdf", b"%PDF"),), send=None):
    sent, updates = [], []
    message = {"pdfs": list(pdfs), "from_address": address, "authenticated": True, "subject": "File", "first_name": "Jane"}

    def capture(to_address, subject, body):
        sent.append((to_address, subject, body))

    with patch.object(worker.store, "save_email", lambda _m: {"id": "e", "attempts": 0}), \
            patch.object(worker.store, "select", lambda *_a, **_k: []), \
            patch.object(worker.store, "insert", lambda *_a, **_k: {}), \
            patch.object(worker.store, "update", lambda _t, _i, **v: updates.append(v)), \
            patch.object(worker.mail, "send", send or capture):
        worker.take_email(message)
    return sent, updates


def test_a_fairstar_address_off_the_list_is_told_it_is_not_authorised():
    sent, updates = _unapproved("jane@fairstarinsurance.ca")
    assert len(sent) == 1
    assert sent[0][0] == "jane@fairstarinsurance.ca"
    assert sent[0][1] == "Not authorised: File"
    assert "not authorised" in sent[0][2]
    assert updates == [{"process_status": "done"}]


def test_an_outside_address_gets_no_reply():
    assert not _unapproved("jane@gmail.com")[0]
    assert not _unapproved("jane@fairstarinsurance.ca.evil.com")[0]


def test_an_email_with_no_pdf_gets_no_reply():
    assert not _unapproved("jane@fairstarinsurance.ca", pdfs=())[0]


def test_a_failed_refusal_is_not_tried_again():
    def fail(*_args):
        raise OSError

    assert _unapproved("jane@fairstarinsurance.ca", send=fail)[1] == [{"process_status": "done"}]


def test_forged_sender_is_not_read_or_answered():
    sent = []
    with patch.object(worker, "allowed_sender", return_value=True), \
            patch.object(worker, "manual_review") as review:
        _take({"from_address": "jane@fairstarinsurance.ca", "authenticated": False,
               "subject": "Doe", "pdfs": [("a.pdf", b"x")]}, sent)
    assert not sent
    assert review.call_args.args[0] == "The sender could not be verified"
