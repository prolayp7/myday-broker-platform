"""Tests for the MVR register's rules: who sees what, who may change what,
duplicates, the derived dates, and the head-office password.

Repositories and Azure are replaced with in-memory fakes, so nothing here
touches a database or spends an Azure page.
"""

import os
from datetime import date

import pytest
from fastapi import HTTPException

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
)
if "TOTP_ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet

    os.environ["TOTP_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import security  # noqa: E402
from services import mvr_pull as service  # noqa: E402

BROKER = {"id": "b-1", "role": "broker", "email": "b1@example.com"}
OTHER = {"id": "b-2", "role": "broker", "email": "b2@example.com"}
ADMIN = {"id": "a-1", "role": "admin", "email": "a1@example.com"}


class _Result:
    def __init__(self, data):
        self.data = data


class _UniqueViolation(Exception):
    code = "23505"
    message = "duplicate key value violates unique constraint"


@pytest.fixture
def db(monkeypatch):
    """A list standing in for mvr_pull, plus the settings row and audit log."""
    rows: list[dict] = []
    audit: list[dict] = []
    settings = {"password_hash": security.hash_password("right-password"), "updated_at": "2026-10-20T00:00:00+00:00"}

    def insert(values):
        if any(r["aris_order_number"] == values["aris_order_number"] for r in rows):
            raise _UniqueViolation()
        row = {
            "id": f"p-{len(rows) + 1}",
            "business_reason": "new_business",
            "info_retrieved": "mvr",
            "created_at": "2026-10-20T12:00:00+00:00",
            **values,
        }
        rows.append(row)
        return _Result([row])

    def get(pull_id):
        return _Result([r for r in rows if r["id"] == pull_id])

    def between(start, end, broker_id=None):
        return [
            r for r in rows
            if start <= r["requested_on"] < end and (broker_id is None or r["broker_id"] == broker_id)
        ]

    def update(pull_id, values):
        for r in rows:
            if r["id"] == pull_id:
                r.update(values)
                return _Result([r])
        return _Result([])

    def delete(pull_id):
        rows[:] = [r for r in rows if r["id"] != pull_id]

    def set_password(password_hash):
        settings["password_hash"] = password_hash
        settings["updated_at"] = "2026-10-21T00:00:00+00:00"

    monkeypatch.setattr(service.repo, "insert", insert)
    monkeypatch.setattr(service.repo, "get", get)
    monkeypatch.setattr(service.repo, "between", between)
    monkeypatch.setattr(service.repo, "update", update)
    monkeypatch.setattr(service.repo, "delete", delete)
    monkeypatch.setattr(service.repo, "brokers_with_pulls", lambda: [
        {"broker_id": r["broker_id"], "completed_by": r["completed_by"]} for r in reversed(rows)
    ])
    monkeypatch.setattr(service.settings_repo, "get", lambda: _Result([dict(settings)]))
    monkeypatch.setattr(service.settings_repo, "set_password", set_password)
    monkeypatch.setattr(service.audit_repo, "insert", lambda **kw: audit.append(kw))
    monkeypatch.setattr(service.broker_repo, "get_safe", lambda bid: _Result([{"name": {"b-1": "Pat Broker", "b-2": "Sam Broker", "a-1": "Ada Admin"}[bid]}]))
    monkeypatch.setattr(service.dash_azure, "read_pdf", lambda pdf: pdf.decode())
    return {"rows": rows, "audit": audit}


def _mvr(licence: str, receipt: str, requested: str = "08/10/2026") -> bytes:
    """Markdown in the shape mvr_parse reads; the fake Azure returns it as-is."""
    return (
        "# ONTARIO Driving Record\n"
        f"<td>Licence Number :</td><td>{licence}</td>"
        "<td>Name :</td><td>TESTER, PAT</td><td>Gender:</td><td>F</td>"
        f"<td>Requested On :</td><td>{requested}</td>"
        f"Receipt Number : {receipt}\n"
    ).encode()


def test_logging_an_mvr_records_the_uploader(db):
    result = service.log_pdf(BROKER, "a.pdf", _mvr("T1234-56789-01234", "600000111"))
    assert result["status"] == "logged"
    [row] = db["rows"]
    assert row["broker_id"] == "b-1"
    assert row["completed_by"] == "Pat Broker"
    assert row["licence_number"] == "T12345678901234"
    assert row["requested_on"] == "2026-10-08"


def test_the_same_mvr_twice_is_already_logged(db):
    service.log_pdf(BROKER, "a.pdf", _mvr("T1234-56789-01234", "600000111"))
    again = service.log_pdf(BROKER, "a.pdf", _mvr("T1234-56789-01234", "600000111"))
    assert again["status"] == "already_logged"
    assert len(db["rows"]) == 1


def test_a_file_that_is_not_an_mvr_is_unreadable_not_an_error(db):
    result = service.log_pdf(BROKER, "dash.pdf", b"# DASH Report")
    assert result["status"] == "unreadable"
    assert db["rows"] == []


def test_derived_dates(db):
    service.log_pdf(BROKER, "a.pdf", _mvr("T1234-56789-01234", "600000111"))
    [pull] = service.list_month(BROKER, "2026-10")["pulls"]
    assert pull["use_completed_on"] == date(2026, 10, 8)
    assert pull["destruction_on"] == date(2027, 10, 8)


def test_leap_day_destruction_never_runs_past_the_year():
    assert service._one_year_on(date(2028, 2, 29)) == date(2029, 2, 28)


def test_broker_sees_only_their_own_lines_masked(db):
    service.log_pdf(BROKER, "a.pdf", _mvr("T1234-56789-01234", "600000111"))
    service.log_pdf(OTHER, "b.pdf", _mvr("U9876-54321-09876", "600000222"))

    mine = service.list_month(BROKER, "2026-10", broker_id="b-2")
    assert [p["aris_order_number"] for p in mine["pulls"]] == ["600000111"]
    assert mine["pulls"][0]["licence_number"] == "T1234XXXXXXXXXX"
    assert mine["brokers"] == []


def test_admin_sees_everyone_in_full_and_can_filter(db):
    service.log_pdf(BROKER, "a.pdf", _mvr("T1234-56789-01234", "600000111"))
    service.log_pdf(OTHER, "b.pdf", _mvr("U9876-54321-09876", "600000222"))

    everyone = service.list_month(ADMIN, "2026-10")
    assert len(everyone["pulls"]) == 2
    assert everyone["pulls"][0]["licence_number"] == "T12345678901234"
    assert {b["name"] for b in everyone["brokers"]} == {"Pat Broker", "Sam Broker"}

    one = service.list_month(ADMIN, "2026-10", broker_id="b-2")
    assert [p["aris_order_number"] for p in one["pulls"]] == ["600000222"]


def test_month_is_half_open(db):
    service.log_pdf(BROKER, "a.pdf", _mvr("T1234-56789-01234", "600000111", requested="31/10/2026"))
    service.log_pdf(BROKER, "b.pdf", _mvr("U9876-54321-09876", "600000222", requested="01/11/2026"))
    assert [p["aris_order_number"] for p in service.list_month(BROKER, "2026-10")["pulls"]] == ["600000111"]


def test_broker_cannot_change_or_delete_someone_elses_line(db):
    service.log_pdf(OTHER, "b.pdf", _mvr("U9876-54321-09876", "600000222"))
    pull_id = db["rows"][0]["id"]
    with pytest.raises(HTTPException) as err:
        service.update(BROKER, pull_id, {"info_retrieved": "predictor"})
    assert err.value.status_code == 404
    with pytest.raises(HTTPException):
        service.delete(BROKER, pull_id)
    assert len(db["rows"]) == 1


def test_owner_and_admin_can_change_the_dropdowns(db):
    service.log_pdf(BROKER, "a.pdf", _mvr("T1234-56789-01234", "600000111"))
    pull_id = db["rows"][0]["id"]
    assert service.update(BROKER, pull_id, {"info_retrieved": "predictor", "business_reason": None})["info_retrieved"] == "predictor"
    assert service.update(ADMIN, pull_id, {"business_reason": "adding_driver"})["business_reason"] == "adding_driver"


def test_delete_removes_the_line_and_audits_without_the_licence(db):
    service.log_pdf(BROKER, "a.pdf", _mvr("T1234-56789-01234", "600000111"))
    pull_id = db["rows"][0]["id"]
    service.delete(ADMIN, pull_id)
    assert db["rows"] == []
    [entry] = db["audit"]
    assert entry["action"] == "mvr_pull_deleted"
    assert "T1234" not in str(entry)


def test_report_needs_the_right_password(db):
    with pytest.raises(HTTPException) as err:
        service.unlock("wrong")
    assert err.value.status_code == 401

    token = service.unlock("right-password")["token"]
    service.log_pdf(BROKER, "a.pdf", _mvr("T1234-56789-01234", "600000111"))
    [pull] = service.report(token, "2026-10")["pulls"]
    assert pull["licence_number"] == "T12345678901234"


def test_report_refuses_no_token_and_a_session_token(db):
    with pytest.raises(HTTPException):
        service.report(None, "2026-10")
    session = security.create_access_token("b-1", "broker")
    with pytest.raises(HTTPException):
        service.report(session, "2026-10")


def test_changing_the_password_locks_out_old_tokens(db):
    token = service.unlock("right-password")["token"]
    service.set_report_password(ADMIN, "a-new-password")
    with pytest.raises(HTTPException):
        service.report(token, "2026-10")
    service.report(service.unlock("a-new-password")["token"], "2026-10")
