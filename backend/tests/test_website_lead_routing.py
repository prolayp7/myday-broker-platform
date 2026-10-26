"""Website lead routing — the SQL in migration 0127, against a real database.

WHY THE REAL DATABASE. The behaviour worth testing is Postgres's: the advisory
lock, the oldest-first walk, the per-row exception block, what a website lead
does NOT write. A mock would only re-assert what it was told.

STAGING ONLY. Marked dbtest, so tests/conftest.py skips this file unless
SUPABASE_URL names the disposable staging project.

THE QUEUE IS GLOBAL, unlike a group's pool: the drain walks every waiting
website submission in the database, and gives it to every switched-on broker.
So each test first checks staging is quiet — nothing waiting, nobody switched
on — and skips with a reason if it is not, rather than handing a stranger's
lead to a test broker or asserting against someone else's rows.

NOT COVERED HERE, deliberately: the temporary-error rule (a non-22/23 error
rolls the whole run back and leaves everything waiting). Provoking one needs
DDL — a trigger that raises — which PostgREST cannot run. It is checked by hand
in the SQL editor instead; the snippet is in the migration's apply notes.
"""

import itertools
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv

pytestmark = pytest.mark.dbtest

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
    pytest.skip("needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY", allow_module_level=True)

from fastapi import HTTPException  # noqa: E402

from db import supabase  # noqa: E402
from schemas.website_lead import (  # noqa: E402
    UpdateWebsiteBrokerRequest,
    UpdateWebsiteBrokersRequest,
    WebsiteLeadPayload,
)
from security import hash_password  # noqa: E402
from services import website_lead  # noqa: E402

PREFIX = "ZZTEST"

# A distinct, increasing submitted_at per submission. Leads made in one drain
# run share created_at (now() is the transaction's start), so it cannot order
# them; requested_at, copied from submitted_at, can.
_BASE = datetime(2026, 9, 29, 14, 0, 0, tzinfo=timezone.utc)
_SEQ = itertools.count()


def _payload(coverage="auto", **overrides) -> dict:
    driving = coverage in ("auto", "auto-home", "auto-tenant")
    body = {
        "version": 1,
        "submission_id": str(uuid.uuid4()),
        "submitted_at": (_BASE + timedelta(seconds=next(_SEQ))).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverage": coverage,
        "name": f"{PREFIX} Website",
        "phone": "+15550001111",
        "email": f"{PREFIX.lower()}-{uuid.uuid4().hex[:10]}@example.com",
        "address": "1 King St W, Toronto, ON",
        "address_location": None,
        "call_time": "asap",
        "convictions": "none" if driving else None,
        "auto_claims": "none" if driving else None,
        "home_claims": "none" if coverage in ("home", "auto-home") else None,
        "consent_notice": "Test notice.",
        "page": "https://example.test/quote/",
    }
    body.update(overrides)
    return body


class Sandbox:
    """Throwaway community, groups and brokers, plus every website submission
    and lead this test made. Teardown removes exactly those."""

    def __init__(self):
        self.community_id = supabase.table("community").insert(
            {"name": f"{PREFIX} {uuid.uuid4().hex[:8]}"}
        ).execute().data[0]["id"]
        self.group_ids, self.broker_ids, self.event_ids = [], [], []
        self.extra_lead_ids = []
        self.saved_pointer = supabase.table("website_rotation_state").select(
            "last_broker_id").eq("id", 1).execute().data[0]["last_broker_id"]

    def group(self):
        row = supabase.table("broker_group").insert({
            "community_id": self.community_id,
            "name": f"{PREFIX} G {uuid.uuid4().hex[:8]}",
        }).execute().data[0]
        self.group_ids.append(row["id"])
        return row["id"]

    def broker(self, group_id, label, on=None, cap=None, **cols):
        row = supabase.table("broker").insert({
            "group_id": group_id,
            "name": f"{PREFIX} {label}",
            "email": f"{PREFIX.lower()}-{uuid.uuid4().hex[:12]}@example.com",
            "phone": "+15550000000",
            "password_hash": hash_password(uuid.uuid4().hex),
            "role": "broker",
            "lines_of_authority": [],
            **cols,
        }).execute().data[0]
        self.broker_ids.append(row["id"])
        if on is not None or cap is not None:
            self.set(row["id"], on=on, cap=cap)
        return row["id"]

    def set(self, broker_id, on=None, cap=None):
        """Through the real service, so the lock is the real lock."""
        return website_lead.update_broker(
            uuid.UUID(broker_id), UpdateWebsiteBrokerRequest(is_on=on, daily_cap=cap)
        )

    def submit(self, coverage="auto", **overrides):
        """Through the real intake service, exactly as the webhook stores it."""
        body = _payload(coverage, **overrides)
        assert website_lead.record_submission(WebsiteLeadPayload.model_validate(body), body)
        event = self._event(body["submission_id"])
        self.event_ids.append(event["id"])
        return event["id"]

    def submit_raw(self, raw: dict):
        """Bypasses validation — for a submission the drain must reject."""
        row = supabase.table("webhook_event").insert({
            "source": "website",
            "dedup_key": f"website:{uuid.uuid4()}",
            "raw_payload": raw,
            "signature_valid": True,
            "status": "waiting",
        }).execute().data[0]
        self.event_ids.append(row["id"])
        return row["id"]

    def _event(self, submission_id):
        return supabase.table("webhook_event").select("*").eq(
            "dedup_key", f"website:{submission_id}").execute().data[0]

    def status(self, event_id):
        return supabase.table("webhook_event").select("status, failure_reason").eq(
            "id", event_id).execute().data[0]

    def leads(self):
        """This sandbox's website leads, oldest SUBMISSION first (see _BASE),
        with who got them."""
        if not self.broker_ids:
            return []
        return (
            supabase.table("lead")
            .select("id, group_id, assigned_broker_id, source, requested_at, "
                    "combined_policy_type, extra_data, created_at, name")
            .eq("source", "website")
            .in_("assigned_broker_id", self.broker_ids)
            .order("requested_at")
            .execute().data
        )

    def drain(self):
        result = website_lead.drain()
        assert result is not None, "the drain failed — see the log"
        return result

    def cleanup(self):
        lead_ids = [l["id"] for l in self.leads()] + self.extra_lead_ids
        for lead_id in lead_ids:
            for table in ("whatsapp_notification", "lead_stage_history",
                          "lead_assignment", "lead_delivery", "website_lead_delivery",
                          "meta_signal"):
                supabase.table(table).delete().eq("lead_id", lead_id).execute()
            supabase.table("audit_log").delete().eq("entity_id", lead_id).execute()
            supabase.table("lead").delete().eq("id", lead_id).execute()
        for event_id in self.event_ids:
            supabase.table("webhook_event").delete().eq("id", event_id).execute()
        supabase.table("website_rotation_state").update(
            {"last_broker_id": self.saved_pointer}).eq("id", 1).execute()
        for group_id in self.group_ids:
            supabase.table("broker_group_rotation").delete().eq("group_id", group_id).execute()
        for broker_id in self.broker_ids:
            supabase.table("website_broker_setting").delete().eq("broker_id", broker_id).execute()
            supabase.table("audit_log").delete().eq("entity_id", broker_id).execute()
            supabase.table("broker").delete().eq("id", broker_id).execute()
        for group_id in self.group_ids:
            supabase.table("broker_group").delete().eq("id", group_id).execute()
        supabase.table("community").delete().eq("id", self.community_id).execute()


@pytest.fixture
def box():
    waiting = supabase.table("webhook_event").select("id", count="exact").eq(
        "source", "website").eq("status", "waiting").execute().count
    switched_on = supabase.table("website_broker_setting").select(
        "broker_id", count="exact").eq("is_on", True).execute().count
    if waiting or switched_on:
        pytest.skip(
            f"staging is not quiet: {waiting} website submission(s) waiting, "
            f"{switched_on} broker(s) switched on. The queue is global, so these "
            "tests would hand real rows to test brokers. Clear them first."
        )
    sandbox = Sandbox()
    try:
        yield sandbox
    finally:
        sandbox.cleanup()


# ── who gets it ──────────────────────────────────────────────────────────
class TestRotation:

    def test_turns_rotate_across_groups_and_wrap(self, box):
        """Groups are ignored: three switched-on brokers in two groups take
        turns, and each lead lands in its broker's own group."""
        g1, g2 = box.group(), box.group()
        brokers = {
            box.broker(g1, "A", on=True, cap=10): g1,
            box.broker(g2, "B", on=True, cap=10): g2,
            box.broker(g2, "C", on=True, cap=10): g2,
        }
        for _ in range(6):
            box.submit()
        box.drain()

        leads = box.leads()
        assert len(leads) == 6
        got = [l["assigned_broker_id"] for l in leads]
        assert sorted(got.count(b) for b in brokers) == [2, 2, 2], "an equal share each"
        # Turns, not blocks: nobody gets two in a row.
        assert all(got[i] != got[i + 1] for i in range(5)), got
        for lead in leads:
            assert lead["group_id"] == brokers[lead["assigned_broker_id"]]

    def test_the_off_switch_and_the_limit_are_respected(self, box):
        g = box.group()
        on = box.broker(g, "On", on=True, cap=1)
        box.broker(g, "Off", on=False, cap=10)
        box.broker(g, "Untouched")  # no row at all = off

        events = [box.submit() for _ in range(3)]
        result = box.drain()

        assert [l["assigned_broker_id"] for l in box.leads()] == [on]
        assert result["waiting"] == 2
        assert [box.status(e)["status"] for e in events] == ["processed", "waiting", "waiting"]

    def test_pauses_are_respected(self, box):
        g = box.group()
        box.broker(g, "Paused", on=True, cap=10, self_paused=True)
        box.broker(g, "Blocked", on=True, cap=10, is_blocked_by_admin=True)
        box.broker(g, "Inactive", on=True, cap=10, is_active=False)
        working = box.broker(g, "Working", on=True, cap=10)

        for _ in range(3):
            box.submit()
        box.drain()

        assert {l["assigned_broker_id"] for l in box.leads()} == {working}


# ── the queue ────────────────────────────────────────────────────────────
class TestQueue:

    def test_oldest_first_once_someone_is_switched_on(self, box):
        g = box.group()
        broker = box.broker(g, "Later")
        first = box.submit(name=f"{PREFIX} First")
        second = box.submit(name=f"{PREFIX} Second")
        box.drain()
        assert box.status(first)["status"] == "waiting"

        box.set(broker, on=True, cap=1)
        box.drain()

        assert box.status(first)["status"] == "processed"
        assert box.status(second)["status"] == "waiting", "the newer one must not jump the older"
        assert [l["name"] for l in box.leads()] == [f"{PREFIX} First"]

    def test_no_new_assignment_after_a_switch_off_commits(self, box):
        g = box.group()
        broker = box.broker(g, "Leaving", on=True, cap=10)
        box.set(broker, on=False)

        event = box.submit()
        box.drain()

        assert box.leads() == []
        assert box.status(event)["status"] == "waiting"

    def test_a_permanently_bad_submission_fails_and_the_queue_moves_on(self, box):
        g = box.group()
        broker = box.broker(g, "B", on=True, cap=10)
        # Oldest, and can never become a lead: lead.name is NOT NULL.
        bad = _payload()
        del bad["name"]
        bad_id = box.submit_raw(bad)
        good_id = box.submit()

        result = box.drain()

        assert box.status(bad_id)["status"] == "failed"
        assert box.status(bad_id)["failure_reason"]
        assert box.status(good_id)["status"] == "processed"
        assert result["failed"] == 1
        # The bad row's attempt did not spend the broker's turn or limit.
        assert [l["assigned_broker_id"] for l in box.leads()] == [broker]

    def test_a_backlog_bigger_than_a_batch_drains_completely(self, box, monkeypatch):
        """0128: the drain works in batches so no single call can outlast the
        statement timeout. A backlog of several batches must still empty in
        one drain() — oldest first across the batch boundaries."""
        monkeypatch.setattr(website_lead, "DRAIN_BATCH", 2)
        g = box.group()
        box.broker(g, "B", on=True, cap=10)
        events = [box.submit(name=f"{PREFIX} N{i}") for i in range(5)]

        box.drain()

        assert [box.status(e)["status"] for e in events] == ["processed"] * 5
        assert [l["name"] for l in box.leads()] == [f"{PREFIX} N{i}" for i in range(5)]

    def test_a_duplicate_submission_makes_one_lead(self, box):
        g = box.group()
        box.broker(g, "B", on=True, cap=10)
        body = _payload()
        parsed = WebsiteLeadPayload.model_validate(body)
        assert website_lead.record_submission(parsed, body) is True
        assert website_lead.record_submission(parsed, body) is False
        box.event_ids.append(box._event(body["submission_id"])["id"])
        box.drain()
        assert len(box.leads()) == 1


# ── what a website lead looks like ───────────────────────────────────────
class TestTheLead:

    def test_the_lead_carries_the_answers_a_broker_reads(self, box):
        g = box.group()
        box.broker(g, "B", on=True, cap=10)
        box.submit("auto-home", call_time="2026-09-30T10", auto_claims="3-plus",
                   submitted_at="2026-09-29T14:32:05Z")
        box.drain()

        lead = box.leads()[0]
        assert lead["source"] == "website"
        # Not set at intake — the broker picks it, which creates the applicant
        # and property rows (0127's header).
        assert lead["combined_policy_type"] is None
        assert lead["requested_at"].startswith("2026-09-29T14:32:05")
        assert lead["extra_data"] == {
            "Coverage": "Auto + Home",
            "Address": "1 King St W, Toronto, ON",
            "Call time": "Wed Sep 30, 10 AM",
            "Tickets (3 yrs)": "None",
            "At-fault claims (6 yrs)": "3+",
            "Home claims (6 yrs)": "None",
        }

    def test_a_tenant_lead_shows_no_unasked_questions(self, box):
        g = box.group()
        box.broker(g, "B", on=True, cap=10)
        box.submit("tenant")
        box.drain()
        extra = box.leads()[0]["extra_data"]
        assert extra["Coverage"] == "Tenant" and extra["Call time"] == "ASAP"
        assert not {"Tickets (3 yrs)", "At-fault claims (6 yrs)", "Home claims (6 yrs)"} & extra.keys()


# ── Facebook is untouched ────────────────────────────────────────────────
class TestFacebookIsUntouched:

    def test_no_facebook_delivery_cost_or_meta_signal(self, box):
        g = box.group()
        broker = box.broker(g, "B", on=True, cap=10)
        box.submit()
        box.drain()
        lead_id = box.leads()[0]["id"]

        # No lead_delivery row = nothing against the Facebook cap and no
        # lead-cost charge, which the 0088 trigger stamps on that row.
        assert supabase.table("lead_delivery").select("lead_id").eq(
            "lead_id", lead_id).execute().data == []
        assert supabase.table("lead_delivery").select("lead_id").eq(
            "broker_id", broker).execute().data == []
        assert supabase.table("meta_signal").select("id").eq(
            "lead_id", lead_id).execute().data == []
        # The website's own log has it instead.
        assert supabase.table("website_lead_delivery").select("broker_id").eq(
            "lead_id", lead_id).execute().data == [{"broker_id": broker}]
        # And the same custody trail a Facebook lead gets.
        assert supabase.table("lead_assignment").select("reason").eq(
            "lead_id", lead_id).execute().data == [{"reason": "Initial Claim"}]

    def test_the_facebook_backlog_drain_leaves_a_website_lead_alone(self, box):
        """No code path leaves a website lead unassigned today, but if one
        ever exists, the Facebook backlog drain (run by the daily reset and on
        unpause) must not hand it out by Facebook rules."""
        g = box.group()
        box.broker(g, "FB", daily_cap=10, max_cap=10)
        orphan = supabase.table("lead").insert({
            "group_id": g, "source": "website", "name": f"{PREFIX} Orphan",
            "phone": "+15550001111", "email": f"{PREFIX.lower()}-o@example.com",
        }).execute().data[0]["id"]
        box.extra_lead_ids.append(orphan)

        supabase.rpc("assign_pending_leads_for_group", {"p_group_id": g}).execute()

        row = supabase.table("lead").select("assigned_broker_id").eq(
            "id", orphan).execute().data[0]
        assert row["assigned_broker_id"] is None


# ── the admin screen's data ──────────────────────────────────────────────
class TestBoard:

    def test_the_board_shows_switch_limit_count_and_queue(self, box):
        g = box.group()
        on = box.broker(g, "On", on=True, cap=1)
        off = box.broker(g, "Off")
        box.submit()
        box.submit()
        box.drain()

        board = website_lead.get_board()
        rows = {r["broker_id"]: r for r in board["brokers"]}
        assert rows[on]["is_on"] is True and rows[on]["daily_cap"] == 1
        assert rows[on]["today"] == 1
        assert rows[off]["is_on"] is False and rows[off]["today"] == 0
        assert board["waiting"] >= 1

    def test_the_board_says_the_community_and_the_start_date(self, box):
        # Migration 0129.
        g = box.group()
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        started = box.broker(g, "Started")
        not_yet = box.broker(g, "Not yet", eligible_from=tomorrow)

        rows = {r["broker_id"]: r for r in website_lead.get_board()["brokers"]}
        assert rows[started]["community_id"] == box.community_id
        assert rows[started]["community_name"].startswith(PREFIX)
        assert rows[started]["eligible_from"] is None
        assert rows[not_yet]["eligible_from"] is not None


# ── changing many brokers at once (0129) ─────────────────────────────────
class TestBulk:

    def test_one_call_changes_everyone_listed(self, box):
        g = box.group()
        a, b = box.broker(g, "A", on=False, cap=3), box.broker(g, "B")
        website_lead.update_brokers(UpdateWebsiteBrokersRequest(
            broker_ids=[uuid.UUID(a), uuid.UUID(b)], is_on=True))

        rows = {r["broker_id"]: r for r in website_lead.get_board()["brokers"]}
        assert rows[a]["is_on"] is True and rows[b]["is_on"] is True
        # The field not sent is left as it was; a broker with no row yet
        # starts at 0.
        assert rows[a]["daily_cap"] == 3 and rows[b]["daily_cap"] == 0

    def test_one_unknown_broker_changes_nobody(self, box):
        g = box.group()
        real = box.broker(g, "Real")

        with pytest.raises(HTTPException) as err:
            website_lead.update_brokers(UpdateWebsiteBrokersRequest(
                broker_ids=[uuid.UUID(real), uuid.uuid4()], is_on=True))

        assert err.value.status_code == 404
        rows = {r["broker_id"]: r for r in website_lead.get_board()["brokers"]}
        assert rows[real]["is_on"] is False
