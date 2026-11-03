"""Who may send files to the submission review.

The rule that matters is the one a typo could weaken: only a whole
@fairstarinsurance.ca address gets on the list. A look-alike domain that passed
here would be the one way an outsider's mail reached a customer's review.
"""

import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")

from services import review_sender as service  # noqa: E402


def rows(*found):
    return SimpleNamespace(data=list(found))


@pytest.fixture
def repo(monkeypatch):
    calls = SimpleNamespace(inserted=[], changed=[], audited=[], existing=[], by_id=[])

    def insert(email):
        calls.inserted.append(email)
        return rows({"id": "new", "email": email, "approved": True})

    def set_approved(sender_id, approved):
        calls.changed.append((sender_id, approved))
        return rows({"id": sender_id, "email": "a@fairstarinsurance.ca", "approved": approved})

    monkeypatch.setattr(service.repo, "insert", insert)
    monkeypatch.setattr(service.repo, "set_approved", set_approved)
    monkeypatch.setattr(service.repo, "get_by_email", lambda _e: rows(*calls.existing))
    monkeypatch.setattr(service.repo, "get_by_id", lambda _i: rows(*calls.by_id))
    monkeypatch.setattr(service.audit_log_repo, "insert", lambda **kw: calls.audited.append(kw))
    return calls


@pytest.mark.parametrize("address", [
    "jane@gmail.com",
    "jane@fairstarinsurance.ca.evil.com",
    "jane@notfairstarinsurance.ca",
    "@fairstarinsurance.ca",
    "two words@fairstarinsurance.ca",
    "a@b@fairstarinsurance.ca",
    "",
])
def test_only_a_whole_fairstar_address_is_accepted(repo, address):
    with pytest.raises(HTTPException) as refused:
        service.add_sender(address, "admin")
    assert refused.value.status_code == 422
    assert repo.inserted == []


def test_an_address_is_saved_in_lower_case_and_recorded(repo):
    saved = service.add_sender("  Jane.Doe@FairstarInsurance.ca ", "admin")
    assert repo.inserted == ["jane.doe@fairstarinsurance.ca"]
    assert saved["approved"] is True
    assert repo.audited[0]["actor_broker_id"] == "admin"
    assert repo.audited[0]["action"] == "review_sender_added"


def test_an_address_already_on_the_list_is_refused(repo):
    repo.existing = [{"id": "1", "email": "a@fairstarinsurance.ca", "approved": True}]
    with pytest.raises(HTTPException) as refused:
        service.add_sender("a@fairstarinsurance.ca", "admin")
    assert refused.value.status_code == 409
    assert repo.audited == []


def test_adding_a_turned_off_address_turns_it_back_on_with_its_files(repo):
    repo.existing = [{"id": "1", "email": "a@fairstarinsurance.ca", "approved": False}]
    service.add_sender("a@fairstarinsurance.ca", "admin")
    assert repo.changed == [("1", True)]
    assert repo.inserted == []


def test_turning_off_is_recorded(repo):
    repo.by_id = [{"id": "1", "email": "a@fairstarinsurance.ca", "approved": True}]
    service.set_approved("1", False, "admin")
    assert repo.changed == [("1", False)]
    assert repo.audited[0]["action"] == "review_sender_off"


def test_an_outside_row_cannot_be_turned_on(repo):
    repo.by_id = [{"id": "1", "email": "mailbox@gmail.com", "approved": False}]
    with pytest.raises(HTTPException) as refused:
        service.set_approved("1", True, "admin")
    assert refused.value.status_code == 404
    assert repo.changed == []
