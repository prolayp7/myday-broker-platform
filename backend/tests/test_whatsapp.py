"""Tests for the WhatsApp lead-notification pipeline.

WHY THIS FILE EXISTS. Every failure in this feature is SILENT. If a broker's
notification never arrives, nothing breaks: the lead is still in the CRM, the
screens all work, and no one complains — they just call the customer later
than a competitor did. There is no error anybody sees, so a test is the only
thing standing between "working" and "quietly stopped working".

Meta is never called. Every send is stubbed, so the suite is offline,
deterministic and free.
"""

import pytest

import whatsapp_client
from services import whatsapp


# ── the message a broker actually receives ───────────────────────────────
class TestVariables:
    """The three template variables, by position. The approved template's
    shape is frozen at approval, so a change in this ORDER silently sends
    brokers a phone number where they expect a name."""

    def test_maps_the_three_fields_in_order(self):
        assert whatsapp._variables({
            "name": "Jane Smith",
            "phone_e164": "+14165550134",
            "licensed_in_canada": "2020_or_before",
        }) == {"1": "Jane Smith", "2": "+14165550134", "3": "2020 or before"}

    def test_prefers_e164_so_the_broker_can_tap_to_call(self):
        v = whatsapp._variables({"name": "A", "phone": "416-555-0134",
                                 "phone_e164": "+14165550134"})
        assert v["2"] == "+14165550134"

    def test_falls_back_to_raw_phone_when_not_normalisable(self):
        # A number a human can still read beats showing them nothing.
        v = whatsapp._variables({"name": "A", "phone": "416-555-0134 ext 22"})
        assert v["2"] == "416-555-0134 ext 22"

    def test_missing_licence_answer_does_not_suppress_the_message(self):
        # A website or phone lead never answers the Meta form question. The
        # broker still needs to know the lead exists — and Meta rejects an
        # empty template variable outright.
        v = whatsapp._variables({"name": "Bob", "phone_e164": "+14165550111"})
        assert v["3"] == "Not provided"
        assert all(v.values()), "no variable may be empty"

    def test_missing_name_still_sends(self):
        assert whatsapp._variables({"phone_e164": "+1416"})["1"] == "Not provided"


# ── which Meta failures are worth retrying ───────────────────────────────
class TestPermanentFailures:
    """Retrying a permanent failure only delays a person noticing it, and
    burns the attempt budget that a genuine outage needs."""

    @pytest.mark.parametrize("code", [131026, 132001, 132015, 133010, 190])
    def test_known_permanent_codes(self, code):
        assert code in whatsapp_client._PERMANENT_CODES

    def test_unknown_codes_are_treated_as_transient(self):
        # A wrong guess this way costs three extra attempts; the other way
        # loses a real notification.
        assert 131000 not in whatsapp_client._PERMANENT_CODES

    def test_missing_phone_is_permanent_not_a_retry(self):
        # phone_e164 is null when the number could not be parsed as NANP.
        # Nothing about that changes on a retry.
        with pytest.raises(whatsapp_client.WhatsAppError) as err:
            whatsapp_client.send_template("", {"1": "a"})
        assert err.value.permanent

    def test_unconfigured_is_permanent(self, monkeypatch):
        monkeypatch.setattr(whatsapp_client, "_ACCESS_TOKEN", "")
        with pytest.raises(whatsapp_client.WhatsAppError) as err:
            whatsapp_client.send_template("+14165550134", {"1": "a"})
        assert err.value.permanent


# ── binding values to {{1}}, {{2}}, {{3}} ────────────────────────────────
class TestTemplateParameters:
    """Meta binds values BY POSITION IN A LIST, where Twilio took an object
    keyed by position. Sorting those keys as strings would order a ten-variable
    template 1, 10, 2 and put the wrong value in every placeholder from {{2}}
    on — silently, because every parameter is still a valid string."""

    def test_positional_order_is_numeric_not_lexicographic(self):
        params = whatsapp_client._variables_to_parameters(
            {str(i): f"v{i}" for i in range(1, 11)}
        )
        assert [x["text"] for x in params] == [f"v{i}" for i in range(1, 11)]

    def test_every_parameter_is_typed_text(self):
        params = whatsapp_client._variables_to_parameters({"1": "a", "2": "b"})
        assert all(x["type"] == "text" for x in params)


# ── the kill switch ──────────────────────────────────────────────────────
class TestIsConfigured:
    """An environment without Meta credentials must pay nothing at all."""

    def test_false_when_any_credential_is_missing(self, monkeypatch):
        for attr in ("_ACCESS_TOKEN", "_PHONE_NUMBER_ID", "_TEMPLATE_NAME"):
            monkeypatch.setattr(whatsapp_client, "_ACCESS_TOKEN", "tok")
            monkeypatch.setattr(whatsapp_client, "_PHONE_NUMBER_ID", "123")
            monkeypatch.setattr(whatsapp_client, "_TEMPLATE_NAME", "tmpl")
            monkeypatch.setattr(whatsapp_client, attr, "")
            assert not whatsapp_client.is_configured(), f"{attr} missing must disable"

    def test_true_when_all_present(self, monkeypatch):
        monkeypatch.setattr(whatsapp_client, "_ACCESS_TOKEN", "tok")
        monkeypatch.setattr(whatsapp_client, "_PHONE_NUMBER_ID", "123")
        monkeypatch.setattr(whatsapp_client, "_TEMPLATE_NAME", "tmpl")
        assert whatsapp_client.is_configured()

    def test_send_pass_does_nothing_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(whatsapp_client, "_ACCESS_TOKEN", "")
        assert whatsapp.send_pending() == {"checked": 0}


# ── the send pass, against a fake repository ─────────────────────────────
class FakeRepo:
    """Records what the service decided, without a database.

    The service's job is a sequence of DECISIONS — send, cancel, fail — so the
    test asserts on those rather than on SQL.
    """

    def __init__(self, rows):
        self._rows = rows
        self.submitted, self.cancelled, self.failed = [], [], []

    def claim_pending(self):
        return type("R", (), {"data": self._rows})()

    def mark_submitted(self, row_id, message_sid):
        self.submitted.append((row_id, message_sid))

    def mark_cancelled(self, row_id, reason):
        self.cancelled.append((row_id, reason))

    def mark_failed(self, row_id, reason, *, attempt_count, exhausted):
        self.failed.append({"id": row_id, "reason": reason,
                            "attempt_count": attempt_count, "exhausted": exhausted})


def row(*, row_id="r1", broker_id="b1", owner="b1", opted_in="2026-08-30T00:00:00Z",
        attempt_count=0, phone_e164="+14165550134", email="broker@example.com"):
    """One queue row joined to its lead and broker, as the repository returns it.

    owner is lead.assigned_broker_id — the CURRENT owner, which is what the
    ownership guard compares against.

    email is broker.email, which only the fallback (migration 0110) reads.
    """
    return {
        "id": row_id,
        "lead_id": "l1",
        "broker_id": broker_id,
        "attempt_count": attempt_count,
        "lead": {"name": "Jane Smith", "phone": "416-555-0134",
                 "phone_e164": "+14165550134",
                 "licensed_in_canada": "2020_or_before",
                 "assigned_broker_id": owner},
        "broker": {"name": "Alex Broker", "email": email,
                   "phone_e164": phone_e164, "whatsapp_opted_in_at": opted_in},
    }


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)


def use(monkeypatch, repo, sender):
    monkeypatch.setattr(whatsapp, "repo", repo)
    monkeypatch.setattr(whatsapp_client, "send_template", sender)


class TestOwnershipGuard:
    """THE GUARD THAT KEEPS A CUSTOMER'S DETAILS FROM THE WRONG BROKER.

    A lead reassigned between queueing and sending must not have its
    customer's name and phone delivered to the broker who no longer owns it.
    That message cannot be taken back once it reaches a real phone.
    """

    def test_sends_when_the_broker_still_owns_the_lead(self, monkeypatch, configured):
        repo = FakeRepo([row(broker_id="b1", owner="b1")])
        use(monkeypatch, repo, lambda to, v: "SM123")
        result = whatsapp.send_pending()
        assert repo.submitted == [("r1", "SM123")]
        assert result["submitted"] == 1

    def test_cancels_and_sends_nothing_when_reassigned(self, monkeypatch, configured):
        sent = []
        repo = FakeRepo([row(broker_id="b1", owner="b2")])  # now b2's lead
        use(monkeypatch, repo, lambda to, v: sent.append(to) or "SM1")
        result = whatsapp.send_pending()
        assert sent == [], "must not message a broker who lost the lead"
        assert len(repo.cancelled) == 1
        assert result["cancelled"] == 1


class TestConsent:
    """Opt-in is re-checked at send time, not just when queued — consent can be
    withdrawn, or revoked automatically by a phone-number change (0109), in the
    seconds in between."""

    def test_never_sends_without_opt_in(self, monkeypatch, configured):
        sent = []
        repo = FakeRepo([row(opted_in=None)])
        use(monkeypatch, repo, lambda to, v: sent.append(to) or "SM1")
        whatsapp.send_pending()
        assert sent == []
        assert len(repo.cancelled) == 1


class TestSubmittedIsNotDelivered:
    def test_a_message_id_records_submitted_only(self, monkeypatch, configured):
        # Meta replies "accepted". Recording that as delivered would
        # manufacture a success for a message that may never arrive.
        repo = FakeRepo([row()])
        use(monkeypatch, repo, lambda to, v: "SM999")
        whatsapp.send_pending()
        assert repo.submitted == [("r1", "SM999")]


class TestFailureHandling:
    def test_permanent_failure_does_not_burn_retries(self, monkeypatch, configured):
        repo = FakeRepo([row()])
        def boom(to, v):
            raise whatsapp_client.WhatsAppError("not on WhatsApp", permanent=True)
        use(monkeypatch, repo, boom)
        whatsapp.send_pending()
        assert repo.failed[0]["exhausted"] is True
        assert repo.failed[0]["attempt_count"] == 1

    def test_transient_failure_increments_the_attempt(self, monkeypatch, configured):
        repo = FakeRepo([row(attempt_count=2)])
        def boom(to, v):
            raise whatsapp_client.WhatsAppError("503 from Meta")
        use(monkeypatch, repo, boom)
        whatsapp.send_pending()
        assert repo.failed[0]["exhausted"] is False, "still has an attempt left"
        assert repo.failed[0]["attempt_count"] == 3

    def test_an_unexpected_error_is_caught_and_retried(self, monkeypatch, configured):
        # A bug in here must not abandon the rest of the batch.
        repo = FakeRepo([row()])
        def boom(to, v):
            raise RuntimeError("something unmodelled")
        use(monkeypatch, repo, boom)
        whatsapp.send_pending()
        assert repo.failed[0]["exhausted"] is False

    def test_the_last_attempt_exhausts_the_row(self, monkeypatch, configured):
        # attempt_count 3 -> this is the 4th and final try. Without this the
        # row would stay Pending and the drain would retry it forever, which
        # is the failure mode MAX_ATTEMPTS exists to prevent.
        repo = FakeRepo([row(attempt_count=whatsapp.MAX_ATTEMPTS - 1)])
        def boom(to, v):
            raise whatsapp_client.WhatsAppError("still down")
        use(monkeypatch, repo, boom)
        whatsapp.send_pending()
        assert repo.failed[0]["attempt_count"] == whatsapp.MAX_ATTEMPTS
        assert repo.failed[0]["exhausted"] is True, "must stop, not loop forever"

    def test_one_bad_row_does_not_stop_the_others(self, monkeypatch, configured):
        rows = [row(row_id="r1"), row(row_id="r2"), row(row_id="r3")]
        repo = FakeRepo(rows)
        calls = {"n": 0}
        def sender(to, v):
            calls["n"] += 1
            if calls["n"] == 2:
                raise whatsapp_client.WhatsAppError("transient")
            return f"SM{calls['n']}"
        use(monkeypatch, repo, sender)
        result = whatsapp.send_pending()
        assert result["submitted"] == 2 and result["failed"] == 1


# ── the delivery receipt ─────────────────────────────────────────────────
import hashlib
import hmac
import json

from routes import whatsapp_webhooks


def meta_signature(secret: str, raw_body: bytes) -> str:
    """Meta's documented scheme, computed INDEPENDENTLY here rather than by
    calling our own verifier — a test that reuses the implementation passes
    just as happily against a bug in it."""
    return "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def status_payload(message_id: str, status: str, *, code=None, title=None) -> dict:
    """Meta's envelope: entry -> changes -> value -> statuses.

    Deeper than Twilio's flat form post, and a BATCH — which is why the route
    loops rather than reading one status.
    """
    row = {"id": message_id, "status": status}
    if code is not None:
        row["errors"] = [{"code": code, "title": title or ""}]
    return {"entry": [{"changes": [{"value": {"statuses": [row]}}]}]}


class TestSignatureVerification:
    """This endpoint is public. Without a valid signature check anyone could
    mark a broker's message delivered — or forge a failure, which sends a real
    customer's details out in a fallback email."""

    SECRET = "test-app-secret"

    def test_accepts_a_correct_signature(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", self.SECRET)
        body = json.dumps(status_payload("wamid.1", "delivered")).encode()
        assert whatsapp_webhooks._verify_signature(body, meta_signature(self.SECRET, body))

    def test_rejects_a_tampered_body(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", self.SECRET)
        body = json.dumps(status_payload("wamid.1", "delivered")).encode()
        sig = meta_signature(self.SECRET, body)
        tampered = json.dumps(status_payload("wamid.1", "failed")).encode()
        assert not whatsapp_webhooks._verify_signature(tampered, sig)

    def test_rejects_the_wrong_secret(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", self.SECRET)
        body = json.dumps(status_payload("wamid.1", "delivered")).encode()
        assert not whatsapp_webhooks._verify_signature(
            body, meta_signature("someone-elses-secret", body))

    def test_fails_closed_when_unconfigured(self, monkeypatch):
        # A missing secret must REJECT, never skip the check.
        monkeypatch.setenv("WHATSAPP_APP_SECRET", "")
        body = json.dumps(status_payload("wamid.1", "delivered")).encode()
        assert not whatsapp_webhooks._verify_signature(
            body, meta_signature(self.SECRET, body))

    def test_rejects_a_header_without_the_sha256_prefix(self, monkeypatch):
        # Meta always prefixes "sha256="; a bare digest is not from Meta.
        monkeypatch.setenv("WHATSAPP_APP_SECRET", self.SECRET)
        body = json.dumps(status_payload("wamid.1", "delivered")).encode()
        bare = meta_signature(self.SECRET, body).removeprefix("sha256=")
        assert not whatsapp_webhooks._verify_signature(body, bare)

    def test_signature_covers_the_raw_bytes_not_reparsed_json(self, monkeypatch):
        # Re-serialising changes whitespace and key order, so a verifier that
        # hashed the parsed dict would reject every genuine request.
        monkeypatch.setenv("WHATSAPP_APP_SECRET", self.SECRET)
        spaced = json.dumps(status_payload("wamid.1", "delivered"), indent=2).encode()
        assert whatsapp_webhooks._verify_signature(spaced, meta_signature(self.SECRET, spaced))


class FakeCallbackRepo:
    def __init__(self, rows):
        self._rows = rows
        self.delivered, self.failed = [], []

    def get_by_message_sid(self, sid):
        found = [r for r in self._rows if r["message_sid"] == sid]
        return type("R", (), {"data": found})()

    def mark_delivered(self, row_id):
        self.delivered.append(row_id)

    def mark_failed_from_callback(self, row_id, reason):
        self.failed.append((row_id, reason))


def _app(repo=None):
    """A bare app carrying only this router, so a test exercises the real
    route — path, signature check and all — without the whole CRM."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(whatsapp_webhooks.router)
    return TestClient(app, raise_server_exceptions=False)


class TestVerificationHandshake:
    """Meta calls this once when the Callback URL is saved. Two traps, both
    already paid for by routes/webhooks.py: the hub.* parameters are DOTTED and
    need alias=, and the challenge must come back as PLAIN TEXT — a JSON
    wrapper is rejected and the URL will not save."""

    TOKEN = "verify-me"

    def test_echoes_the_challenge_as_plain_text(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", self.TOKEN)
        r = _app().get("/webhooks/whatsapp", params={
            "hub.mode": "subscribe",
            "hub.verify_token": self.TOKEN,
            "hub.challenge": "1158201444",
        })
        assert r.status_code == 200
        assert r.text == "1158201444", "a JSON wrapper here means Meta refuses the URL"

    def test_rejects_the_wrong_token(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", self.TOKEN)
        r = _app().get("/webhooks/whatsapp", params={
            "hub.mode": "subscribe", "hub.verify_token": "guessed", "hub.challenge": "x",
        })
        assert r.status_code == 403

    def test_rejects_when_no_token_is_configured(self, monkeypatch):
        # Fails closed: an empty expected token must not match an empty sent one.
        monkeypatch.setenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
        r = _app().get("/webhooks/whatsapp", params={
            "hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "x",
        })
        assert r.status_code == 403


class TestStatusWebhook:
    """Meta's statuses are not all terminal, so they are mapped explicitly
    rather than 'anything that is not delivered is a failure'."""

    SECRET = "test-app-secret"

    def _client(self, monkeypatch, repo):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", self.SECRET)
        monkeypatch.setattr(whatsapp_webhooks, "repo", repo)
        return _app()

    def _post(self, client, payload):
        body = json.dumps(payload).encode()
        return client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={
                "X-Hub-Signature-256": meta_signature(self.SECRET, body),
                "Content-Type": "application/json",
            },
        )

    def _row(self, message_id="wamid.1", row_id="r1"):
        return {"id": row_id, "message_sid": message_id, "status": "Submitted"}

    def test_delivered_marks_delivered(self, monkeypatch):
        repo = FakeCallbackRepo([self._row()])
        r = self._post(self._client(monkeypatch, repo), status_payload("wamid.1", "delivered"))
        assert r.status_code == 200 and repo.delivered == ["r1"]

    def test_read_also_counts_as_delivered(self, monkeypatch):
        repo = FakeCallbackRepo([self._row()])
        self._post(self._client(monkeypatch, repo), status_payload("wamid.1", "read"))
        assert repo.delivered == ["r1"]

    def test_failure_records_the_error_code(self, monkeypatch):
        # The code is the difference between "this number is not on WhatsApp"
        # and "our template was rejected" — different fixes entirely.
        repo = FakeCallbackRepo([self._row()])
        self._post(self._client(monkeypatch, repo),
                   status_payload("wamid.1", "failed", code=131026,
                                  title="Receiver is not a WhatsApp user"))
        assert repo.failed and "131026" in repo.failed[0][1]

    def test_in_flight_status_changes_nothing(self, monkeypatch):
        repo = FakeCallbackRepo([self._row()])
        r = self._post(self._client(monkeypatch, repo), status_payload("wamid.1", "sent"))
        assert r.status_code == 200
        assert repo.delivered == [] and repo.failed == []

    def test_a_batch_applies_every_status(self, monkeypatch):
        # THE DIFFERENCE FROM TWILIO: one request, several statuses. Reading
        # only the first would silently lose the rest.
        repo = FakeCallbackRepo([self._row("wamid.1", "r1"), self._row("wamid.2", "r2")])
        payload = {"entry": [{"changes": [{"value": {"statuses": [
            {"id": "wamid.1", "status": "delivered"},
            {"id": "wamid.2", "status": "delivered"},
        ]}}]}]}
        r = self._post(self._client(monkeypatch, repo), payload)
        assert r.status_code == 200
        assert repo.delivered == ["r1", "r2"]

    def test_unknown_id_asks_meta_to_retry(self, monkeypatch):
        # THE RACE: Meta can call back before our worker has written the id.
        # Answering 200 would mean Meta never resends and the row sits at
        # Submitted forever. A 5xx makes it come back when the row exists.
        repo = FakeCallbackRepo([])
        r = self._post(self._client(monkeypatch, repo),
                       status_payload("wamid.unknown", "delivered"))
        assert r.status_code == 503

    def test_a_known_status_still_applies_when_another_is_unknown(self, monkeypatch):
        # The batch is processed BEFORE asking for a retry, so one unrecorded
        # id does not hold up rows we can already resolve. Meta redelivers the
        # whole batch and re-marking Delivered is idempotent.
        repo = FakeCallbackRepo([self._row("wamid.1", "r1")])
        payload = {"entry": [{"changes": [{"value": {"statuses": [
            {"id": "wamid.1", "status": "delivered"},
            {"id": "wamid.missing", "status": "delivered"},
        ]}}]}]}
        r = self._post(self._client(monkeypatch, repo), payload)
        assert r.status_code == 503, "still asks for a retry"
        assert repo.delivered == ["r1"], "but the known row was applied"

    def test_a_non_status_event_is_acked_untouched(self, monkeypatch):
        # Template approvals and account updates arrive on this same endpoint.
        # None is a delivery receipt, and none is an error.
        repo = FakeCallbackRepo([self._row()])
        r = self._post(self._client(monkeypatch, repo),
                       {"entry": [{"changes": [{"value": {"event": "APPROVED"}}]}]})
        assert r.status_code == 200
        assert repo.delivered == [] and repo.failed == []

    def test_a_bad_signature_is_rejected_before_any_write(self, monkeypatch):
        repo = FakeCallbackRepo([self._row()])
        client = self._client(monkeypatch, repo)
        r = client.post(
            "/webhooks/whatsapp",
            content=json.dumps(status_payload("wamid.1", "delivered")).encode(),
            headers={"X-Hub-Signature-256": "sha256=forged",
                     "Content-Type": "application/json"},
        )
        assert r.status_code == 403
        assert repo.delivered == [], "nothing may be written on a bad signature"

    def test_unparseable_body_is_acked_not_retried_forever(self, monkeypatch):
        # The signature already proved it came from Meta, so a 500 here would
        # make Meta retry the same broken body indefinitely.
        repo = FakeCallbackRepo([])
        client = self._client(monkeypatch, repo)
        body = b"not json at all"
        r = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"X-Hub-Signature-256": meta_signature(self.SECRET, body),
                     "Content-Type": "application/json"},
        )
        assert r.status_code == 200


class TestLicensedLabels:
    """The column stores the Meta form's own vocabulary ("2021_or_later") so it
    can never disagree with extra_data. Turning that into English happens here,
    at the presentation edge — a broker must not be shown a database value."""

    def test_both_real_answers_become_readable(self):
        assert whatsapp._LICENSED_LABELS["2020_or_before"] == "2020 or before"
        assert whatsapp._LICENSED_LABELS["2021_or_later"] == "2021 or later"

    def test_an_unmapped_future_value_shows_through(self):
        # If the form gains a third option, the broker sees the raw answer
        # rather than "Not provided" — a real answer hidden is worse than an
        # ugly one shown.
        v = whatsapp._variables({"name": "X", "phone_e164": "+1",
                                 "licensed_in_canada": "2019_or_earlier"})
        assert v["3"] == "2019_or_earlier"


class TestParserExtractsTheAnswer:
    """The field key was read off real Meta leads, not guessed — it carries
    "first" and a trailing question mark, both easy to get wrong."""

    def _fd(self, key, value):
        from meta_lead_parser import parse_field_data
        return parse_field_data([
            {"name": "full_name", "values": ["Ruban"]},
            {"name": key, "values": [value]},
        ])["licensed_in_canada"]

    KEY = "when_were_you_first_licensed_in_canada?"

    def test_reads_both_real_answers(self):
        assert self._fd(self.KEY, "2021_or_later") == "2021_or_later"
        assert self._fd(self.KEY, "2020_or_before") == "2020_or_before"

    def test_a_form_without_the_question_yields_none(self):
        from meta_lead_parser import parse_field_data
        assert parse_field_data([{"name": "full_name", "values": ["X"]}])["licensed_in_canada"] is None

    def test_an_unrecognised_answer_is_none_not_a_guess(self):
        # lead.licensed_in_canada has a check constraint, and a plausible-but-
        # wrong value in a rating qualifier is worse than none: a broker acts
        # on it. The raw answer is still in extra_data.
        assert self._fd(self.KEY, "sometime in 2015") is None

    def test_a_reworded_key_does_not_match(self):
        # Meta derives the key from the question text, so an edited form
        # produces a new key. It must fail to match rather than mis-read.
        assert self._fd("when_were_you_licensed_in_canada?", "2021_or_later") is None


# ── the reminder sender (two numbers, one WABA) ──────────────────────────
class TestReminderSender:
    """Reminders go out from a second number so a broker can mute that thread
    without losing new-lead alerts. Both numbers sit in the same WhatsApp
    Business Account, which is what makes this a config change rather than a
    fresh round of template approvals — a template belongs to the ACCOUNT.
    """

    def test_unset_falls_back_to_the_main_sender(self, monkeypatch):
        # The safe default in both directions: deploying before the second
        # number exists changes nothing, and clearing the variable is the
        # one-step rollback to a single thread.
        monkeypatch.setattr(whatsapp_client, "_PHONE_NUMBER_ID", "main")
        monkeypatch.setattr(whatsapp_client, "_REMINDER_PHONE_NUMBER_ID", "")
        assert whatsapp_client.reminder_phone_number_id() == "main"

    def test_set_returns_the_second_sender(self, monkeypatch):
        monkeypatch.setattr(whatsapp_client, "_PHONE_NUMBER_ID", "main")
        monkeypatch.setattr(whatsapp_client, "_REMINDER_PHONE_NUMBER_ID", "second")
        assert whatsapp_client.reminder_phone_number_id() == "second"

    def test_send_template_defaults_to_the_main_sender(self):
        # Every existing call site passes nothing and must keep sending from
        # the number it always has — the parameter is opt-in.
        import inspect

        sig = inspect.signature(whatsapp_client.send_template)
        assert sig.parameters["phone_number_id"].default is None

    def test_the_sender_changes_the_url_not_the_payload(self, monkeypatch):
        # Meta names the sender ONLY in the path segment; nothing in the body
        # identifies it. A test asserting otherwise would enshrine a field that
        # does not exist.
        captured = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"messages": [{"id": "wamid.TEST"}]}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["payload"] = kwargs.get("json")
            return _Resp()

        monkeypatch.setattr(whatsapp_client.httpx, "post", fake_post)
        monkeypatch.setattr(whatsapp_client, "_ACCESS_TOKEN", "tok")
        monkeypatch.setattr(whatsapp_client, "_PHONE_NUMBER_ID", "main")

        whatsapp_client.send_template(
            "+14165550134", {"1": "a"}, template="t", phone_number_id="second"
        )
        assert "/second/messages" in captured["url"]
        assert "main" not in captured["url"]
        assert "second" not in str(captured["payload"])
