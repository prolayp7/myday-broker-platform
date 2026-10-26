"""Website lead intake — the contract, the signature, and the webhook's answers.

WHY THIS FILE EXISTS. The website and the CRM were built by different hands
against one written contract (plans: website leads, payload section). Every
rule here is a place the two could drift apart, and each drift has the same
cost: a customer who pressed Send and never reached a broker. The webhook's
status codes are part of that contract too — the website retries a 5xx and
gives up on a 403/422 — so they are asserted exactly.

No database. The routing itself is SQL and is covered by
tests/test_website_lead_routing.py, against staging.
"""

import hashlib
import hmac
import json
import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv
from pydantic import ValidationError

# Credentials before the import: services.website_lead reaches db.py, which
# reads SUPABASE_URL at module level. Nothing below talks to the database.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")):
    pytest.skip(
        "needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY — services import db.py "
        "(no call is made)",
        allow_module_level=True,
    )

from fastapi import HTTPException  # noqa: E402

from routes import webhooks  # noqa: E402
from schemas.website_lead import (  # noqa: E402
    UpdateWebsiteBrokerRequest,
    UpdateWebsiteBrokersRequest,
    WebsiteBrokerRow,
    WebsiteLeadPayload,
)
from services import website_lead  # noqa: E402

SECRET = "test-website-secret"


def payload(coverage="auto-home", **overrides) -> dict:
    """A valid submission for the given coverage, answers matching what the
    form asks for it."""
    driving = coverage in ("auto", "auto-home", "auto-tenant")
    body = {
        "version": 1,
        "submission_id": str(uuid.uuid4()),
        "submitted_at": "2026-09-29T14:32:05Z",
        "coverage": coverage,
        "name": "Priya Sharma",
        "phone": "+14165550123",
        "email": "priya@example.com",
        "address": "100 King St W, Toronto, ON M5X 1A9",
        "address_location": "43.6487,-79.3817",
        "call_time": "2026-09-30T10",
        "convictions": "none" if driving else None,
        "auto_claims": "1" if driving else None,
        "home_claims": "none" if coverage in ("home", "auto-home") else None,
        "consent_notice": "By selecting Send my details, you agree to our Terms.",
        "page": "https://policyscanner.ca/quote/",
    }
    body.update(overrides)
    return body


def sign(raw: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


# ── the payload contract ─────────────────────────────────────────────────
class TestPayloadContract:

    @pytest.mark.parametrize("coverage", ["auto", "home", "tenant", "auto-home", "auto-tenant"])
    def test_every_coverage_is_accepted_with_its_own_questions(self, coverage):
        WebsiteLeadPayload.model_validate(payload(coverage))

    def test_the_crm_vocabulary_is_refused(self):
        # The first draft of the contract sent combined_policy_type values that
        # 0068 had already renamed. coverage is the website's word, and only it.
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(payload(coverage="Homeowners"))

    @pytest.mark.parametrize("field", ["convictions", "auto_claims"])
    def test_a_car_coverage_must_answer_the_driving_questions(self, field):
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(payload("auto", **{field: None}))

    @pytest.mark.parametrize("field", ["convictions", "auto_claims", "home_claims"])
    def test_a_tenant_is_never_asked_anything(self, field):
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(payload("tenant", **{field: "none"}))

    def test_home_claims_only_for_an_owned_home(self):
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(payload("auto-tenant", home_claims="1"))
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(payload("home", home_claims=None))

    def test_every_key_must_be_present_even_when_null(self):
        # "Always present, null when not asked" — a missing key is a website
        # bug, not an unanswered question.
        body = payload("tenant")
        del body["home_claims"]
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(body)

    @pytest.mark.parametrize("phone", ["4165550123", "+1416555012", "+44 20 7946 0958", ""])
    def test_the_phone_must_arrive_normalised(self, phone):
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(payload(phone=phone))

    @pytest.mark.parametrize("call_time", ["asap", "2026-09-30T09", "2026-09-30T20"])
    def test_call_time_accepts_asap_and_a_slot(self, call_time):
        WebsiteLeadPayload.model_validate(payload(call_time=call_time))

    @pytest.mark.parametrize("call_time", ["ASAP", "2026-09-30T24", "2026-09-30 10", "tomorrow"])
    def test_call_time_refuses_anything_else(self, call_time):
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(payload(call_time=call_time))

    def test_a_typed_address_has_no_location(self):
        WebsiteLeadPayload.model_validate(payload(address_location=None))

    def test_a_new_field_on_the_website_does_not_lose_the_lead(self):
        # extra='ignore': the raw body is stored whole anyway, and refusing an
        # unknown key would turn a harmless website change into lost customers.
        WebsiteLeadPayload.model_validate(payload(utm_source="google"))

    def test_the_version_is_pinned(self):
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(payload(version=2))

    # ── what the drain would read raw must already be exactly right ──────
    # The drain builds the lead from the STORED body, so anything Pydantic
    # would quietly normalise has to be refused here instead (review, 09-29).

    @pytest.mark.parametrize("call_time", ["2026-02-30T10", "2026-13-01T10", "2026-00-10T10"])
    def test_call_time_must_be_a_real_day(self, call_time):
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(payload(call_time=call_time))

    @pytest.mark.parametrize("submitted_at", [
        1727620325, 1727620325.5, "1727620325", "2026-09-29T14:32:05", "yesterday",
        # Wider than Postgres, or than what the website sends (re-review, 09-29):
        "2026-09-29T14:32:05+20:00", "2026-09-29T14:32:05,123Z", "2026-09-29T14:32Z",
        "2026-09-29T10:32:05-04:00", "2026-13-29T14:32:05Z",
    ])
    def test_submitted_at_must_be_utc_iso_with_z(self, submitted_at):
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(payload(submitted_at=submitted_at))

    @pytest.mark.parametrize("submitted_at", ["2026-09-29T14:32:05Z", "2026-09-29T14:32:05.123Z"])
    def test_submitted_at_accepts_what_to_iso_string_writes(self, submitted_at):
        WebsiteLeadPayload.model_validate(payload(submitted_at=submitted_at))

    @pytest.mark.parametrize("email", ["Priya Sharma <priya@example.com>", "<priya@example.com>",
                                       " priya@example.com"])
    def test_email_must_be_a_bare_address(self, email):
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(payload(email=email))

    @pytest.mark.parametrize("phone", ["+1４１６５５５０１２３", "+1٤١٦٥٥٥٠١٢٣"])
    def test_digits_from_other_scripts_are_refused(self, phone):
        with pytest.raises(ValidationError):
            WebsiteLeadPayload.model_validate(payload(phone=phone))


# ── the signature ────────────────────────────────────────────────────────
class TestSignature:

    def test_a_correct_signature_passes(self):
        raw = b'{"a":1}'
        assert webhooks._verify_website_signature(raw, sign(raw), SECRET)

    def test_the_wrong_secret_fails(self):
        raw = b'{"a":1}'
        assert not webhooks._verify_website_signature(raw, sign(raw, "other"), SECRET)

    def test_a_reserialised_body_fails(self):
        # The bytes signed must be the bytes checked. A body re-serialised with
        # different spacing is a different body.
        raw = b'{"a": 1}'
        assert not webhooks._verify_website_signature(b'{"a":1}', sign(raw), SECRET)

    def test_a_missing_prefix_fails(self):
        raw = b'{"a":1}'
        bare = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        assert not webhooks._verify_website_signature(raw, bare, SECRET)

    def test_a_non_ascii_signature_is_false_not_an_exception(self):
        # compare_digest raises TypeError on non-ASCII — a 500 anyone could
        # cause without knowing the secret.
        assert not webhooks._verify_website_signature(b"{}", "sha256=é", SECRET)


# ── the webhook's answers ────────────────────────────────────────────────
def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(webhooks.router)
    return TestClient(app, raise_server_exceptions=False)


class TestWebhook:

    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        """No database: record what the route stored and whether it drained."""
        self.stored, self.drained = [], []
        monkeypatch.setenv("WEBSITE_WEBHOOK_SECRET", SECRET)
        monkeypatch.setattr(
            website_lead, "record_submission",
            lambda p, raw: self.stored.append((p, raw)) or True,
        )
        monkeypatch.setattr(website_lead, "drain", lambda: self.drained.append(1))

    def _post(self, body: dict, signature: str | None = None):
        raw = json.dumps(body).encode()
        return _client().post(
            "/webhooks/website",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-PolicyScanner-Signature": signature if signature is not None else sign(raw),
            },
        )

    def test_a_valid_submission_is_stored_then_drained(self):
        body = payload()
        r = self._post(body)
        assert r.status_code == 200
        assert r.json() == {"status": "received"}
        assert len(self.stored) == 1
        # The raw body is what gets stored, exactly as sent.
        assert self.stored[0][1] == body
        assert self.drained == [1], "the drain runs after the response"

    def test_a_bad_signature_is_403_and_nothing_is_stored(self):
        r = self._post(payload(), signature="sha256=deadbeef")
        assert r.status_code == 403
        assert self.stored == [] and self.drained == []

    def test_an_invalid_payload_is_422_and_nothing_is_stored(self):
        r = self._post(payload(coverage="boat"))
        assert r.status_code == 422
        assert self.stored == []

    def test_a_422_never_echoes_the_customer_back(self):
        # The website's server logs this response. The customer's details must
        # not be in it.
        r = self._post(payload(email="not-an-email", name="Priya Sharma"))
        assert r.status_code == 422
        assert "Priya" not in r.text and "not-an-email" not in r.text

    def test_a_body_that_is_not_json_is_422(self):
        raw = b"not json"
        r = _client().post(
            "/webhooks/website", content=raw,
            headers={"X-PolicyScanner-Signature": sign(raw)},
        )
        assert r.status_code == 422

    @pytest.mark.parametrize("raw", [
        b"\xff\xfe not utf-8",
        b'{"version": NaN}',
        b'{"version": Infinity}',
        b"[" * 100_000 + b"]" * 100_000,
    ], ids=["not-utf8", "nan", "infinity", "nested-100k-deep"])
    def test_a_malformed_signed_body_is_422_not_500(self, raw):
        r = _client().post(
            "/webhooks/website", content=raw,
            headers={"X-PolicyScanner-Signature": sign(raw)},
        )
        assert r.status_code == 422
        assert self.stored == []

    def test_a_nul_character_is_422_before_storage(self):
        # Postgres refuses NUL in text and jsonb: accepted here, it would fail
        # at storage and the website would retry forever.
        r = self._post(payload(name="Priya\u0000Sharma"))
        assert r.status_code == 422
        assert self.stored == []

    def test_a_non_ascii_signature_header_is_403(self):
        raw = json.dumps(payload()).encode()
        r = _client().post(
            "/webhooks/website", content=raw,
            # Raw bytes: a server decodes header bytes as latin-1, so a UTF-8
            # "é" arrives as two non-ASCII characters.
            headers={"X-PolicyScanner-Signature": "sha256=é".encode("utf-8")},
        )
        assert r.status_code == 403

    def test_an_email_error_never_quotes_the_address(self):
        r = self._post(payload(email="someone@xn--zz-invalid-idna.com"))
        assert r.status_code == 422
        assert "xn--" not in r.text and "someone" not in r.text

    def test_a_missing_email_is_reported_as_missing(self):
        # Only VALUE errors are rewritten to the fixed email message; a missing
        # key must still say so, or a contract drift is misreported.
        body = payload()
        del body["email"]
        r = self._post(body)
        assert r.status_code == 422
        assert "Field required" in r.text

    def test_a_lone_surrogate_in_an_ignored_key_is_422_not_500(self):
        # Validation ignores unknown keys, but the body is stored whole, and a
        # lone surrogate cannot be encoded to send to the database.
        raw = b'{"x": "\\ud83d", ' + json.dumps(payload()).encode()[1:]
        r = _client().post(
            "/webhooks/website", content=raw,
            headers={"X-PolicyScanner-Signature": sign(raw)},
        )
        assert r.status_code == 422
        assert self.stored == []

    def test_no_secret_configured_refuses_with_503(self, monkeypatch):
        # Refused, never accepted unsigned. 503 so the website retries and then
        # shows the phone number.
        monkeypatch.delenv("WEBSITE_WEBHOOK_SECRET")
        r = self._post(payload())
        assert r.status_code == 503
        assert self.stored == []

    def test_a_storage_failure_is_a_5xx_not_received(self, monkeypatch):
        # "received" for a submission that was never stored is the one outcome
        # the whole design exists to prevent.
        def boom(p, raw):
            raise RuntimeError("database unreachable")
        monkeypatch.setattr(website_lead, "record_submission", boom)
        r = self._post(payload())
        assert r.status_code >= 500
        assert "received" not in r.text

    def test_a_duplicate_is_still_received(self, monkeypatch):
        monkeypatch.setattr(website_lead, "record_submission", lambda p, raw: False)
        r = self._post(payload())
        assert r.status_code == 200
        assert r.json() == {"status": "received"}


# ── the service ──────────────────────────────────────────────────────────
class _Result:
    def __init__(self, data):
        self.data = data


class TestService:

    def test_a_submission_is_stored_waiting_under_a_namespaced_key(self, monkeypatch):
        written = []
        monkeypatch.setattr(
            website_lead.repo, "insert_submission_if_new",
            lambda values: written.append(values) or _Result([{"id": "e1"}]),
        )
        body = payload()
        assert website_lead.record_submission(WebsiteLeadPayload.model_validate(body), body) is True
        row = written[0]
        assert row["source"] == "website"
        assert row["status"] == "waiting", "never 'received' — the Meta retry drain sweeps those"
        assert row["dedup_key"] == f"website:{body['submission_id']}"
        assert row["raw_payload"] == body

    def test_a_duplicate_reports_false(self, monkeypatch):
        monkeypatch.setattr(website_lead.repo, "insert_submission_if_new", lambda v: _Result([]))
        body = payload()
        assert website_lead.record_submission(WebsiteLeadPayload.model_validate(body), body) is False

    def test_a_storage_error_is_a_500_that_does_not_log_the_customer(self, monkeypatch, caplog):
        # An unhandled APIError is logged by uvicorn with str(err), whose
        # `details` on a check violation is the whole failing row. Through
        # db_error, only the code and the constraint name are logged.
        class CheckViolation(Exception):
            code = "23514"
            message = 'new row violates check constraint "webhook_event_source_check"'
            details = "Failing row contains (Priya Sharma, +14165550123, priya@example.com)"
            def __str__(self):
                return f"{self.message} {self.details}"
        def fail(values):
            raise CheckViolation()
        monkeypatch.setattr(website_lead.repo, "insert_submission_if_new", fail)
        body = payload()
        with pytest.raises(HTTPException) as err:
            website_lead.record_submission(WebsiteLeadPayload.model_validate(body), body)
        assert err.value.status_code == 500
        assert "Priya" not in caplog.text and "+1416" not in caplog.text

    def test_the_drain_never_raises(self, monkeypatch):
        # Every caller is a background task or the timed loop. A temporary
        # error leaves everything waiting (the SQL rolls back); it must not
        # escape and kill the loop.
        def boom(limit):
            raise RuntimeError("connection reset")
        monkeypatch.setattr(website_lead.repo, "assign_waiting", boom)
        assert website_lead.drain() is None

    def test_the_drain_calls_again_while_batches_come_back_full(self, monkeypatch):
        # 0128: a full batch means there may be more; a short one means the
        # queue is empty or nobody is free.
        monkeypatch.setattr(website_lead, "DRAIN_BATCH", 2)
        batches = iter([{"assigned": 2, "failed": 0}, {"assigned": 1, "failed": 1},
                        {"assigned": 1, "failed": 0}])
        calls = []
        def one_batch(limit):
            calls.append(limit)
            return _Result(next(batches))
        monkeypatch.setattr(website_lead.repo, "assign_waiting", one_batch)
        assert website_lead.drain() == {"assigned": 1, "failed": 0}
        assert calls == [2, 2, 2]

    def test_a_drain_already_running_absorbs_a_second_request(self, monkeypatch):
        # A burst of submissions must not queue one database drain each: a
        # caller that finds a drain running leaves a note, and the running one
        # goes round once more.
        import threading
        release = threading.Event()
        calls = []
        def slow_batch(limit):
            calls.append(1)
            if len(calls) == 1:
                release.wait(timeout=5)
            return _Result({"assigned": 0, "failed": 0})
        monkeypatch.setattr(website_lead.repo, "assign_waiting", slow_batch)

        first = threading.Thread(target=website_lead.drain)
        first.start()
        while not calls:
            pass
        # The second caller does not run a drain of its own...
        assert website_lead.drain() is None
        release.set()
        first.join(timeout=5)
        # ...the first one ran once more on its behalf.
        assert len(calls) == 2

    def test_an_unknown_broker_is_404(self, monkeypatch):
        class FkError(Exception):
            code = "23503"
            message = 'violates foreign key constraint "website_broker_setting_broker_id_fkey"'
        def fk(*a):
            raise FkError()
        monkeypatch.setattr(website_lead.repo, "set_broker_setting", fk)
        with pytest.raises(HTTPException) as err:
            website_lead.update_broker(uuid.uuid4(), UpdateWebsiteBrokerRequest(is_on=True))
        assert err.value.status_code == 404

    def test_a_bulk_change_sends_every_broker_in_one_call(self, monkeypatch):
        ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        calls = []
        def one_call(broker_ids, is_on, daily_cap):
            calls.append((broker_ids, is_on, daily_cap))
            return _Result([{"broker_id": b, "is_on": True, "daily_cap": 0} for b in broker_ids])
        monkeypatch.setattr(website_lead.repo, "set_broker_settings", one_call)

        rows = website_lead.update_brokers(UpdateWebsiteBrokersRequest(broker_ids=ids, is_on=True))

        # One call carrying all three — that is what makes it all-or-nothing.
        assert calls == [([str(i) for i in ids], True, None)]
        assert len(rows) == 3

    def test_a_bulk_change_with_an_unknown_broker_is_404(self, monkeypatch):
        class FkError(Exception):
            code = "23503"
            message = 'violates foreign key constraint "website_broker_setting_broker_id_fkey"'
        def fk(*a):
            raise FkError()
        monkeypatch.setattr(website_lead.repo, "set_broker_settings", fk)
        with pytest.raises(HTTPException) as err:
            website_lead.update_brokers(
                UpdateWebsiteBrokersRequest(broker_ids=[uuid.uuid4()], is_on=True)
            )
        assert err.value.status_code == 404

    def test_a_bulk_change_that_comes_back_short_is_not_reported_as_saved(self, monkeypatch):
        ids = [uuid.uuid4(), uuid.uuid4()]
        monkeypatch.setattr(
            website_lead.repo, "set_broker_settings",
            lambda *a: _Result([{"broker_id": str(ids[0]), "is_on": True, "daily_cap": 0}]),
        )
        with pytest.raises(HTTPException) as err:
            website_lead.update_brokers(UpdateWebsiteBrokersRequest(broker_ids=ids, is_on=True))
        assert err.value.status_code == 500


class TestTheFacebookDoorIsClosed:

    def test_the_admin_create_lead_refuses_a_website_lead(self):
        # POST /leads runs assign_lead: Facebook cap, lead_delivery, lead cost.
        # A website lead must never be created through it.
        from schemas.lead import CreateLeadRequest
        from services import lead as lead_service
        request = CreateLeadRequest(
            group_id=uuid.uuid4(), source="website", name="A", phone="+14165550123",
            email="a@example.com",
        )
        with pytest.raises(HTTPException) as err:
            lead_service.create_lead(request)
        assert err.value.status_code == 422


class TestUpdateRequest:

    def test_one_field_at_a_time_is_fine(self):
        UpdateWebsiteBrokerRequest(is_on=False)
        UpdateWebsiteBrokerRequest(daily_cap=5)

    def test_an_empty_update_is_refused(self):
        with pytest.raises(ValidationError):
            UpdateWebsiteBrokerRequest()

    @pytest.mark.parametrize("cap", [-1, 201])
    def test_the_limit_is_bounded(self, cap):
        with pytest.raises(ValidationError):
            UpdateWebsiteBrokerRequest(daily_cap=cap)


class TestBulkUpdateRequest:

    def test_one_field_for_many_brokers_is_fine(self):
        UpdateWebsiteBrokersRequest(broker_ids=[uuid.uuid4(), uuid.uuid4()], is_on=False)
        UpdateWebsiteBrokersRequest(broker_ids=[uuid.uuid4()], daily_cap=5)

    def test_nobody_listed_is_refused(self):
        with pytest.raises(ValidationError):
            UpdateWebsiteBrokersRequest(broker_ids=[], is_on=True)

    def test_nothing_to_change_is_refused(self):
        with pytest.raises(ValidationError):
            UpdateWebsiteBrokersRequest(broker_ids=[uuid.uuid4()])

    def test_the_same_broker_twice_is_refused(self):
        again = uuid.uuid4()
        with pytest.raises(ValidationError):
            UpdateWebsiteBrokersRequest(broker_ids=[again, again], is_on=True)

    @pytest.mark.parametrize("cap", [-1, 201])
    def test_the_limit_is_bounded(self, cap):
        with pytest.raises(ValidationError):
            UpdateWebsiteBrokersRequest(broker_ids=[uuid.uuid4()], daily_cap=cap)


class TestBoardRow:

    def test_a_board_from_before_0129_still_validates(self):
        # No community, no start date — what 0127's function sends. A required
        # field here would be a 500 on the whole screen until 0129 is applied.
        row = WebsiteBrokerRow(
            broker_id="b", name="A", group_id="g", group_name="G", is_on=False,
            daily_cap=0, today=0, self_paused=False, is_blocked_by_admin=False,
            is_active=True,
        )
        assert row.community_name is None and row.eligible_from is None
