"""When the Meta `Lead` conversion is queued (migration 0096).

LEAD MEANS "a Meta lead was downloaded into the CRM". Not "a broker picked it
up", and not "the broker captured the details" — Meta's own implementation
guide asks for this event on all downloaded leads.

0095 briefly held it until the applicant was complete, on the reasoning that
reporting never-answered leads taught Meta to find more form-fillers. Meta
advised the opposite: sending only positive signals leaves their model with no
baseline, so it cannot tell which ads bring reachable people from which do not.
Measured two days after 0095 shipped — 11 Meta leads arrived, 0 were reported,
and 0 had even been opened by a broker, so none would ever have fired.

These tests hit the real database, because the thing under test IS a database
trigger. Stubbing it would assert that a Python function calls another Python
function and prove nothing about what Postgres does inside the transaction.
"""

import sys
import uuid

import pytest

sys.path.insert(0, ".")

from db import supabase  # noqa: E402

# THIS FILE WRITES TO WHATEVER SUPABASE_URL RESOLVES TO, and was missing the
# guard conftest.py added after ZZTEST rows reached production on 2026-09-20.
# It creates real communities, broker groups, leads and drivers; teardown is in
# a `finally` so it cleans up even on a failed assertion, but an interrupted run
# leaves them behind — which is exactly how that incident happened.
#
# Found because a plain `pytest` on a machine configured for production ran it
# against production. Every other db-backed module in this directory already
# carried this line; this one did not.
pytestmark = pytest.mark.dbtest

PREFIX = "ZZTEST"

# ONE DATASET (0133): every signal is a 'legacy' row, which is Auto Insurance.
LEAD_DATASETS = {"legacy"}



# A complete applicant, used only to prove that applicant activity does NOT
# queue a second Lead. The nine fields are what _REQUIRED_APPLICANT_FIELDS
# still asks for before Quote Done — that guard is unchanged by 0096.
COMPLETE = {
    "first_name": "Anna",
    "last_name": "Lakshmi",
    "phone": "+15550001111",
    "email": "anna@example.com",
    "date_of_birth": "1990-01-01",
    "gender": "Female",
    "street_address": "1 King St W",
    "city": "Toronto",
    "postal_code": "M5H 1A1",
}


class Sandbox:
    """Tracks its own rows so teardown deletes exactly what it made."""

    def __init__(self):
        self.community_id = None
        self.group_id = None
        self.lead_ids = []

    def setup(self):
        self.community_id = supabase.table("community").insert(
            {"name": f"{PREFIX} {uuid.uuid4().hex[:8]}"}
        ).execute().data[0]["id"]
        self.group_id = supabase.table("broker_group").insert({
            "community_id": self.community_id,
            "name": f"{PREFIX} G {uuid.uuid4().hex[:8]}",
        }).execute().data[0]["id"]

    def lead(self, source="meta", **cols):
        row = supabase.table("lead").insert({
            "group_id": self.group_id,
            "source": source,
            "name": f"{PREFIX} L",
            "phone": "+15550001111",
            "email": f"{PREFIX.lower()}-{uuid.uuid4().hex[:12]}@example.com",
            **cols,
        }).execute().data[0]
        self.lead_ids.append(row["id"])
        return row

    def applicant(self, lead_id, is_principal=True, **overrides):
        return supabase.table("driver").insert({
            "lead_id": lead_id,
            "is_principal": is_principal,
            "relationship_to_applicant": "Applicant" if is_principal else "Spouse",
            **overrides,
        }).execute().data[0]

    def signals(self, lead_id, event_name="Lead"):
        return supabase.table("meta_signal").select("*").eq(
            "lead_id", lead_id
        ).eq("event_name", event_name).execute().data

    def teardown(self):
        for lead_id in self.lead_ids:
            supabase.table("driver").delete().eq("lead_id", lead_id).execute()
            for table in ("meta_signal", "lead_stage_history", "lead_assignment",
                          "premium_history"):
                supabase.table(table).delete().eq("lead_id", lead_id).execute()
            supabase.table("audit_log").delete().eq("entity_id", lead_id).execute()
            supabase.table("lead").delete().eq("id", lead_id).execute()
        if self.group_id:
            supabase.table("broker_group").delete().eq("id", self.group_id).execute()
        if self.community_id:
            supabase.table("community").delete().eq("id", self.community_id).execute()


@pytest.fixture
def sandbox():
    s = Sandbox()
    s.setup()
    try:
        yield s
    finally:
        s.teardown()


class TestTheLeadFiresOnArrival:
    def test_a_meta_lead_queues_one_lead_immediately(self, sandbox):
        """THE CORE BEHAVIOUR. No broker, no assignment, no applicant row —
        and Meta is told, because the lead exists. This is the baseline event
        every other signal is measured against.
        """
        lead = sandbox.lead(source="meta")

        rows = sandbox.signals(lead["id"])
        assert len(rows) == 1
        assert {r["dataset_target"] for r in rows} == LEAD_DATASETS
        assert all(r["status"] == "Pending" for r in rows)

    def test_it_fires_before_anyone_has_opened_the_lead(self, sandbox):
        """The case 0095 got wrong. 11 real leads sat unreported because no
        broker had opened them; a Lead event must not depend on that.
        """
        lead = sandbox.lead(source="meta")

        drivers = supabase.table("driver").select("id").eq(
            "lead_id", lead["id"]
        ).execute().data
        assert drivers == [], "no applicant exists yet"
        assert {r["dataset_target"] for r in sandbox.signals(lead["id"])} == LEAD_DATASETS

    def test_qualification_is_not_required(self, sandbox):
        """At creation nobody has looked at the lead, so there is no judgement
        to gate on. Gating here would mean never telling Meta a lead arrived.

        Unlike CompleteRegistration, which DOES require
        is_qualified — the case that breaks if someone copies the gate
        from 0078.
        """
        lead = sandbox.lead(source="meta", is_qualified=False)
        assert {r["dataset_target"] for r in sandbox.signals(lead["id"])} == LEAD_DATASETS

    def test_triggered_by_is_null(self, sandbox):
        """Nobody caused this — the lead simply arrived."""
        lead = sandbox.lead(source="meta")
        assert sandbox.signals(lead["id"])[0]["triggered_by"] is None


class TestTheLeadDoesNotFire:
    def test_a_manual_lead_queues_nothing(self, sandbox):
        """GUARD RAIL. A manually created lead has no Meta ad to report a
        conversion against.
        """
        lead = sandbox.lead(source="manual")
        assert sandbox.signals(lead["id"]) == []


class TestOneLeadFanOutPerLead:
    def test_completing_the_applicant_queues_no_second_lead(self, sandbox):
        """The arrival row is the only Lead event — never a second one.

        0095's trigger fired on every applicant update; if any remnant of it
        survived, filling in the applicant would try to insert a second
        (lead_id, 'Lead') pair.
        """
        lead = sandbox.lead(source="meta")
        driver = sandbox.applicant(lead["id"], **COMPLETE)

        # The arrival row, and creating the applicant added nothing.
        assert len(sandbox.signals(lead["id"])) == len(LEAD_DATASETS)

        # And an edit afterwards still adds nothing.
        supabase.table("driver").update({"city": "Ottawa"}).eq(
            "id", driver["id"]
        ).execute()
        assert len(sandbox.signals(lead["id"])) == len(LEAD_DATASETS)

    def test_an_additional_driver_queues_nothing(self, sandbox):
        lead = sandbox.lead(source="meta")
        before = len(sandbox.signals(lead["id"]))

        sandbox.applicant(lead["id"], is_principal=False, **COMPLETE)

        assert len(sandbox.signals(lead["id"])) == before


# ── Where the later events go (0133) ────────────────────────────────────
#
# STAGING ONLY, and only while staging's META_CAPI_DATASET_ID points at the
# Staging Test dataset: these leads are source 'meta', so the drain would really
# send their rows wherever staging is configured.
class TestFollowUpsReachAutoInsurance:
    CLEAN_DRIVER = {"licensed_5y_plus": True, "convictions_3y": 0,
                    "at_fault_claims_6y": 0}

    @staticmethod
    def _qualify(lead_id):
        supabase.rpc("set_lead_qualification", {
            "p_lead_id": lead_id, "p_is_qualified": True,
        }).execute()

    @staticmethod
    def _sell(lead_id, **premium):
        """A sale as the database sees it: a premium row and a sale stage.
        update_lead_stage is not used because it needs an assigned broker,
        and what is under test is the reconciler it calls."""
        supabase.table("premium_history").insert({"lead_id": lead_id, **premium}).execute()
        supabase.table("lead").update({"stage": "Sold"}).eq("id", lead_id).execute()
        supabase.rpc("ensure_conversion_signals", {"p_lead_id": lead_id}).execute()

    def test_qualified_queues_one_complete_registration(self, sandbox):
        lead = sandbox.lead(source="meta", combined_policy_type="Auto and Tenant")
        sandbox.applicant(lead["id"], **self.CLEAN_DRIVER)
        self._qualify(lead["id"])
        rows = sandbox.signals(lead["id"], "CompleteRegistration")
        assert [r["dataset_target"] for r in rows] == ["legacy"]

    def test_driver_facts_no_longer_queue_anything(self, sandbox):
        """The Preferred trigger is gone (0133): a DASH report writing the
        at-fault count changes no signal."""
        lead = sandbox.lead(source="meta", combined_policy_type="Auto and Homeowner")
        driver = sandbox.applicant(lead["id"], licensed_5y_plus=True, convictions_3y=0)
        self._qualify(lead["id"])
        before = supabase.table("meta_signal").select("id").eq("lead_id", lead["id"]).execute().data

        supabase.table("driver").update({"at_fault_claims_6y": 0}).eq("id", driver["id"]).execute()

        after = supabase.table("meta_signal").select("id").eq("lead_id", lead["id"]).execute().data
        assert len(after) == len(before) == 2   # Lead + CompleteRegistration

    def test_a_sale_carries_the_yearly_premium(self, sandbox):
        lead = sandbox.lead(source="meta", combined_policy_type="Auto and Tenant")
        sandbox.applicant(lead["id"])
        self._qualify(lead["id"])
        self._sell(lead["id"], new_auto_premium_annual="1600", new_tenant_premium_annual="800")

        [row] = sandbox.signals(lead["id"], "Purchase")
        assert row["dataset_target"] == "legacy"
        assert float(row["payload"]["total_new_premium_at_purchase"]) == 2400

    def test_a_monthly_premium_reaches_meta_annualised(self, sandbox):
        """Typed as 200 a month, reported as 2400 a year: the value is
        total_new_premium_annual, which the database derives (0106)."""
        lead = sandbox.lead(source="meta", combined_policy_type="Auto Only")
        sandbox.applicant(lead["id"])
        self._sell(lead["id"], new_premium_unit="month", new_auto_premium_monthly="200")

        [row] = sandbox.signals(lead["id"], "Purchase")
        assert float(row["payload"]["total_new_premium_at_purchase"]) == 2400

    def test_a_sale_without_a_premium_queues_no_purchase(self, sandbox):
        lead = sandbox.lead(source="meta", combined_policy_type="Auto Only")
        sandbox.applicant(lead["id"])
        supabase.table("lead").update({"stage": "Sold"}).eq("id", lead["id"]).execute()
        supabase.rpc("ensure_conversion_signals", {"p_lead_id": lead["id"]}).execute()
        assert sandbox.signals(lead["id"], "Purchase") == []

    def test_an_empty_premium_row_queues_no_purchase(self, sandbox):
        """total_new_premium_annual sums with coalesce, so an empty row reads 0
        rather than null. A Purchase worth $0 must not be sent."""
        lead = sandbox.lead(source="meta", combined_policy_type="Auto Only")
        sandbox.applicant(lead["id"])
        self._sell(lead["id"])
        assert sandbox.signals(lead["id"], "Purchase") == []

    def test_a_manual_lead_still_queues_nothing(self, sandbox):
        lead = sandbox.lead(source="manual", combined_policy_type="Auto Only")
        sandbox.applicant(lead["id"])
        supabase.table("lead").update({"is_qualified": True}).eq("id", lead["id"]).execute()
        self._sell(lead["id"], new_auto_premium_annual="2400")
        assert supabase.table("meta_signal").select("id").eq(
            "lead_id", lead["id"]).execute().data == []
