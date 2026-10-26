"""Integration tests for the lead-assignment SQL — assign_lead and
admin_assign_lead.

WHY THIS FILE EXISTS. These two functions decide who receives a lead, and
therefore who earns the commission on it. Until now they had NO tests at all:
they are PL/pgSQL, so nothing in the unit suite can reach them, and every
verification of them has been someone running a script by hand and reading the
output. That proves the code worked once, on one afternoon. It does not stay
proved.

Everything asserted here was verified manually at least once — the 3/5/10
rotation, the isolation across communities, the migration 0071 guards. This
file is that work made permanent, so the next edit to assign_lead is checked
by the suite instead of by another review.

THE TESTS TALK TO THE REAL DATABASE, deliberately. The whole point is the
behaviour of Postgres — advisory locks, ordering, the gate — and a mock of
Supabase would only ever re-assert what the mock was told to do. Every test
builds its own throwaway community, group, brokers and leads, and removes them
in a fixture teardown that runs even when the test fails.

SKIPPED, NOT FAILED, without credentials, so `pytest` still works for someone
who has only checked out the repo.

Marked `dbtest`. Run the fast suite with:  pytest -m "not dbtest"
"""

import os
import uuid
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest
from dotenv import load_dotenv

pytestmark = pytest.mark.dbtest

# Load backend/.env the same way db.py does before deciding whether to skip —
# checking os.environ alone would skip on every developer machine, where the
# credentials live in that file rather than the shell.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# db.py reads SUPABASE_* at import and raises KeyError without them, which
# would break collection rather than skip it. Check before importing anything.
if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
    pytest.skip(
        "needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY — these tests run "
        "against a real database",
        allow_module_level=True,
    )

from db import supabase  # noqa: E402
from security import hash_password  # noqa: E402

# Every row this file creates is named with this prefix, so a crashed run
# leaves something searchable rather than anonymous debris.
PREFIX = "ZZTEST"


class Sandbox:
    """A throwaway community + group, and whatever is built inside it.

    Tracks its own rows so teardown deletes exactly what it made — never a
    blanket "delete where name like ZZ%", which would race another developer
    running the same suite.
    """

    def __init__(self):
        self.community_id = None
        # Every community made, including extras a test asks for. Deleted in
        # teardown AFTER the groups inside them — a test deleting its own
        # extra community in a finally block runs BEFORE this fixture's
        # teardown and trips the broker_group foreign key.
        self.extra_community_ids = []
        self.group_ids = []
        self.broker_ids = []
        self.lead_ids = []

    def community(self):
        row = supabase.table("community").insert(
            {"name": f"{PREFIX} {uuid.uuid4().hex[:8]}"}
        ).execute().data[0]
        if self.community_id is None:
            self.community_id = row["id"]
        self.extra_community_ids.append(row["id"])
        return row["id"]

    def group(self, community_id=None, **cols):
        row = supabase.table("broker_group").insert({
            "community_id": community_id or self.community_id,
            "name": f"{PREFIX} G {uuid.uuid4().hex[:8]}",
            **cols,
        }).execute().data[0]
        self.group_ids.append(row["id"])
        return row

    def broker(self, group_id, label, **cols):
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
        return row

    def lead(self, group_id, label="L", **cols):
        row = supabase.table("lead").insert({
            "group_id": group_id,
            "source": "manual",
            "name": f"{PREFIX} {label}",
            "phone": "+15550001111",
            "email": f"{PREFIX.lower()}-{uuid.uuid4().hex[:12]}@example.com",
            **cols,
        }).execute().data[0]
        self.lead_ids.append(row["id"])
        return row

    def applicant(self, lead_id, **overrides):
        """A COMPLETE applicant, so the lead can reach Quote Done.

        services/lead.py::_REQUIRED_APPLICANT_FIELDS refuses the move without
        all six — they are what Meta matches a conversion on. Tests whose
        subject is something else (commission, issued dates, qualification)
        need this so the applicant guard is not what refuses them; the tests
        that ARE about the guard build their own partial rows instead.
        """
        row = {
            "lead_id": lead_id,
            "is_principal": True,
            "relationship_to_applicant": "Applicant",
            "name": f"{PREFIX} Applicant",
            # FIRST AND LAST, because that is what completeness is judged on —
            # Meta hashes fn and ln separately, so `name` alone cannot satisfy
            # it. Seeding only `name` here would make every "complete
            # applicant" fixture incomplete.
            "first_name": PREFIX,
            "last_name": "Applicant",
            "phone": "+15550001111",
            "email": f"{PREFIX.lower()}-{uuid.uuid4().hex[:10]}@example.com",
            "date_of_birth": "1990-04-17",
            "gender": "Female",
            # ALL FOUR ADDRESS PARTS. This fixture set only street_address
            # until 2026-08-07 — the same omission the guard itself had, so
            # the suite approved an "applicant" that would have sent Meta no
            # location at all.
            "street_address": "1 King St W",
            "city": "Toronto",
            "province": "Ontario",
            "postal_code": "M5H 1A1",
            # LONG ENOUGH INSURED to pass the four-year rule
            # (services/lead.py::MIN_INSURED_YEARS). Added for the same reason
            # the four address parts were: a fixture calling itself "a complete
            # applicant" has to satisfy every guard that runs before a stage
            # move, or the suite approves a lead the real system would refuse.
            #
            # A FIXED DATE, not `today - 4 years`. A relative one would sit
            # exactly on the boundary and flip with any change to the rule; 2010
            # is unambiguous and stays unambiguous. The tests that are ABOUT the
            # boundary override this with their own dates.
            "insured_since": "2010-06-01",
            **overrides,
        }
        return supabase.table("driver").insert(row).execute().data[0]

    def assign(self, lead_id):
        """The real round-robin. Returns the broker id, or None."""
        return supabase.rpc("assign_lead", {"p_lead_id": lead_id}).execute().data

    def admin_assign(self, lead_id, broker_id, admin_id):
        """The real admin path. Returns None on success, else the message."""
        try:
            supabase.rpc("admin_assign_lead", {
                "p_lead_id": lead_id,
                "p_broker_id": broker_id,
                "p_admin_id": admin_id,
            }).execute()
            return None
        except Exception as err:
            return getattr(err, "message", None) or str(err)

    def cleanup(self):
        # ORDER MATTERS. broker_group_rotation.last_assigned_broker_id
        # references broker(id), so the rotation row has to go before the
        # broker — deleting brokers first fails on a foreign key and leaves
        # the rest of the teardown unrun. Found the hard way.
        for group_id in self.group_ids:
            supabase.table("broker_group_rotation").delete().eq("group_id", group_id).execute()
        for lead_id in self.lead_ids:
            # driver first: property/vehicle cascade from IT, not from lead, so
            # a lead that grew a driver (setting a combined policy type creates
            # one) cannot be deleted until the driver goes. Found the hard way —
            # the foreign key aborts teardown midway and orphans everything
            # after it.
            supabase.table("driver").delete().eq("lead_id", lead_id).execute()
            for table in ("premium_history", "reminder",
                          "meta_signal", "lead_stage_history", "lead_assignment",
                          "lead_delivery"):
                supabase.table(table).delete().eq("lead_id", lead_id).execute()
            supabase.table("audit_log").delete().eq("entity_id", lead_id).execute()
            supabase.table("lead").delete().eq("id", lead_id).execute()
        # BY ACTOR, not just by entity. audit_log.actor_broker_id references
        # broker(id), so any row this sandbox's brokers wrote — a retried
        # meta_signal, say, whose entity_id is the SIGNAL's id and not a
        # lead's — blocks the broker delete below and aborts the rest of
        # teardown. The per-lead sweep above only catches rows keyed to a lead.
        for broker_id in self.broker_ids:
            supabase.table("audit_log").delete().eq("actor_broker_id", broker_id).execute()
        for broker_id in self.broker_ids:
            supabase.table("audit_log").delete().eq("entity_id", broker_id).execute()
            supabase.table("broker").delete().eq("id", broker_id).execute()
        for group_id in self.group_ids:
            supabase.table("broker_group").delete().eq("id", group_id).execute()
        for community_id in self.extra_community_ids:
            supabase.table("community").delete().eq("id", community_id).execute()


@pytest.fixture
def sandbox():
    box = Sandbox()
    box.community()
    try:
        yield box
    finally:
        box.cleanup()


# ── The rule: everyone's share first, then extras ────────────────────────
class TestFairShareThenOvertime:
    """The 3/5/10 case, which is how the rule was described in plain terms:
    unequal caps, everyone rotating until they hit their own limit, and
    overtime opening only once the whole group's share is used up."""

    def test_unequal_caps_fill_exactly_then_overtime_opens(self, sandbox):
        group = sandbox.group()
        spec = [("B1", 3, 6, True), ("B2", 5, 5, False), ("B3", 10, 12, True)]
        brokers = {}
        for label, daily, ceiling, ready in spec:
            row = sandbox.broker(group["id"], label, daily_cap=daily,
                                 max_cap=ceiling, ready_for_more=ready)
            brokers[row["id"]] = label

        # 18 = the group's fair share (3 + 5 + 10). Six more to exercise
        # overtime and then exhaustion.
        got = []
        for i in range(24):
            lead = sandbox.lead(group["id"], f"L{i}")
            broker_id = sandbox.assign(lead["id"])
            got.append(brokers.get(broker_id) if broker_id else None)

        level_1 = Counter(g for g in got[:18] if g)
        assert None not in got[:18], "the fair share must all be placed"
        assert level_1 == {"B1": 3, "B2": 5, "B3": 10}, level_1

        # B2 never opted in, so it must not appear past the fair share — this
        # is the assertion that would catch overtime leaking to everyone.
        assert "B2" not in got[18:], got[18:]

        totals = Counter(g for g in got if g)
        assert totals["B1"] <= 6, totals
        assert totals["B3"] <= 12, totals

    def test_turns_alternate_rather_than_filling_one_broker_first(self, sandbox):
        """Three brokers on equal caps must take turns, not have the first
        one absorb its whole cap before the second sees anything."""
        group = sandbox.group()
        brokers = {sandbox.broker(group["id"], f"B{i}", daily_cap=3,
                                  max_cap=3)["id"]: f"B{i}" for i in (1, 2, 3)}

        got = []
        for i in range(6):
            lead = sandbox.lead(group["id"], f"L{i}")
            got.append(brokers.get(sandbox.assign(lead["id"])))

        # NOT asserting a fixed starting broker: the rotation is ordered by
        # uuid, so which broker goes first is arbitrary. What must hold is
        # that each round covers all three before anyone repeats.
        assert len(set(got[:3])) == 3, got
        assert len(set(got[3:6])) == 3, got

    def test_a_broker_who_did_not_opt_in_gets_no_overtime(self, sandbox):
        group = sandbox.group()
        keen = sandbox.broker(group["id"], "Keen", daily_cap=1, max_cap=5,
                              ready_for_more=True)
        quiet = sandbox.broker(group["id"], "Quiet", daily_cap=1, max_cap=5,
                               ready_for_more=False)
        names = {keen["id"]: "Keen", quiet["id"]: "Quiet"}

        got = []
        for i in range(6):
            lead = sandbox.lead(group["id"], f"L{i}")
            got.append(names.get(sandbox.assign(lead["id"])))

        assert Counter(got[:2]) == {"Keen": 1, "Quiet": 1}, got
        assert set(got[2:]) == {"Keen"}, got

    def test_overtime_waits_until_everyone_has_had_their_share(self, sandbox):
        """The gate. One broker still under cap must keep overtime shut, even
        when a volunteer has room — otherwise the guaranteed share stops being
        guaranteed."""
        group = sandbox.group()
        # Volunteer with a big ceiling, and someone slow who has not been
        # given their share yet.
        eager = sandbox.broker(group["id"], "Eager", daily_cap=1, max_cap=9,
                               ready_for_more=True)
        slow = sandbox.broker(group["id"], "Slow", daily_cap=4, max_cap=4)
        names = {eager["id"]: "Eager", slow["id"]: "Slow"}

        got = []
        for i in range(5):
            lead = sandbox.lead(group["id"], f"L{i}")
            got.append(names.get(sandbox.assign(lead["id"])))

        # 5 leads, share is 1 + 4. Eager takes its 1; the remaining 4 must all
        # go to Slow rather than Eager's overtime, even though Eager could
        # take them.
        assert Counter(got) == {"Eager": 1, "Slow": 4}, got


# ── Commission follows the policy it is paid on ──────────────────────────
class TestIssuedAtIsRecorded:
    """premium_history.issued_at was READ and never WRITTEN.

    repositories/premium_history.py::issued_since_for_broker filters
    `.gte("issued_at", since)` to answer "what did this broker write this
    month" — and nothing anywhere set the column. So that query returned
    NOTHING, permanently, and every issued-premium figure was zero. Its own
    docstring even explains the intent: "rows with no issued_at are not yet
    real business and are excluded by the filter rather than summed as zero."
    Every row qualified as not-yet-real.

    ONTARIO TIME, not the server's. Railway runs UTC, so date.today() there
    rolls over at 8pm Ontario — a policy issued at 9pm on the 31st landed in
    the NEXT month's figures. commission.py already defined _TZ for exactly
    this reason and then did not use it.
    """

    def _premium_row(self, lead_id):
        rows = supabase.table("premium_history").select(
            "issued_at, total_new_premium_annual").eq("lead_id", lead_id).execute().data
        return rows[0] if rows else None

    def _seed_premium(self, lead_id, annual="100"):
        """An ANNUAL figure, and the parameter is named for it now. It was
        called `monthly` while writing new_auto_premium_annual on a row whose
        unit defaults to year — so it was always annual, and the name was a
        leftover from the schema 0106 replaced."""
        return supabase.table("premium_history").insert({
            "lead_id": lead_id,
            "new_auto_premium_annual": annual,
        }).execute().data[0]

    @pytest.fixture
    def scene(self, sandbox):
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5)
        # Auto Only, matching the auto premium seeded below. Issued requires a
        # policy type and a premium for every product in it — this suite is
        # about ISSUED DATES, and an incomplete policy must not be what
        # refuses the move.
        lead = sandbox.lead(group["id"], combined_policy_type="Auto Only")
        assert sandbox.assign(lead["id"]) == broker["id"]
        # About ISSUED DATES, not the applicant guard.
        sandbox.applicant(lead["id"])
        return {"sandbox": sandbox, "lead": lead, "broker": broker}

    def _to_issued(self, lead_id, broker_id, issued_on=None):
        """Move to Issued, carrying the date the broker confirmed.

        Defaults to today only so the callers that do not care about the date
        stay short — the endpoint itself has NO default, deliberately: it used
        to stamp today automatically, which is the behaviour these tests exist
        to prove is gone.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        if issued_on is None:
            issued_on = datetime.now(ZoneInfo("America/Toronto")).date()
        return lead_service.update_stage(
            uuid.UUID(lead_id),
            UpdateStageRequest(stage="Issued", issued_at=issued_on),
            uuid.UUID(broker_id),
        )

    def test_reaching_issued_stamps_the_premium_row(self, scene):
        """THE BUG. Fails today: issued_at stays null and the figure is
        invisible to reporting forever."""
        box = scene["sandbox"]
        self._seed_premium(scene["lead"]["id"])
        try:
            self._to_issued(scene["lead"]["id"], scene["broker"]["id"])
            row = self._premium_row(scene["lead"]["id"])
            assert row["issued_at"] is not None, (
                "issued_at was not written, so this policy is invisible to "
                "issued-premium reporting"
            )
        finally:
            supabase.table("premium_history").delete().eq(
                "lead_id", scene["lead"]["id"]).execute()

    def test_the_BROKERS_date_is_stored_not_todays(self, scene):
        """THE POINT OF THE DIALOG.

        issued_at used to be today, set automatically — so a policy the
        carrier wrote on the 30th and the broker recorded on the 2nd landed in
        the following month's figures, with nothing on screen able to correct
        it. A backdated date must survive exactly as given.
        """
        from datetime import date, timedelta

        self._seed_premium(scene["lead"]["id"])
        backdated = date.today() - timedelta(days=9)
        try:
            self._to_issued(scene["lead"]["id"], scene["broker"]["id"], backdated)
            row = self._premium_row(scene["lead"]["id"])
            assert row["issued_at"] == backdated.isoformat(), (
                f"issued_at is {row['issued_at']}, the broker said {backdated}"
            )
        finally:
            supabase.table("premium_history").delete().eq(
                "lead_id", scene["lead"]["id"]).execute()

    def test_issued_without_a_date_is_refused(self, scene):
        """And the lead does NOT move. A guard that refuses after writing is
        not a guard — the policy would sit in Issued, dateless, invisible to
        every report and with nothing saying why."""
        import uuid as _uuid

        from fastapi import HTTPException
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        self._seed_premium(scene["lead"]["id"])
        try:
            with pytest.raises(HTTPException) as err:
                lead_service.update_stage(
                    _uuid.UUID(scene["lead"]["id"]),
                    UpdateStageRequest(stage="Issued"),
                    _uuid.UUID(scene["broker"]["id"]),
                )
            assert err.value.status_code == 422
            assert "date" in err.value.detail.lower()

            stage = supabase.table("lead").select("stage").eq(
                "id", scene["lead"]["id"]).execute().data[0]["stage"]
            assert stage != "Issued", "the stage moved despite the refusal"
        finally:
            supabase.table("premium_history").delete().eq(
                "lead_id", scene["lead"]["id"]).execute()

    def test_a_future_date_is_refused(self, scene):
        """A policy cannot have been issued tomorrow. Worth refusing rather
        than trusting: a year typed as 2027 parks the projection in a month
        nobody will think to look at."""
        import uuid as _uuid
        from datetime import date, timedelta

        from fastapi import HTTPException
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        self._seed_premium(scene["lead"]["id"])
        try:
            with pytest.raises(HTTPException) as err:
                lead_service.update_stage(
                    _uuid.UUID(scene["lead"]["id"]),
                    UpdateStageRequest(
                        stage="Issued", issued_at=date.today() + timedelta(days=1)
                    ),
                    _uuid.UUID(scene["broker"]["id"]),
                )
            assert err.value.status_code == 422
            assert "future" in err.value.detail.lower()
        finally:
            supabase.table("premium_history").delete().eq(
                "lead_id", scene["lead"]["id"]).execute()

    def test_moving_back_off_issued_clears_the_date(self, scene):
        """A lead moved off Issued was not written after all.
        A policy that was not written after all must not keep a date saying it
        was — the month's figures would count a sale that did not happen."""
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        box = scene["sandbox"]
        self._seed_premium(scene["lead"]["id"])
        try:
            self._to_issued(scene["lead"]["id"], scene["broker"]["id"])
            assert self._premium_row(scene["lead"]["id"])["issued_at"] is not None

            lead_service.update_stage(
                uuid.UUID(scene["lead"]["id"]),
                UpdateStageRequest(stage="Pending"),
                uuid.UUID(scene["broker"]["id"]),
            )
            assert self._premium_row(scene["lead"]["id"])["issued_at"] is None, (
                "a lead moved off Issued kept its issued date"
            )
        finally:
            supabase.table("premium_history").delete().eq(
                "lead_id", scene["lead"]["id"]).execute()

    def test_a_lead_with_no_premium_row_does_not_error(self, scene):
        """Issued requires a premium at the service layer, but the write must
        not assume a row exists — an admin correction or a direct SQL path
        could reach Issued without one."""
        from datetime import date

        # Deliberately no premium row. update_stage refuses Issued without one,
        # so drive the SQL directly to exercise the write path itself.
        supabase.rpc("update_lead_stage", {
            "p_lead_id": scene["lead"]["id"],
            "p_broker_id": scene["broker"]["id"],
            "p_new_stage": "Issued",
            "p_issued_at": date.today().isoformat(),
        }).execute()
        assert self._premium_row(scene["lead"]["id"]) is None


# ── The stage and its date are one write ─────────────────────────────────
class TestIssuedIsAtomicWithItsDate:
    """THE STAGE AND THE DATE CANNOT DISAGREE.

    They used to be two calls. update_lead_stage committed the stage, and then
    services/lead.py::_stamp_issued_at made a SECOND PostgREST call to write
    premium_history.issued_at — one that caught and logged its own failures,
    described in its own docstring as "best effort".

    So a blip between the two left:

        lead.stage                = 'Issued'
        premium_history.issued_at = null

    The screen says the policy was issued. Every issued-premium figure counts
    by issued_at (repositories/premium_history.py::issued_since_for_broker), so
    the policy is absent from the month's business and NOTHING reports a
    problem. The inverse is worse: a failed clear on the way out leaves a
    policy that was never written counted as business that was.

    The old code's defence was that a missing date "is fixed by re-entering the
    stage" — but the stage dropdown does not offer the stage a lead is already
    on, so re-entering Issued was impossible. The named recovery did not exist.

    Migration 0085 moved the write inside the function. These tests pin that
    there is no longer a window: revert 0085 and the two below fail, because
    the date write becomes separately skippable again.
    """

    def _premium_row(self, lead_id):
        rows = supabase.table("premium_history").select(
            "issued_at").eq("lead_id", lead_id).execute().data
        return rows[0] if rows else None

    @pytest.fixture
    def scene(self, sandbox):
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5)
        lead = sandbox.lead(group["id"], combined_policy_type="Auto Only")
        assert sandbox.assign(lead["id"]) == broker["id"]
        sandbox.applicant(lead["id"])
        supabase.table("premium_history").insert({
            "lead_id": lead["id"],
            "new_auto_premium_annual": "100",
        }).execute()
        yield {"lead": lead, "broker": broker}
        supabase.table("premium_history").delete().eq(
            "lead_id", lead["id"]).execute()

    def test_the_date_lands_in_the_same_call_as_the_stage(self, scene):
        """One RPC does both. Not "both happen" — both happen ATOMICALLY,
        which is what the direct RPC call here demonstrates: there is no
        second call left for a failure to fall between."""
        from datetime import date

        supabase.rpc("update_lead_stage", {
            "p_lead_id": scene["lead"]["id"],
            "p_broker_id": scene["broker"]["id"],
            "p_new_stage": "Issued",
            "p_issued_at": date.today().isoformat(),
        }).execute()

        stage = supabase.table("lead").select("stage").eq(
            "id", scene["lead"]["id"]).execute().data[0]["stage"]
        assert stage == "Issued"
        assert self._premium_row(scene["lead"]["id"])["issued_at"] is not None, (
            "the stage moved to Issued without its date — exactly the split "
            "0085 exists to make impossible"
        )

    def test_leaving_issued_clears_the_date_in_the_same_call(self, scene):
        """The dangerous direction. A stale date counts business that does not
        exist, where a missing one merely under-reports."""
        from datetime import date

        supabase.rpc("update_lead_stage", {
            "p_lead_id": scene["lead"]["id"],
            "p_broker_id": scene["broker"]["id"],
            "p_new_stage": "Issued",
            "p_issued_at": date.today().isoformat(),
        }).execute()
        assert self._premium_row(scene["lead"]["id"])["issued_at"] is not None

        supabase.rpc("update_lead_stage", {
            "p_lead_id": scene["lead"]["id"],
            "p_broker_id": scene["broker"]["id"],
            "p_new_stage": "Pending",
            "p_issued_at": None,
        }).execute()

        assert self._premium_row(scene["lead"]["id"])["issued_at"] is None, (
            "a lead that left Issued kept its date, so a policy that was not "
            "written is still counted as business that was"
        )

    def test_a_failed_stage_change_writes_no_date(self, scene):
        """The other half of atomicity, and the one a two-call design got
        wrong in reverse: the ownership guard raises BEFORE either write, so a
        rejected move must leave the date untouched too."""
        import uuid as _uuid
        from datetime import date

        stranger = _uuid.uuid4()
        with pytest.raises(Exception):
            supabase.rpc("update_lead_stage", {
                "p_lead_id": scene["lead"]["id"],
                "p_broker_id": str(stranger),
                "p_new_stage": "Issued",
                "p_issued_at": date.today().isoformat(),
            }).execute()

        assert self._premium_row(scene["lead"]["id"])["issued_at"] is None, (
            "a refused stage change still dated the premium row"
        )

    def _history_count(self, lead_id):
        return len(supabase.table("lead_stage_history").select(
            "id").eq("lead_id", lead_id).execute().data or [])

    def test_re_entering_issued_overwrites_the_date(self, scene):
        """THE CORRECTION PATH, which had no way to be reached at all.

        Overwrite-only by design: no history row, no second table. The screen
        reaches it with an explicit "Edit issued date" button
        (routes/leads/NewPolicyPage.tsx) rather than by moving the lead
        somewhere else and back, which would write two false rows into
        lead_stage_history.
        """
        from datetime import date, timedelta

        wrong = date.today() - timedelta(days=30)
        right = date.today() - timedelta(days=2)

        for when in (wrong, right):
            supabase.rpc("update_lead_stage", {
                "p_lead_id": scene["lead"]["id"],
                "p_broker_id": scene["broker"]["id"],
                "p_new_stage": "Issued",
                "p_issued_at": when.isoformat(),
            }).execute()

        assert self._premium_row(scene["lead"]["id"])["issued_at"] == right.isoformat(), (
            "correcting the issued date did not overwrite the wrong one"
        )

    def test_correcting_the_date_writes_NO_stage_history_row(self, scene):
        """THE TEST THE FIRST VERSION OF THIS SUITE WAS MISSING.

        0085 wrote the history row unconditionally, so a date correction —
        which is Issued -> Issued — appended `from_stage='Issued',
        to_stage='Issued'`. The lead did not move. lead_stage_history is the
        record of a lead's journey (0029), and a row saying it went from Issued
        to Issued is a fact that never happened; fixing a typo would age the
        history with entries indistinguishable from real progress.

        This was not hypothetical. The suite for 0085 asserted only on the
        date, so it wrote four such rows into the live table before anyone read
        it. Counting rows is the assertion that would have caught it, which is
        why it is here now. 0086 returns early on the correction path.
        """
        from datetime import date, timedelta

        supabase.rpc("update_lead_stage", {
            "p_lead_id": scene["lead"]["id"],
            "p_broker_id": scene["broker"]["id"],
            "p_new_stage": "Issued",
            "p_issued_at": date.today().isoformat(),
        }).execute()

        # The real move above is allowed exactly one row. Everything after it
        # is a correction and must add none.
        after_the_move = self._history_count(scene["lead"]["id"])

        for days in (2, 5):
            supabase.rpc("update_lead_stage", {
                "p_lead_id": scene["lead"]["id"],
                "p_broker_id": scene["broker"]["id"],
                "p_new_stage": "Issued",
                "p_issued_at": (date.today() - timedelta(days=days)).isoformat(),
            }).execute()

        assert self._history_count(scene["lead"]["id"]) == after_the_move, (
            "correcting the issued date appended a stage-history row saying "
            "the lead moved from Issued to Issued"
        )

        # And the correction still did its actual job.
        assert self._premium_row(scene["lead"]["id"])["issued_at"] == (
            date.today() - timedelta(days=5)
        ).isoformat()

    def test_a_correction_still_refuses_a_stranger(self, scene):
        """The early return sits AFTER the ownership guard, deliberately.

        Returning before it would let anyone rewrite the date on a lead they do
        not hold — a quieter version of the hole the guard exists to close.
        """
        import uuid as _uuid
        from datetime import date, timedelta

        supabase.rpc("update_lead_stage", {
            "p_lead_id": scene["lead"]["id"],
            "p_broker_id": scene["broker"]["id"],
            "p_new_stage": "Issued",
            "p_issued_at": date.today().isoformat(),
        }).execute()

        with pytest.raises(Exception):
            supabase.rpc("update_lead_stage", {
                "p_lead_id": scene["lead"]["id"],
                "p_broker_id": str(_uuid.uuid4()),
                "p_new_stage": "Issued",
                "p_issued_at": (date.today() - timedelta(days=20)).isoformat(),
            }).execute()

        assert self._premium_row(scene["lead"]["id"])["issued_at"] == date.today().isoformat(), (
            "a broker who does not hold this lead rewrote its issued date"
        )


# ── Open follow-ups travel with the lead ─────────────────────────────────
class TestReassignmentMovesOpenReminders:
    """migrations/0061 states the rule outright, in the reminder table's own
    comment on broker_id:

        "and MOVED when a lead is reassigned — an open promise belongs to
         whoever holds the lead now, not to whoever made it. (contact_log does
         the opposite: a call Sarah made stays Sarah's forever, because it is a
         record of the past. A reminder is a debt against the future.)"

    admin_assign_lead never did it. So broker A kept seeing a follow-up for a
    customer they no longer had, and broker B never learned one existed — the
    promise to ring someone back simply evaporated at the moment of handover.

    OPEN ONLY. A Done or Dismissed reminder is history: broker A really did
    make that call, and rewriting it to B would falsify the record. Only the
    outstanding work follows the customer.
    """

    def _reminder(self, lead_id, broker_id, **cols):
        return supabase.table("reminder").insert({
            "lead_id": lead_id, "broker_id": broker_id,
            "kind": "Callback", "note": f"{PREFIX} follow up",
            "due_at": "2099-01-01T12:00:00+00:00",
            **cols,
        }).execute().data[0]

    def _owner(self, reminder_id):
        return supabase.table("reminder").select("broker_id").eq(
            "id", reminder_id).execute().data[0]["broker_id"]

    @pytest.fixture
    def scene(self, sandbox):
        group = sandbox.group()
        scene = {
            "sandbox": sandbox,
            "group": group,
            "admin": sandbox.broker(group["id"], "Admin", role="admin"),
            "a": sandbox.broker(group["id"], "BrokerA", daily_cap=5),
            "b": sandbox.broker(group["id"], "BrokerB", daily_cap=5),
        }
        lead = sandbox.lead(group["id"])
        assert sandbox.admin_assign(lead["id"], scene["a"]["id"], scene["admin"]["id"]) is None
        scene["lead"] = lead
        return scene

    def _cleanup_reminders(self, lead_id):
        supabase.table("reminder").delete().eq("lead_id", lead_id).execute()

    def test_an_open_reminder_follows_the_lead(self, scene):
        """THE BUG. Fails against the pre-fix function."""
        box, lead = scene["sandbox"], scene["lead"]
        reminder = self._reminder(lead["id"], scene["a"]["id"])
        try:
            assert box.admin_assign(lead["id"], scene["b"]["id"], scene["admin"]["id"]) is None
            assert self._owner(reminder["id"]) == scene["b"]["id"], (
                "the follow-up stayed with the broker who no longer holds the lead"
            )
        finally:
            self._cleanup_reminders(lead["id"])

    def test_every_open_reminder_moves_not_just_one(self, scene):
        box, lead = scene["sandbox"], scene["lead"]
        reminders = [self._reminder(lead["id"], scene["a"]["id"]) for _ in range(3)]
        try:
            box.admin_assign(lead["id"], scene["b"]["id"], scene["admin"]["id"])
            assert all(self._owner(r["id"]) == scene["b"]["id"] for r in reminders)
        finally:
            self._cleanup_reminders(lead["id"])

    @pytest.mark.parametrize("closed_status", ["Done", "Dismissed"])
    def test_a_closed_reminder_stays_with_whoever_closed_it(self, scene, closed_status):
        """History, not an outstanding promise. Broker A really did deal with
        it, and moving the row would rewrite that."""
        box, lead = scene["sandbox"], scene["lead"]
        # reminder_closed_has_timestamp: the table refuses a Done or Dismissed
        # row with no completed_at, so a closed reminder always says WHEN.
        reminder = self._reminder(
            lead["id"], scene["a"]["id"],
            status=closed_status,
            completed_at="2026-01-01T12:00:00+00:00",
            completed_by=scene["a"]["id"],
        )
        try:
            box.admin_assign(lead["id"], scene["b"]["id"], scene["admin"]["id"])
            assert self._owner(reminder["id"]) == scene["a"]["id"], (
                f"a {closed_status} reminder was rewritten to the new holder"
            )
        finally:
            self._cleanup_reminders(lead["id"])

    def test_another_leads_reminders_are_untouched(self, scene):
        """Scoped by lead_id. A broker's other customers must not be disturbed
        by a reassignment that has nothing to do with them."""
        box = scene["sandbox"]
        other = box.lead(scene["group"]["id"], "other")
        assert box.admin_assign(other["id"], scene["a"]["id"], scene["admin"]["id"]) is None
        elsewhere = self._reminder(other["id"], scene["a"]["id"])
        try:
            box.admin_assign(scene["lead"]["id"], scene["b"]["id"], scene["admin"]["id"])
            assert self._owner(elsewhere["id"]) == scene["a"]["id"]
        finally:
            self._cleanup_reminders(other["id"])

    def test_a_lead_with_no_reminders_reassigns_normally(self, scene):
        """The ordinary case: the extra statement must not require a reminder
        to exist."""
        box, lead = scene["sandbox"], scene["lead"]
        assert box.admin_assign(lead["id"], scene["b"]["id"], scene["admin"]["id"]) is None
        holder = supabase.table("lead").select("assigned_broker_id").eq(
            "id", lead["id"]).execute().data[0]["assigned_broker_id"]
        assert holder == scene["b"]["id"]


# ── A lead can come back to a stage it has already been ──────────────────
class TestStagesCanBeReEntered:
    """"Any stage to any stage" has to survive a Meta lead going backwards.

    A customer goes quiet after a quote and comes back a month later. The
    broker moves the lead Quote Done -> Pending -> Quote Done, and the second
    move used to FAIL:

        duplicate key value violates unique constraint
        "idx_meta_signal_lead_event_active"

    The conversion trigger did a plain INSERT into meta_signal, which carries a
    unique index on (lead_id, event_name) where status <> 'Failed'. Once the
    first signal had been SENT, re-entering the stage violated the index — and
    because the trigger runs inside the stage change, the whole stage change
    rolled back. The broker got a database error and the lead was stuck out of
    that stage permanently.

    An already-queued-or-sent signal is not a reason to refuse the stage
    change. Meta has been told, which is the entire point of the signal.
    """

    def _meta_lead(self, sandbox, group_id, broker_id):
        """A lead that looks like it came from Meta, held by a broker — the
        trigger only fires for source='meta'."""
        lead = sandbox.lead(
            group_id, "meta lead", source="meta",
            platform_leadgen_id=f"{PREFIX}-{uuid.uuid4().hex[:14]}",
            # QUALIFIED, because this class is about RE-ENTRY, not about the
            # gate. Since 0078 an unqualified or unanswered lead emits no
            # signal at all — so without this these tests would count zero
            # signals and "pass" for a reason that has nothing to do with the
            # duplicate they exist to catch. (They did exactly that when the
            # gate landed: 're-entry duplicated the signal: 0'.)
            is_qualified=True,
        )
        assert sandbox.assign(lead["id"]) == broker_id
        return lead

    def _stage(self, lead_id, broker_id, stage):
        """Returns None on success, or the error text."""
        try:
            supabase.rpc("update_lead_stage", {
                "p_lead_id": lead_id, "p_broker_id": broker_id,
                "p_new_stage": stage,
            }).execute()
            return None
        except Exception as err:
            return getattr(err, "message", None) or str(err)

    def _mark_signals_sent(self, lead_id):
        """What the CAPI drain does within 15 minutes of the stage change. The
        bug only bites once a signal is no longer Pending-and-deletable."""
        for row in supabase.table("meta_signal").select("id").eq(
                "lead_id", lead_id).execute().data:
            supabase.table("meta_signal").update({"status": "Sent"}).eq(
                "id", row["id"]).execute()

    def test_quote_done_can_be_re_entered_after_its_signal_was_sent(self, sandbox):
        """THE BUG. Reproduced against the live database before the fix."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"])

        assert self._stage(lead["id"], broker["id"], "Quote Done") is None
        self._mark_signals_sent(lead["id"])
        assert self._stage(lead["id"], broker["id"], "Pending") is None

        error = self._stage(lead["id"], broker["id"], "Quote Done")
        assert error is None, f"could not return to Quote Done: {error}"

        current = supabase.table("lead").select("stage").eq(
            "id", lead["id"]).execute().data[0]["stage"]
        assert current == "Quote Done", current

    def test_sold_can_be_re_entered_after_its_signal_was_sent(self, sandbox):
        """Same shape, and worse: a lead that reached Sold, was corrected back,
        and genuinely sells again could not be marked sold."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"])

        # Sold requires a premium (services/lead.py), but update_lead_stage is
        # the raw SQL path and does not enforce that — this exercises the
        # trigger, not the service guard.
        assert self._stage(lead["id"], broker["id"], "Sold") is None
        self._mark_signals_sent(lead["id"])
        assert self._stage(lead["id"], broker["id"], "Pending") is None

        error = self._stage(lead["id"], broker["id"], "Sold")
        assert error is None, f"could not return to Sold: {error}"

    def test_re_entry_does_not_create_a_second_signal(self, sandbox):
        """The index exists for a good reason: one Quote Done must not become
        two CompleteRegistration events, or Meta counts one customer twice.
        Allowing the stage change must not cost that guarantee."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"])

        self._stage(lead["id"], broker["id"], "Quote Done")
        self._mark_signals_sent(lead["id"])
        self._stage(lead["id"], broker["id"], "Pending")
        self._stage(lead["id"], broker["id"], "Quote Done")

        signals = supabase.table("meta_signal").select("event_name").eq(
            "lead_id", lead["id"]).eq("event_name", "CompleteRegistration").execute().data
        assert len(signals) == 1, f"re-entry duplicated the signal: {len(signals)}"

    def test_a_FAILED_signal_is_never_duplicated_by_a_stage_re_entry(self, sandbox):
        """REVERSED BY MIGRATION 0112, and this is the defect that forced it.

        This test used to assert that re-entering the stage produced a SECOND
        row, because 0056's unique index carried `where status <> 'Failed'` — so
        a Failed row was outside it and the insert simply succeeded. 0075's own
        table records that as intended: "Failed -> A NEW ROW IS CREATED".

        It was a hole. services/meta_signal.py marks a row Failed when Meta
        ACCEPTED the event but the database write recording it did not — and the
        row id is the event_id Meta deduplicates on. A second row meant a second
        id, so one customer's conversion counted twice and corrupted the exact
        signal the table exists to send.

        0112 removed the predicate: uniqueness now spans every status. Retry is
        an UPDATE in place (repositories/meta_signal.py::reset_to_pending, or the
        code-190 bulk endpoint), which keeps the id and therefore the event_id.
        So the row stays Failed here until someone retries it, and that is
        correct.
        """
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"])

        self._stage(lead["id"], broker["id"], "Quote Done")
        supabase.table("meta_signal").update({"status": "Failed"}).eq(
            "lead_id", lead["id"]).eq("event_name", "CompleteRegistration").execute()

        # THE STAGE MOVE ITSELF MUST STILL SUCCEED. The trigger runs inside the
        # stage transaction, so a conflict that raised instead of being skipped
        # would roll the whole move back and strand the lead — the bug 0075 was
        # written to fix, which must not return.
        self._stage(lead["id"], broker["id"], "Pending")
        assert self._stage(lead["id"], broker["id"], "Quote Done") is None

        rows = supabase.table("meta_signal").select("id, status").eq(
            "lead_id", lead["id"]).eq("event_name", "CompleteRegistration").execute().data
        assert len(rows) == 1, (
            f"a stage re-entry duplicated a signal: {[r['status'] for r in rows]}"
        )
        assert rows[0]["status"] == "Failed", (
            "the failed row is recovered by retry, which updates it in place"
        )

    def test_a_non_meta_lead_never_creates_signals_at_all(self, sandbox):
        """The trigger checks source first. A manual lead has nothing to report
        to Meta, so it moves freely regardless."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = sandbox.lead(group["id"], "manual")
        assert sandbox.assign(lead["id"]) == broker["id"]

        for stage in ("Quote Done", "Pending", "Quote Done", "Sold", "Pending", "Sold"):
            assert self._stage(lead["id"], broker["id"], stage) is None, stage

        signals = supabase.table("meta_signal").select("id").eq(
            "lead_id", lead["id"]).execute().data
        assert signals == [], "a manual lead produced Meta signals"


# ── Opting in picks up what is already waiting ───────────────────────────
class TestOptingInDrainsTheBacklog:
    """The case the overtime feature exists for: a broker finishes their share,
    says "I'll take more", and the leads ALREADY waiting in their group reach
    them.

    Before this, only leads arriving AFTER the click could — so the broker who
    asked for work got nothing until the next lead happened to come in, or
    until the next morning's reset. The leads sat unassigned the whole time.

    These go through services.broker.set_ready_for_more rather than writing the
    column directly, because the drain is the service's job: a test that set
    the flag itself would pass without the fix.
    """

    def _opt_in(self, broker_id, ready=True):
        from schemas.broker import SetReadyForMoreRequest
        from services import broker as broker_service

        return broker_service.set_ready_for_more(
            broker_id, SetReadyForMoreRequest(ready_for_more=ready)
        )

    def _holder(self, lead_id):
        return supabase.table("lead").select("assigned_broker_id").eq(
            "id", lead_id).execute().data[0]["assigned_broker_id"]

    def test_a_lead_already_waiting_reaches_the_broker_who_opts_in(self, sandbox):
        """THE BUG, as a test. Fails against the pre-fix code."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "Solo", daily_cap=1, max_cap=5,
                                ready_for_more=False)

        first = sandbox.lead(group["id"], "first")
        assert sandbox.assign(first["id"]) == broker["id"]

        waiting = sandbox.lead(group["id"], "waiting")
        assert sandbox.assign(waiting["id"]) is None, "should wait — cap is 1"

        self._opt_in(broker["id"])

        assert self._holder(waiting["id"]) == broker["id"], (
            "the lead that was already waiting should have gone to the broker "
            "who just said they would take more"
        )

    def test_the_whole_backlog_is_offered_not_just_one(self, sandbox):
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "Solo", daily_cap=1, max_cap=5,
                                ready_for_more=False)

        sandbox.assign(sandbox.lead(group["id"], "uses the cap")["id"])
        waiting = [sandbox.lead(group["id"], f"w{i}") for i in range(3)]
        for lead in waiting:
            assert sandbox.assign(lead["id"]) is None

        self._opt_in(broker["id"])

        assert all(self._holder(lead["id"]) == broker["id"] for lead in waiting)

    def test_max_cap_still_bounds_the_backlog(self, sandbox):
        """Opting in must not become a way around the overtime ceiling."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "Solo", daily_cap=1, max_cap=3,
                                ready_for_more=False)

        sandbox.assign(sandbox.lead(group["id"], "uses the cap")["id"])
        waiting = [sandbox.lead(group["id"], f"w{i}") for i in range(5)]
        for lead in waiting:
            assert sandbox.assign(lead["id"]) is None

        self._opt_in(broker["id"])

        # daily 1 + overtime up to max 3 = 3 total, so 2 more are taken.
        taken = [lead for lead in waiting if self._holder(lead["id"])]
        assert len(taken) == 2, f"expected 2 more, got {len(taken)}"

    def test_the_backlog_is_shared_by_rotation_not_given_to_the_clicker(self, sandbox):
        """Two volunteers means turn-taking, not first-come. Otherwise a broker
        could toggle the flag to jump the queue."""
        group = sandbox.group()
        one = sandbox.broker(group["id"], "One", daily_cap=1, max_cap=4,
                             ready_for_more=False)
        two = sandbox.broker(group["id"], "Two", daily_cap=1, max_cap=4,
                             ready_for_more=False)

        # Use up both daily caps.
        for i in range(2):
            assert sandbox.assign(sandbox.lead(group["id"], f"d{i}")["id"])
        waiting = [sandbox.lead(group["id"], f"w{i}") for i in range(4)]
        for lead in waiting:
            assert sandbox.assign(lead["id"]) is None

        # Both opt in; the SECOND one triggers the drain that places them.
        self._opt_in(one["id"])
        self._opt_in(two["id"])

        holders = Counter(self._holder(lead["id"]) for lead in waiting)
        assert holders[one["id"]] > 0 and holders[two["id"]] > 0, (
            f"the backlog went to one broker only: {dict(holders)}"
        )

    def test_turning_it_OFF_changes_nothing(self, sandbox):
        """No backlog to offer, and nothing should move."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "Solo", daily_cap=1, max_cap=5,
                                ready_for_more=True)

        sandbox.assign(sandbox.lead(group["id"], "uses the cap")["id"])
        waiting = sandbox.lead(group["id"], "waiting")
        sandbox.assign(waiting["id"])  # goes to overtime, since ready is on

        before = self._holder(waiting["id"])
        self._opt_in(broker["id"], ready=False)
        assert self._holder(waiting["id"]) == before

    def test_a_drain_failure_does_not_fail_the_request(self, sandbox):
        """The flag is already saved by the time the drain runs. If the drain
        errors, the broker IS opted in — the next lead, or tomorrow's reset,
        still reaches them. Reporting a 500 would say their choice failed when
        it did not."""
        from unittest.mock import patch

        from services import broker as broker_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "Solo", daily_cap=1, max_cap=5,
                                ready_for_more=False)

        with patch.object(broker_service.lead_repo, "drain_pending_for_group",
                          side_effect=Exception("database is down")):
            result = self._opt_in(broker["id"])

        assert result["ready_for_more"] is True, "the opt-in must still be saved"


# ── Groups do not interfere with each other ──────────────────────────────
class TestGroupIsolation:
    def test_groups_in_different_communities_run_independently(self, sandbox):
        """Two communities, two groups each, different caps, leads sent
        interleaved the way concurrent webhooks arrive."""
        # Made through the sandbox so its teardown removes it in the right
        # order — after the groups that reference it.
        second_community = sandbox.community()
        groups = {}
        for label, community_id, cap in (
                ("A", sandbox.community_id, 2),
                ("B", sandbox.community_id, 3),
                ("C", second_community, 2),
                ("D", second_community, 3),
            ):
            grp = sandbox.group(community_id=community_id)
            members = {
                sandbox.broker(grp["id"], f"{label}{i}", daily_cap=cap,
                               max_cap=cap)["id"]: f"{label}{i}"
                for i in (1, 2)
            }
            groups[label] = {"id": grp["id"], "cap": cap, "members": members}

        # INTERLEAVED — a round across all four groups at a time, rather than
        # finishing one group before starting the next. Sequential runs would
        # pass even if the groups shared a rotation pointer.
        placed = {label: [] for label in groups}
        for _ in range(4):
            for label, grp in groups.items():
                lead = sandbox.lead(grp["id"], label)
                broker_id = sandbox.assign(lead["id"])
                placed[label].append(grp["members"].get(broker_id) if broker_id else None)

        for label, grp in groups.items():
            share = grp["cap"] * 2
            results = placed[label]
            assert len([r for r in results if r]) == min(4, share), (label, results)
            # Nobody exceeded THEIR OWN group's cap...
            for name, count in Counter(r for r in results if r).items():
                assert count <= grp["cap"], (label, name, count)
            # ...and no lead was ever handed to another group's broker.
            assert all(r is None or r.startswith(label) for r in results), (label, results)

        rotations = supabase.table("broker_group_rotation").select(
            "group_id, last_assigned_broker_id").execute().data
        by_group = {r["group_id"]: r["last_assigned_broker_id"] for r in rotations}
        for label, grp in groups.items():
            pointer = by_group.get(grp["id"])
            assert pointer in grp["members"], (label, pointer)


# ── Who may receive a lead (migration 0071) ──────────────────────────────
class TestAdminAssignmentGuards:
    """0071 closed a hole where an admin could hand a live customer to a
    broker who no longer works leads. Nine such brokers were reachable when it
    was found."""

    @pytest.fixture
    def scene(self, sandbox):
        group = sandbox.group()
        bin_group = sandbox.group(is_recycle_bin=True)
        return {
            "sandbox": sandbox,
            "admin": sandbox.broker(group["id"], "Admin", role="admin"),
            "good": sandbox.broker(group["id"], "Good"),
            "archived": sandbox.broker(group["id"], "Archived", is_archived=True),
            "inactive": sandbox.broker(group["id"], "Inactive", is_active=False),
            "binned": sandbox.broker(bin_group["id"], "Binned"),
            "group": group,
            "bin_group": bin_group,
        }

    @pytest.mark.parametrize(
        "who,expected",
        [("archived", "archived"), ("inactive", "deactivated"), ("binned", "recycle")],
    )
    def test_unfit_brokers_are_refused_by_name(self, scene, who, expected):
        box = scene["sandbox"]
        lead = box.lead(scene["group"]["id"])
        error = box.admin_assign(lead["id"], scene[who]["id"], scene["admin"]["id"])
        assert error is not None, f"{who} was ALLOWED to receive a lead"
        # The message must name the actual state — it is shown to the admin
        # verbatim, and "cannot receive leads" leaves them guessing.
        assert expected in error.lower(), error

    def test_a_normal_broker_is_still_accepted(self, scene):
        box = scene["sandbox"]
        lead = box.lead(scene["group"]["id"])
        assert box.admin_assign(lead["id"], scene["good"]["id"], scene["admin"]["id"]) is None
        holder = supabase.table("lead").select("assigned_broker_id").eq(
            "id", lead["id"]).execute().data[0]
        assert holder["assigned_broker_id"] == scene["good"]["id"]

    def test_leads_can_still_be_moved_AWAY_from_a_binned_broker(self, scene):
        """Offboarding depends on this. Guarding both directions would strand
        every lead the departing broker was holding."""
        box = scene["sandbox"]
        lead = box.lead(scene["bin_group"]["id"],
                        assigned_broker_id=scene["binned"]["id"])
        assert box.admin_assign(lead["id"], scene["good"]["id"], scene["admin"]["id"]) is None
        holder = supabase.table("lead").select("assigned_broker_id").eq(
            "id", lead["id"]).execute().data[0]
        assert holder["assigned_broker_id"] == scene["good"]["id"]

    def test_reassigning_to_the_current_holder_is_refused(self, scene):
        box = scene["sandbox"]
        lead = box.lead(scene["group"]["id"],
                        assigned_broker_id=scene["good"]["id"])
        error = box.admin_assign(lead["id"], scene["good"]["id"], scene["admin"]["id"])
        assert error is not None and "already assigned" in error.lower(), error


# ── A stranded lead must say why (migration 0071) ────────────────────────
class TestUnassignableLeadsRecordAReason:
    """The gate-closed path used to return silently, so a group with nobody
    working stranded its leads with nothing written anywhere. That is the
    failure worth hearing about: 'everyone is at cap' fixes itself tomorrow,
    'nobody is working' needs a person."""

    def _reason_for(self, lead_id):
        rows = supabase.table("audit_log").select("detail").eq(
            "entity_id", lead_id).eq("action", "lead_unassigned").execute().data
        return rows[0]["detail"].get("reason") if rows else None

    def test_no_eligible_broker_is_recorded(self, sandbox):
        group = sandbox.group()
        sandbox.broker(group["id"], "Deactivated", is_active=False)

        lead = sandbox.lead(group["id"])
        assert sandbox.assign(lead["id"]) is None
        assert self._reason_for(lead["id"]) == "no_eligible_broker_in_group"

    def test_everyone_at_cap_is_recorded_with_a_DIFFERENT_reason(self, sandbox):
        """Two distinct reasons, so whatever reads audit_log can tell a full
        group from a broken one."""
        group = sandbox.group()
        sandbox.broker(group["id"], "Solo", daily_cap=1, max_cap=1)

        first = sandbox.lead(group["id"], "first")
        assert sandbox.assign(first["id"]) is not None

        second = sandbox.lead(group["id"], "second")
        assert sandbox.assign(second["id"]) is None
        assert self._reason_for(second["id"]) == "all_brokers_at_cap"

    def test_an_empty_group_strands_the_lead_rather_than_erroring(self, sandbox):
        """A group with no brokers at all is a configuration mistake, not a
        crash — the lead waits and the reason is recorded."""
        group = sandbox.group()
        lead = sandbox.lead(group["id"])
        assert sandbox.assign(lead["id"]) is None
        assert self._reason_for(lead["id"]) == "no_eligible_broker_in_group"


# ── Admins are not in the rotation ───────────────────────────────────────
class TestAdminsNeverReceiveLeads:
    """assign_lead used to check active, blocked, eligible-date, recycle-bin
    and caps — but never role. So an active admin in a working group with a
    daily cap was just another broker as far as the rotation was concerned.

    It had already happened once: an admin created inside a worker's group for
    a live check quietly absorbed the lead that check was about, and the test
    passed for the wrong reason.

    Deliberate assignment is a different thing and stays allowed — an admin can
    still be HANDED a lead. Only the automatic rotation excludes them.
    """

    def test_an_admin_alone_in_a_group_receives_nothing(self, sandbox):
        group = sandbox.group()
        sandbox.broker(group["id"], "Admin", role="admin", daily_cap=10)

        lead = sandbox.lead(group["id"])
        assert sandbox.assign(lead["id"]) is None, (
            "the round-robin gave a lead to an admin"
        )

    def test_the_broker_takes_everything_and_the_admin_takes_none(self, sandbox):
        """The mixed-group case. The admin has plenty of room; the broker is at
        their limit. Every lead past the broker's cap must WAIT rather than
        fall to the admin."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "Broker", daily_cap=2, max_cap=2)
        admin = sandbox.broker(group["id"], "Admin", role="admin", daily_cap=10,
                               max_cap=10)

        got = []
        for i in range(5):
            lead = sandbox.lead(group["id"], f"L{i}")
            got.append(sandbox.assign(lead["id"]))

        assert got[:2] == [broker["id"], broker["id"]], got
        assert all(g is None for g in got[2:]), (
            f"leads past the broker's cap went somewhere: {got[2:]}"
        )
        assert admin["id"] not in got

    def test_an_admin_cannot_take_overtime_either(self, sandbox):
        """Same rule in the second tier. An admin toggling ready_for_more must
        still receive nothing — otherwise the rule only half-applies."""
        group = sandbox.group()
        sandbox.broker(group["id"], "Broker", daily_cap=1, max_cap=1)
        admin = sandbox.broker(group["id"], "Admin", role="admin", daily_cap=5,
                               max_cap=9, ready_for_more=True)

        first = sandbox.lead(group["id"], "first")
        assert sandbox.assign(first["id"]) is not None

        overflow = sandbox.lead(group["id"], "overflow")
        assert sandbox.assign(overflow["id"]) is None, (
            "an admin opted into overtime and took a lead"
        )
        assert self_holder(overflow["id"]) != admin["id"]

    def test_the_gate_is_not_held_open_or_shut_by_an_admin(self, sandbox):
        """THE TEST THAT CATCHES A HALF-APPLIED PREDICATE.

        The eligibility predicate appears five times: level 1, level 2, and
        TWICE inside the gate. Missing one fails silently in opposite
        directions — a gate that excludes admins while a tier does not opens
        overtime early; a tier that excludes them while the gate does not waits
        forever for an admin to reach a cap they will never approach.

        Here the broker is at cap, a volunteer has room, and an admin has room
        too. The volunteer must get it: the admin should be invisible to both
        the gate and the tiers.
        """
        group = sandbox.group()
        sandbox.broker(group["id"], "AtCap", daily_cap=1, max_cap=1)
        volunteer = sandbox.broker(group["id"], "Volunteer", daily_cap=1,
                                   max_cap=5, ready_for_more=True)
        admin = sandbox.broker(group["id"], "Admin", role="admin", daily_cap=9,
                               max_cap=9, ready_for_more=True)

        # Use up both brokers' daily caps.
        placed = [sandbox.assign(sandbox.lead(group["id"], f"d{i}")["id"])
                  for i in range(2)]
        assert admin["id"] not in placed, placed

        overflow = sandbox.lead(group["id"], "overflow")
        got = sandbox.assign(overflow["id"])
        assert got == volunteer["id"], (
            f"overtime went to {got} — expected the volunteer, not the admin "
            "and not nobody"
        )

    def test_a_group_of_only_admins_records_a_reason(self, sandbox):
        """Not silence. An all-admin group receiving leads is a routing
        mistake, and the stranded lead should say so."""
        group = sandbox.group()
        sandbox.broker(group["id"], "Admin", role="admin", daily_cap=10)

        lead = sandbox.lead(group["id"])
        assert sandbox.assign(lead["id"]) is None

        rows = supabase.table("audit_log").select("detail").eq(
            "entity_id", lead["id"]).eq("action", "lead_unassigned").execute().data
        assert rows, "a stranded lead recorded no reason"
        assert rows[0]["detail"].get("reason") == "no_eligible_broker_in_group"

    def test_an_admin_can_still_be_handed_a_lead_deliberately(self, sandbox):
        """Excluded from the ROTATION, not from the system. Admin reassignment
        is a considered human action and stays allowed."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "Broker", daily_cap=5)
        admin = sandbox.broker(group["id"], "Admin", role="admin", daily_cap=10)

        lead = sandbox.lead(group["id"])
        assert sandbox.assign(lead["id"]) == broker["id"]

        assert sandbox.admin_assign(lead["id"], admin["id"], admin["id"]) is None
        assert self_holder(lead["id"]) == admin["id"]


def self_holder(lead_id):
    """Who currently holds this lead."""
    return supabase.table("lead").select("assigned_broker_id").eq(
        "id", lead_id).execute().data[0]["assigned_broker_id"]


# ── Eligibility, as the system path defines it ───────────────────────────
class TestSystemEligibility:
    @pytest.mark.parametrize(
        "cols",
        [
            {"is_active": False},
            {"is_blocked_by_admin": True},
        ],
        ids=["inactive", "blocked"],
    )
    def test_ineligible_brokers_never_receive_a_system_lead(self, sandbox, cols):
        group = sandbox.group()
        sandbox.broker(group["id"], "Unfit", **cols)
        lead = sandbox.lead(group["id"])
        assert sandbox.assign(lead["id"]) is None

    def test_an_archived_broker_is_unreachable_via_the_recycle_bin_rule(self, sandbox):
        """assign_lead does NOT test is_archived, and does not need to.

        Archiving is only permitted from the Recycle Bin
        (services/broker.py::archive_broker refuses otherwise), and the
        recycle-bin exclusion already keeps every broker there out of the
        rotation. So an archived broker in a live group is a state the
        application cannot produce.

        Written as its own test rather than a third parametrize case because
        an earlier draft DID assert that — by setting is_archived directly on
        a broker in a normal group, which fabricated a state no code path can
        reach, and then read the resulting pass/fail as a finding about
        assign_lead. It is a finding about the test.

        If archiving is ever allowed straight out of a working group, this
        test is what should start failing.
        """
        bin_group = sandbox.group(is_recycle_bin=True)
        sandbox.broker(bin_group["id"], "Archived", is_archived=True)
        lead = sandbox.lead(bin_group["id"])
        assert sandbox.assign(lead["id"]) is None

    def test_a_broker_starting_tomorrow_is_skipped_today(self, sandbox):
        """eligible_from is what keeps a broker onboarded this afternoon out
        of today's rotation."""
        group = sandbox.group()
        sandbox.broker(group["id"], "Tomorrow",
                       eligible_from="2099-01-01T00:00:00+00:00")
        lead = sandbox.lead(group["id"])
        assert sandbox.assign(lead["id"]) is None

    def test_a_broker_in_the_recycle_bin_never_receives_a_system_lead(self, sandbox):
        bin_group = sandbox.group(is_recycle_bin=True)
        sandbox.broker(bin_group["id"], "Binned")
        lead = sandbox.lead(bin_group["id"])
        assert sandbox.assign(lead["id"]) is None

    def test_a_zero_cap_broker_receives_nothing(self, sandbox):
        group = sandbox.group()
        sandbox.broker(group["id"], "Zero", daily_cap=0, max_cap=0)
        lead = sandbox.lead(group["id"])
        assert sandbox.assign(lead["id"]) is None


# ── The qualification gate ───────────────────────────────────────────────
class TestTheSignalFiresOnTheFirstQualifiedStage:
    """CompleteRegistration fires as soon as a lead is qualified, not at Quote
    Done (0116).

    WHY IT MOVED. Quote Done is days late — a quote is real work, and Meta's
    model waited that long to learn anything. When this was written 50 leads sat
    at In Progress against 19 at Quote Done, so the old firing point ignored most
    of the qualified book.

    A DENY-LIST, NOT AN ALLOW-LIST. Stages are free-form: a broker may go
    New -> Quote Done or New -> Sold, skipping In Progress entirely. Watching for
    'In Progress' alone would fire nothing for those leads AND stop Quote Done
    firing — a silent loss of every conversion from a broker who works that way.
    So every stage signals except New (the lead has only arrived; 0096 already
    fired `Lead` there) and Lost (including its 'Junk' label).

    ONCE, whichever qualifying stage comes first. Guaranteed by 0112's
    unique (lead_id, event_name, dataset_target) across every status.
    """

    def _lead(self, sandbox, group_id, broker_id, *, qualified=True, source="meta"):
        cols = {"combined_policy_type": "Auto Only"}
        if source == "meta":
            cols["platform_leadgen_id"] = f"{PREFIX}-{uuid.uuid4().hex[:14]}"
        lead = sandbox.lead(group_id, "lead", source=source, is_qualified=qualified, **cols)
        assert sandbox.assign(lead["id"]) == broker_id
        sandbox.applicant(lead["id"])
        return lead

    def _stage(self, lead_id, broker_id, stage):
        """The RPC directly, so this tests the TRIGGER rather than the service
        guards in front of it."""
        supabase.rpc("update_lead_stage", {
            "p_lead_id": lead_id, "p_broker_id": broker_id, "p_new_stage": stage,
        }).execute()

    def _registrations(self, lead_id):
        return supabase.table("meta_signal").select("id, status").eq(
            "lead_id", lead_id).eq("event_name", "CompleteRegistration").execute().data

    def test_it_fires_at_in_progress(self, sandbox):
        """THE POINT OF THE CHANGE. Days earlier than Quote Done."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._lead(sandbox, group["id"], broker["id"])

        self._stage(lead["id"], broker["id"], "In Progress")

        assert len(self._registrations(lead["id"])) == 1

    def test_moving_on_does_not_fire_a_second_time(self, sandbox):
        """THE DOUBLE-COUNT GUARD, and the reason 0112 came first.

        The id IS the event_id Meta deduplicates on, so a second row would be a
        second id — one customer reported as two conversions.
        """
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._lead(sandbox, group["id"], broker["id"])

        self._stage(lead["id"], broker["id"], "In Progress")
        first = self._registrations(lead["id"])
        self._stage(lead["id"], broker["id"], "Quote Done")
        self._stage(lead["id"], broker["id"], "Sold")

        after = self._registrations(lead["id"])
        assert len(after) == 1, f"fired more than once: {after}"
        assert after[0]["id"] == first[0]["id"], "and it is the same row"

    def test_a_broker_who_skips_in_progress_still_fires(self, sandbox):
        """THE CASE AN ALLOW-LIST WOULD HAVE LOST. New -> Quote Done directly."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._lead(sandbox, group["id"], broker["id"])

        self._stage(lead["id"], broker["id"], "Quote Done")

        assert len(self._registrations(lead["id"])) == 1

    def test_a_jump_straight_to_sold_still_fires(self, sandbox):
        """And New -> Sold, skipping both."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._lead(sandbox, group["id"], broker["id"])

        self._stage(lead["id"], broker["id"], "Sold")

        assert len(self._registrations(lead["id"])) == 1

    def test_lost_fires_nothing(self, sandbox):
        """Reporting a dead lead as a conversion is the opposite of the truth."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._lead(sandbox, group["id"], broker["id"])

        self._stage(lead["id"], broker["id"], "Lost")

        assert self._registrations(lead["id"]) == []

    def test_an_unqualified_lead_fires_nothing(self, sandbox):
        """THE GATE THE FOUR-YEAR RULE WRITES THROUGH. A customer insured too
        recently arrives here with is_qualified = false, set by the service
        before the stage was written — which is why the trigger needs no
        knowledge of that rule at all."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._lead(sandbox, group["id"], broker["id"], qualified=False)

        self._stage(lead["id"], broker["id"], "In Progress")

        assert self._registrations(lead["id"]) == []

    def test_an_unjudged_lead_fires_nothing(self, sandbox):
        """null is not false, and neither is a yes. A lead nobody has judged
        must not be reported."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._lead(sandbox, group["id"], broker["id"], qualified=None)

        self._stage(lead["id"], broker["id"], "In Progress")

        assert self._registrations(lead["id"]) == []

    def test_a_manual_lead_fires_nothing(self, sandbox):
        """No ad produced it, so there is no conversion to report."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._lead(sandbox, group["id"], broker["id"], source="manual")

        self._stage(lead["id"], broker["id"], "In Progress")

        assert self._registrations(lead["id"]) == []


class TestTheFourYearRule:
    """A customer insured for less than four years is not worth reporting.

    THE INSURANCE HISTORY DECIDES BEFORE THE BROKER DOES. This runs ahead of the
    qualification question, and when it settles the answer there is nothing left
    to ask — the dialog never opens.

    WHY IT IS A GUARD AND NOT A DIALOG QUESTION. Meta deduplicates on event_id,
    so a conversion once sent cannot be recalled. A rule enforced only on the
    screen would be bypassed by a stale client, and the resulting event would be
    permanent.

    THREE OUTCOMES:

        no date          -> the move is REFUSED. The answer is unknowable, and a
                            broker can fix it (parse a DASH report, or type the
                            date on New Policy).
        under four years -> is_qualified = false, and THE MOVE PROCEEDS. The
                            broker keeps the lead; only Meta is kept out of it.
        four years or more -> nothing happens here; the broker judges as normal.

    GAPS ARE IGNORED, by decision. insured_since is the start of the EARLIEST
    policy term, so someone first insured in 2015 who lapsed for two years still
    counts from 2015.
    """

    def _meta_lead(self, sandbox, group_id, broker_id, insured_since, **cols):
        cols.setdefault("combined_policy_type", "Auto Only")
        lead = sandbox.lead(
            group_id, "meta lead", source="meta",
            platform_leadgen_id=f"{PREFIX}-{uuid.uuid4().hex[:14]}",
            **cols,
        )
        assert sandbox.assign(lead["id"]) == broker_id
        # insured_since is the subject here, so it is passed explicitly rather
        # than taking the fixture's default. None means the column is left null.
        sandbox.applicant(lead["id"], insured_since=insured_since)
        return lead

    def _move(self, lead_id, broker_id, stage="In Progress"):
        """Through the SERVICE, not the RPC — the guard lives in the service and
        the RPC would sail straight past it."""
        from fastapi import HTTPException
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        try:
            lead_service.update_stage(
                lead_id, UpdateStageRequest(stage=stage), broker_id
            )
            return None
        except HTTPException as err:
            return err.detail

    def _row(self, lead_id):
        return supabase.table("lead").select("stage, is_qualified").eq(
            "id", lead_id).execute().data[0]

    # ── the boundary ────────────────────────────────────────────────────

    def test_exactly_four_years_qualifies(self, sandbox):
        """THE BOUNDARY, pinned as a decision rather than an accident of < vs <=.

        Four years to the day is long enough. The broker is still asked — this
        rule only removes the question when the answer is already no.
        """
        from services.lead import MIN_INSURED_YEARS

        today = date.today()
        cutoff = today.replace(year=today.year - MIN_INSURED_YEARS)

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"], cutoff.isoformat())

        error = self._move(lead["id"], broker["id"])

        # Refused for the JUDGEMENT, not for the date — which is the proof the
        # four-year rule let it through.
        assert error is not None
        assert "qualified" in str(error).lower()
        assert "continuous insurance" not in str(error).lower()
        assert self._row(lead["id"])["is_qualified"] is None

    def test_one_day_short_of_four_years_is_disqualified(self, sandbox):
        """The other side of the same boundary. Tested beside the case above so
        an off-by-one in either direction fails."""
        from services.lead import MIN_INSURED_YEARS

        today = date.today()
        cutoff = today.replace(year=today.year - MIN_INSURED_YEARS)
        one_day_short = cutoff + timedelta(days=1)

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(
            sandbox, group["id"], broker["id"], one_day_short.isoformat()
        )

        assert self._move(lead["id"], broker["id"]) is None, "the move must succeed"

        row = self._row(lead["id"])
        assert row["is_qualified"] is False, "recorded, not asked"
        assert row["stage"] == "In Progress", "the broker keeps the lead"

    def test_a_recent_customer_is_disqualified_but_still_moves(self, sandbox):
        """THE SHAPE OF THE RULE. Not qualified is a REPORTING outcome, not a
        pipeline one — the lead moves, the broker carries on, and only Meta is
        kept out of it."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        two_years_ago = date.today().replace(year=date.today().year - 2)
        lead = self._meta_lead(
            sandbox, group["id"], broker["id"], two_years_ago.isoformat()
        )

        assert self._move(lead["id"], broker["id"]) is None

        row = self._row(lead["id"])
        assert row["is_qualified"] is False
        assert row["stage"] == "In Progress"

    def test_a_long_insured_customer_is_still_asked(self, sandbox):
        """The rule must not qualify anyone on its own. Eight years of history
        says nothing about whether the person is reachable."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"], "2018-01-01")

        error = self._move(lead["id"], broker["id"])

        assert error is not None and "qualified" in str(error).lower()
        assert self._row(lead["id"])["is_qualified"] is None, "never auto-qualified"

    # ── the missing case ────────────────────────────────────────────────

    def test_no_date_refuses_the_move(self, sandbox):
        """UNKNOWN IS NOT A VERDICT. Without the date the rule cannot run, so the
        move is refused rather than guessed at in either direction.

        Six real leads were judged qualified with no date at all before this
        existed — the convention held 30 times out of 36.
        """
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"], None)

        error = self._move(lead["id"], broker["id"])

        assert error is not None
        assert "continuous insurance" in str(error).lower()
        row = self._row(lead["id"])
        assert row["stage"] == "New", "the lead did not move"
        assert row["is_qualified"] is None, "and nothing was decided for it"

    # ── what the rule must not touch ────────────────────────────────────

    def test_a_manual_lead_is_never_judged_by_it(self, sandbox):
        """A manual lead has no conversion to withhold, so the rule has nothing
        to protect and must not block the broker."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = sandbox.lead(group["id"], "walk-in", source="manual")
        assert sandbox.assign(lead["id"]) == broker["id"]
        sandbox.applicant(lead["id"], insured_since=None)

        assert self._move(lead["id"], broker["id"]) is None
        assert self._row(lead["id"])["stage"] == "In Progress"

    def test_a_direct_call_cannot_bypass_it(self, sandbox):
        """THE REASON THIS IS SERVER-SIDE. A client that has already written
        is_qualified = true must still not get a recent customer into In
        Progress as qualified — the guard overwrites the judgement."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        one_year_ago = date.today().replace(year=date.today().year - 1)
        lead = self._meta_lead(
            sandbox, group["id"], broker["id"], one_year_ago.isoformat()
        )
        supabase.table("lead").update({"is_qualified": True}).eq(
            "id", lead["id"]).execute()

        assert self._move(lead["id"], broker["id"]) is None

        assert self._row(lead["id"])["is_qualified"] is False, (
            "the rule outranks a judgement the client had already stored"
        )


class TestQualificationGate:
    """An UNQUALIFIED lead tells Meta nothing.

    CompleteRegistration IS the qualified-lead signal — that is what the event
    means to us, whatever Meta named it. Until 0078 it fired for EVERY Meta
    lead reaching Quote Done, with nobody ever asked whether the lead was
    real. Meta's optimisation was being trained on every form-filler who got a
    quote, junk included.

    Three states, and the difference between two of them is the whole design:

        true   broker said yes   -> signals fire
        false  broker said no    -> NOTHING is ever sent for this lead
        null   NOT ASKED YET     -> nothing sent, and the broker is BLOCKED
                                    from moving past Quote Done until they
                                    answer (services/lead.py::update_stage)

    'Skip' writes nothing at all and leaves null, which is why null must not
    default to false — the question has to survive being skipped.
    """

    def _meta_lead(self, sandbox, group_id, broker_id, **cols):
        # A policy type, so the PREMIUM gate is not what refuses these. Quote
        # Done began requiring a premium in 0100; tests that expect the move to
        # SUCCEED call self._premium() as well. Callers may override.
        cols.setdefault("combined_policy_type", "Auto Only")
        lead = sandbox.lead(
            group_id, "meta lead", source="meta",
            platform_leadgen_id=f"{PREFIX}-{uuid.uuid4().hex[:14]}",
            **cols,
        )
        assert sandbox.assign(lead["id"]) == broker_id
        # Complete, so the APPLICANT guard is not what refuses these — this
        # class is about qualification. The applicant guard runs first, so
        # without this the 422 would name the missing fields and never mention
        # qualification at all.
        sandbox.applicant(lead["id"])
        return lead

    def _stage(self, lead_id, broker_id, stage):
        """The DB function directly — no service guard, so this tests the
        TRIGGER in isolation from the 422."""
        try:
            supabase.rpc("update_lead_stage", {
                "p_lead_id": lead_id, "p_broker_id": broker_id,
                "p_new_stage": stage,
            }).execute()
            return None
        except Exception as err:
            return getattr(err, "message", None) or str(err)

    def _signals(self, lead_id):
        return sorted(
            r["event_name"] for r in supabase.table("meta_signal")
            .select("event_name").eq("lead_id", lead_id).execute().data
        )

    def _premium(self, lead_id):
        """A premium, so CompleteRegistration has a value to carry — and so
        the Quote Done premium guard is not what refuses these. Without it a
        missing signal could be blamed on the premium rather than on
        qualification, which is what this class is actually about.

        PLAUSIBLE, not a token 100. Since 0101 a proposed auto premium under
        $1,000 a year raises a 409 asking the broker to confirm it, so a
        placeholder figure would refuse these moves for a reason that has
        nothing to do with qualification — the exact confusion this helper
        exists to prevent."""
        supabase.table("premium_history").insert({
            "lead_id": lead_id, "new_auto_premium_annual": 1800,
        }).execute()

    # ── false: asked, and the answer was no ──────────────────────────────
    def test_unqualified_lead_sends_no_complete_registration(self, sandbox):
        """THE POINT OF THE FEATURE. An unqualified lead is exactly what we do
        NOT want Meta optimising towards."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"], is_qualified=False)

        assert self._stage(lead["id"], broker["id"], "Quote Done") is None
        assert self._signals(lead["id"]) == ["Lead"], (
            "only the delivery signal should exist — no CompleteRegistration"
        )

    def test_an_unqualified_lead_stays_silent_all_the_way_to_sold(self, sandbox):
        """The premium is present and the lead runs the full pipeline, so a
        missing signal can only be the qualification gate.

        REWRITTEN FOR 0100. This used to assert that no Purchase fired at
        Sold. Purchase no longer exists — Sold signals nothing for anyone —
        so the assertion that carries weight now is that the gate holds
        across every stage, not just the one that used to be valued."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"], is_qualified=False)
        self._premium(lead["id"])

        assert self._stage(lead["id"], broker["id"], "Quote Done") is None
        assert self._stage(lead["id"], broker["id"], "Sold") is None
        assert self._signals(lead["id"]) == ["Lead"], (
            "only the arrival signal should exist, at any stage"
        )

    # ── null: never asked ────────────────────────────────────────────────
    def test_an_unanswered_lead_sends_nothing(self, sandbox):
        """NULL is not false, and both must stay silent. A lead whose question
        was skipped has no judgement behind it — sending would be inventing
        one."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"])
        assert lead["is_qualified"] is None, "must arrive unanswered"

        assert self._stage(lead["id"], broker["id"], "Quote Done") is None
        assert self._signals(lead["id"]) == ["Lead"]

    # ── true: the guard rail ─────────────────────────────────────────────
    def test_a_qualified_lead_still_signals(self, sandbox):
        """GUARD RAIL. Proves the gate narrowed signalling rather than
        switching it off — the failure mode where every test above passes
        because nothing fires at all.

        Also pins the OTHER half of 0100: moving on to Sold adds nothing.
        Without that assertion this class would keep passing if Purchase were
        ever reinstated by accident."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"], is_qualified=True)
        self._premium(lead["id"])

        assert self._stage(lead["id"], broker["id"], "Quote Done") is None
        assert "CompleteRegistration" in self._signals(lead["id"])

        before = sorted(self._signals(lead["id"]))
        assert self._stage(lead["id"], broker["id"], "Sold") is None
        assert sorted(self._signals(lead["id"])) == before, (
            "Sold must add no signal of any kind"
        )

    # ── the service guard: answer before you can move on ─────────────────
    def test_moving_past_quote_done_is_refused_until_answered(self, sandbox):
        """The question BLOCKS the stage movement rather than being deferred —
        a deferred question is one nobody comes back to. Same shape as the
        premium guard it sits beside (422, broker-fixable)."""
        from fastapi import HTTPException
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"])
        # Priced, because the premium guard runs BEFORE this one (0100).
        # Without it the refusal would be about the premium and this test
        # would never reach the qualification gate it exists to check.
        self._premium(lead["id"])

        with pytest.raises(HTTPException) as err:
            lead_service.update_stage(
                lead["id"], UpdateStageRequest(stage="Quote Done"), broker["id"]
            )
        assert err.value.status_code == 422
        assert "qualif" in str(err.value.detail).lower(), err.value.detail

    def test_once_answered_the_move_goes_through(self, sandbox):
        """The other half — the block must lift, or the gate is a wall."""
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"], is_qualified=True)
        # Quote Done needs a premium since 0100, and this test is about the
        # qualification gate lifting — not about the premium one.
        self._premium(lead["id"])

        lead_service.update_stage(
            lead["id"], UpdateStageRequest(stage="Quote Done"), broker["id"]
        )
        row = supabase.table("lead").select("stage").eq("id", lead["id"]).execute().data
        assert row[0]["stage"] == "Quote Done"

    def test_qualification_is_accepted_while_the_applicant_is_incomplete(self, sandbox):
        """REVERSED, DELIBERATELY. This asserted the opposite until the
        judgement moved earlier in the funnel.

        The guard made sense while the question was asked on the way into Quote
        Done, where the full applicant is required anyway. It is wrong now: the
        judgement is asked on the way into IN PROGRESS, which exists so a
        qualified lead reaches Meta days sooner — before the form is finished.
        Keeping it meant the final Qualified tap failed for exactly the leads the
        change was built for.

        NOTHING IS UNGUARDED. update_stage still refuses Quote Done and beyond
        while the applicant is incomplete, which is the gate that governs what
        Meta is told; the test below proves it still bites.
        """
        from schemas.lead import SetQualificationRequest
        from services import lead as lead_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"])
        # _meta_lead builds a complete applicant; empty two required fields.
        supabase.table("driver").update(
            {"date_of_birth": None, "city": None}
        ).eq("lead_id", lead["id"]).eq("is_principal", True).execute()

        lead_service.set_qualification(
            lead["id"], SetQualificationRequest(is_qualified=True), broker["id"]
        )

        row = supabase.table("lead").select("is_qualified").eq(
            "id", lead["id"]).execute().data[0]
        assert row["is_qualified"] is True

    def test_the_stage_move_still_refuses_an_incomplete_applicant(self, sandbox):
        """THE GUARD THAT REPLACES THE ONE ABOVE, and the reason removing it was
        safe. A judgement may be recorded early; the lead still cannot reach
        Quote Done until the applicant is written down."""
        from fastapi import HTTPException
        from schemas.lead import SetQualificationRequest, UpdateStageRequest
        from services import lead as lead_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"])
        supabase.table("driver").update(
            {"date_of_birth": None, "city": None}
        ).eq("lead_id", lead["id"]).eq("is_principal", True).execute()

        lead_service.set_qualification(
            lead["id"], SetQualificationRequest(is_qualified=True), broker["id"]
        )

        with pytest.raises(HTTPException) as err:
            lead_service.update_stage(
                lead["id"], UpdateStageRequest(stage="Quote Done"), broker["id"]
            )
        assert err.value.status_code == 422
        assert "date of birth" in str(err.value.detail)

    def test_the_judgement_saves_without_touching_the_driver_facts(self, sandbox):
        """THE POINT OF SAVING EVERY TAP, now that the answers live in two
        places.

        The dialog's three questions do not share an endpoint: the judgement
        patches the LEAD, while convictions and the licence answer patch the
        DRIVER, because those describe the person. So a judgement written on its
        own must leave the driver's answers exactly as they were.

        This replaced a pair of tests that proved the same property using
        vehicle_count_declared, which the dialog no longer sends.
        """
        from schemas.lead import SetQualificationRequest
        from services import lead as lead_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"])
        supabase.table("driver").update(
            {"convictions_3y": 0, "licensed_5y_plus": True}
        ).eq("lead_id", lead["id"]).eq("is_principal", True).execute()

        lead_service.set_qualification(
            lead["id"], SetQualificationRequest(is_qualified=True), broker["id"]
        )

        assert supabase.table("lead").select("is_qualified").eq(
            "id", lead["id"]).execute().data[0]["is_qualified"] is True
        driver = supabase.table("driver").select(
            "convictions_3y, licensed_5y_plus"
        ).eq("lead_id", lead["id"]).eq("is_principal", True).execute().data[0]
        assert driver["convictions_3y"] == 0, "the earlier answer survived"
        assert driver["licensed_5y_plus"] is True

    def test_a_second_judgement_overwrites_the_first(self, sandbox):
        """A broker who changes their mind is answering the same question again,
        not adding a second answer. The last one stands."""
        from schemas.lead import SetQualificationRequest
        from services import lead as lead_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"])

        lead_service.set_qualification(
            lead["id"], SetQualificationRequest(is_qualified=True), broker["id"]
        )
        lead_service.set_qualification(
            lead["id"], SetQualificationRequest(is_qualified=False), broker["id"]
        )

        assert supabase.table("lead").select("is_qualified").eq(
            "id", lead["id"]).execute().data[0]["is_qualified"] is False

    def test_an_empty_request_and_an_explicit_null_are_both_refused(self):
        """Nothing sent is a mistake; null sent on purpose is a worse one.

        With is_qualified optional, `{"is_qualified": null}` would otherwise
        CLEAR a stored judgement — and a client sending it by accident would
        erase the answer silently. Caught at the schema, before any guard in the
        service runs, so no lead or broker is needed to prove it.
        """
        import pydantic

        from schemas.lead import SetQualificationRequest

        with pytest.raises(pydantic.ValidationError):
            SetQualificationRequest()

        with pytest.raises(pydantic.ValidationError):
            SetQualificationRequest(is_qualified=None)

    def test_a_false_judgement_is_stored_not_dropped(self, sandbox):
        """FALSY VALUES MUST SURVIVE THE PATCH, and `false` is the one that
        matters most here: it is the answer that keeps a lead out of Meta
        entirely.

        exclude_unset is what makes it work. A patch built with `if value:`
        would drop it, leaving is_qualified null — and null means NOT ASKED, so
        the dialog would reopen and the broker's rejection would be lost.

        This replaced the same assertion made through vehicle_count_declared,
        which the dialog no longer sends. The falsy-value risk did not go away
        with it — driver.convictions_3y carries it too, and 0 is the only value
        there that makes a lead Preferred.
        """
        from schemas.lead import SetQualificationRequest
        from services import lead as lead_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"])

        lead_service.set_qualification(
            lead["id"], SetQualificationRequest(is_qualified=False), broker["id"]
        )

        row = supabase.table("lead").select("is_qualified").eq(
            "id", lead["id"]).execute().data[0]
        assert row["is_qualified"] is False, "false is an answer, not an absence"

    def test_qualification_is_accepted_once_the_applicant_is_complete(self, sandbox):
        """GUARD RAIL: the order must not become a wall."""
        from schemas.lead import SetQualificationRequest
        from services import lead as lead_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"], broker["id"])

        lead_service.set_qualification(
            lead["id"], SetQualificationRequest(is_qualified=True), broker["id"]
        )
        row = supabase.table("lead").select("is_qualified").eq(
            "id", lead["id"]).execute().data[0]
        assert row["is_qualified"] is True

    def test_a_manual_lead_is_never_asked_and_never_signals(self, sandbox):
        """A walk-in referral has no platform_leadgen_id — Meta's strongest
        match signal — so there is nothing to attach a conversion to. Asking
        the broker would collect a judgement that goes nowhere, and blocking
        them on it would be worse."""
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = sandbox.lead(
            group["id"], "manual",  # source defaults to manual
            combined_policy_type="Auto Only",
        )
        assert sandbox.assign(lead["id"]) == broker["id"]
        # The APPLICANT and PREMIUM guards are not Meta-specific — a quote
        # needs these details and these figures whoever the lead came from —
        # so a manual lead still has to be complete and priced. It is the
        # QUALIFICATION question it is spared.
        sandbox.applicant(lead["id"])
        supabase.table("premium_history").insert({
            "lead_id": lead["id"], "new_auto_premium_annual": 1200,
        }).execute()

        lead_service.update_stage(
            lead["id"], UpdateStageRequest(stage="Quote Done"), broker["id"]
        )
        assert self._signals(lead["id"]) == [], "manual leads never signal"


# ── The applicant must be complete ───────────────────────────────────────
class TestApplicantRequiredBeforeQuoteDone:
    """A lead cannot be quoted until the applicant is filled in.

    Every field in the list is one Meta matches a conversion on
    (meta_capi.py::build_user_data hashes them into em, ph, db, ge, ct/st/zp).
    A lead quoted without them reports a sale Meta cannot attribute to the ad
    that produced it — which is the whole point of sending the event.

    THE APPLICANT ONLY. build_user_data reads the principal driver alone, so a
    spouse or additional driver contributes nothing and stays optional.
    """

    FULL = {
        "name": "Mitali Chawla",
        "first_name": "Mitali",
        "last_name": "Chawla",
        "phone": "4168274934",
        "email": "mitali@example.com",
        "date_of_birth": "1990-04-17",
        "gender": "Female",
        "street_address": "1 King St W",
        "city": "Toronto",
        "province": "Ontario",
        "postal_code": "M5H 1A1",
        # NOT one of _REQUIRED_APPLICANT_FIELDS, and present for the same reason
        # is_qualified and the premium row are set below: so that the applicant
        # guard is what refuses these leads and not something else.
        #
        # The four-year rule (services/lead.py::MIN_INSURED_YEARS) reads this and
        # runs BEFORE the applicant check, so without it every test here fails on
        # "Add the applicant's Continuous Insurance date" — a message that says
        # nothing about the fields this class exists to test.
        "insured_since": "2010-06-01",
    }

    def _lead_with_applicant(self, sandbox, **fields):
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = sandbox.lead(
            group["id"], "meta", source="meta",
            platform_leadgen_id=f"{PREFIX}-{uuid.uuid4().hex[:14]}",
            # Answered, so the qualification gate is not what refuses these.
            is_qualified=True,
            # Priced, so the PREMIUM gate is not what refuses these either.
            # Quote Done began requiring a premium in 0100; without this the
            # whole class would fail on "Choose the policy type", which says
            # nothing about the applicant fields it exists to test.
            combined_policy_type="Auto Only",
        )
        assert sandbox.assign(lead["id"]) == broker["id"]
        supabase.table("premium_history").insert({
            "lead_id": lead["id"], "new_auto_premium_annual": 1200,
        }).execute()
        supabase.table("driver").insert({
            "lead_id": lead["id"], "is_principal": True,
            "relationship_to_applicant": "Applicant", **fields,
        }).execute()
        return lead, broker

    def _move(self, lead_id, broker_id, stage="Quote Done"):
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service
        return lead_service.update_stage(
            lead_id, UpdateStageRequest(stage=stage), broker_id
        )

    def test_a_complete_applicant_moves_through(self, sandbox):
        """GUARD RAIL FIRST. If this fails the gate is a wall, and every
        assertion below would pass for the wrong reason."""
        lead, broker = self._lead_with_applicant(sandbox, **self.FULL)
        self._move(lead["id"], broker["id"])
        row = supabase.table("lead").select("stage").eq("id", lead["id"]).execute().data
        assert row[0]["stage"] == "Quote Done"

    @pytest.mark.parametrize(
        "column,label",
        [
            # BOTH NAME PARTS, SEPARATELY. Meta hashes fn and ln as different
            # parameters, so each one missing has to refuse on its own — a
            # lead with a first name and no surname can be matched on neither.
            ("first_name", "first name"),
            ("last_name", "last name"),
            ("phone", "phone"),
            ("email", "email"),
            ("date_of_birth", "date of birth"),
            ("gender", "gender"),
            ("street_address", "address"),
            # The three the guard missed until 2026-08-07, while the sender
            # was hashing all of them into ct/st/zp.
            ("city", "city"),
            ("postal_code", "postal code"),
            # PROVINCE IS NOT HERE. An Ontario-only brokerage has one possible
            # answer, so the broker is not asked — meta_capi.hash_province
            # defaults an empty value to "on" and the field still reaches Meta.
            # See test_province_is_not_required_of_the_broker below.
        ],
    )
    def test_each_missing_field_refuses_the_move(self, sandbox, column, label):
        """One case per field, so a gate that only checks five of the six
        cannot hide behind the other five."""
        from fastapi import HTTPException

        fields = {k: v for k, v in self.FULL.items() if k != column}
        lead, broker = self._lead_with_applicant(sandbox, **fields)

        with pytest.raises(HTTPException) as err:
            self._move(lead["id"], broker["id"])
        assert err.value.status_code == 422
        assert label in str(err.value.detail), err.value.detail

    def test_province_is_not_required_of_the_broker(self, sandbox):
        """ONTARIO ONLY, so there is one possible answer and the broker is not
        asked for it. The field still reaches Meta — meta_capi.hash_province
        defaults an empty value to "on", the same way country is hardcoded to
        "ca" — so this costs no match quality.

        Found by a browser test on 2026-08-07: the address lookup fills the
        column with "ON", which the hasher did not recognise, so `st` was
        silently dropped from every real event."""
        import meta_capi

        fields = {k: v for k, v in self.FULL.items() if k != "province"}
        lead, broker = self._lead_with_applicant(sandbox, **fields)

        self._move(lead["id"], broker["id"])
        row = supabase.table("lead").select("stage").eq(
            "id", lead["id"]).execute().data
        assert row[0]["stage"] == "Quote Done", "a blank province must not block"

        # ...and Meta still receives it.
        assert meta_capi.hash_province(None) == meta_capi.hash_province("ON")
        assert meta_capi.hash_province("ON") == meta_capi.hash_province("Ontario")

    def test_the_message_lists_everything_missing_at_once(self, sandbox):
        """Not one field at a time: the broker fixes the form in one pass
        rather than being sent back to the same dialog six times."""
        from fastapi import HTTPException

        lead, broker = self._lead_with_applicant(sandbox, name="Mitali Chawla")

        with pytest.raises(HTTPException) as err:
            self._move(lead["id"], broker["id"])
        detail = str(err.value.detail)
        for label in ["phone", "email", "date of birth", "gender", "address"]:
            assert label in detail, f"{label} missing from: {detail}"

    def test_not_provided_counts_as_empty(self, sandbox):
        """A manual lead carries '(not provided)' in whichever of phone/email
        the broker did not have, and it is copied onto the applicant when the
        row is seeded. A plain truthiness check would read that placeholder as
        a real answer and let the lead through with nothing to match on."""
        from fastapi import HTTPException

        lead, broker = self._lead_with_applicant(
            sandbox, **{**self.FULL, "email": "(not provided)"}
        )

        with pytest.raises(HTTPException) as err:
            self._move(lead["id"], broker["id"])
        assert "email" in str(err.value.detail)

    def test_lost_is_always_reachable(self, sandbox):
        """A dead lead must stay closable. Refusing to let a broker file one
        because the applicant is incomplete would strand it forever."""
        lead, broker = self._lead_with_applicant(sandbox, name="Mitali Chawla")
        self._move(lead["id"], broker["id"], stage="Lost")
        row = supabase.table("lead").select("stage").eq("id", lead["id"]).execute().data
        assert row[0]["stage"] == "Lost"

    def test_an_additional_driver_is_not_required(self, sandbox):
        """Only the applicant is sent to Meta, so a blank spouse row must not
        block the lead."""
        lead, broker = self._lead_with_applicant(sandbox, **self.FULL)
        supabase.table("driver").insert({
            "lead_id": lead["id"], "is_principal": False,
            "relationship_to_applicant": "Spouse",
        }).execute()

        self._move(lead["id"], broker["id"])
        row = supabase.table("lead").select("stage").eq("id", lead["id"]).execute().data
        assert row[0]["stage"] == "Quote Done"


# ── The Lead event covers every Meta lead ────────────────────────────────
class TestLeadSignalOnArrival:
    """Meta's 'Lead' event means "a Meta lead was downloaded into the CRM",
    not "a colleague picked it up" and not "the broker captured the details".

    0095 briefly held it until the applicant was complete. Meta advised
    against it: sending only positive signals leaves their model with no
    baseline, so it cannot tell which ads bring reachable people. 0096 put it
    back. test_meta_signal_trigger.py owns the firing rules; this class holds
    the ASSIGNMENT-shaped cases, because assignment is what this file is about.
    """

    def _signals(self, lead_id):
        return sorted(
            r["event_name"] for r in supabase.table("meta_signal")
            .select("event_name").eq("lead_id", lead_id).execute().data
        )

    def _meta_lead(self, sandbox, group_id, **cols):
        return sandbox.lead(
            group_id, "meta", source="meta",
            platform_leadgen_id=f"{PREFIX}-{uuid.uuid4().hex[:14]}",
            **cols,
        )

    def test_an_unassigned_meta_lead_already_reports(self, sandbox):
        """No broker, no assignment, no delivery row — and Meta is told,
        because the lead exists."""
        group = sandbox.group()
        lead = self._meta_lead(sandbox, group["id"])
        assert self._signals(lead["id"]) == ["Lead"]

    def test_an_admin_assigned_lead_reports(self, sandbox):
        """THE GAP THAT PROMPTED 0079. admin_assign_lead writes no
        lead_delivery row — a reassignment must not consume anyone's daily cap
        — so under the delivery trigger this lead told Meta nothing, ever.
        Firing at insert makes the assignment path irrelevant."""
        group = sandbox.group()
        admin = sandbox.broker(group["id"], "Admin", daily_cap=0, max_cap=0, role="admin")
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"])

        assert sandbox.admin_assign(lead["id"], broker["id"], admin["id"]) is None
        assert "Lead" in self._signals(lead["id"])

    def test_a_manual_lead_still_reports_nothing(self, sandbox):
        """GUARD RAIL. source is read straight off the inserted row, so the
        non-Meta gate has to hold there."""
        group = sandbox.group()
        lead = sandbox.lead(group["id"], "manual")  # source defaults to manual
        assert self._signals(lead["id"]) == []

    def test_assignment_does_not_add_a_second_lead_event(self, sandbox):
        """GUARD RAIL. The old delivery trigger is dropped; if it survived,
        assigning would try to insert the same (lead_id, 'Lead') pair again."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = self._meta_lead(sandbox, group["id"])
        assert sandbox.assign(lead["id"]) == broker["id"]

        rows = supabase.table("meta_signal").select("id").eq(
            "lead_id", lead["id"]).eq("event_name", "Lead").execute().data
        assert len(rows) == 1, f"expected exactly one Lead signal, got {len(rows)}"

    def test_completing_the_applicant_adds_no_second_lead_event(self, sandbox):
        """GUARD RAIL for 0096 specifically: 0095's trigger fired on every
        applicant update, and any remnant of it would double-report here."""
        group = sandbox.group()
        lead = self._meta_lead(sandbox, group["id"])
        sandbox.applicant(lead["id"])

        rows = supabase.table("meta_signal").select("id").eq(
            "lead_id", lead["id"]).eq("event_name", "Lead").execute().data
        assert len(rows) == 1

    def test_the_lead_event_is_not_gated_on_qualification(self, sandbox):
        """Deliberate: at creation nobody has looked at the lead, so there is
        no judgement to gate on. Gating here would mean never telling Meta a
        lead arrived."""
        group = sandbox.group()
        lead = self._meta_lead(sandbox, group["id"], is_qualified=False)
        assert self._signals(lead["id"]) == ["Lead"]


# ── Retrying a failed signal ─────────────────────────────────────────────
class TestFailedSignalsCanBeRetried:
    """A Failed signal had no way back into the queue.

    list_pending reads only status = 'Pending', so nothing ever picked a
    Failed row up again. That mattered because EVERY HTTP 400 from Meta is
    terminal — including error code 190, an expired access token, where
    nothing is wrong with the event at all. A token expiring overnight failed
    every signal queued in that window and lost those conversions silently.

    Retry puts the row back. THE ID IS UNCHANGED, which is what makes it safe:
    send_event passes meta_signal.id as Meta's event_id and Meta deduplicates
    on it, so even retrying something Meta did receive cannot double-count.
    """

    def _signal(self, sandbox, status="Failed", **cols):
        """A real signal row, created the way the trigger creates them: by
        inserting a Meta lead.

        These tests are about RETRY, not about when a Lead fires —
        test_meta_signal_trigger.py owns that question.
        """
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = sandbox.lead(
            group["id"], "meta", source="meta",
            platform_leadgen_id=f"{PREFIX}-{uuid.uuid4().hex[:14]}",
        )
        assert sandbox.assign(lead["id"]) == broker["id"]
        rows = supabase.table("meta_signal").select("id").eq(
            "lead_id", lead["id"]).execute().data
        assert rows, "a Meta lead should have queued a Lead signal on insert"
        sig_id = rows[0]["id"]
        supabase.table("meta_signal").update(
            {"status": status, **cols}
        ).eq("id", sig_id).execute()
        return sig_id, broker

    def _row(self, sig_id):
        return supabase.table("meta_signal").select("*").eq(
            "id", sig_id).execute().data[0]

    def test_a_failed_signal_goes_back_to_pending(self, sandbox):
        """THE POINT. Fix the token, hit Retry, and the drain sends it."""
        from services import meta_signal as service

        sig_id, broker = self._signal(
            sandbox, error_message="Session has expired", error_code=190,
        )
        service.retry(sig_id, broker["id"])

        row = self._row(sig_id)
        assert row["status"] == "Pending"
        assert row["error_message"] is None, "the old error describes a past attempt"
        assert row["error_code"] is None

    def test_the_id_is_unchanged_so_meta_still_deduplicates(self, sandbox):
        """The whole safety argument. meta_signal.id IS the event_id Meta
        deduplicates on — a new id would turn a retry into a second
        conversion."""
        from services import meta_signal as service

        sig_id, broker = self._signal(sandbox)
        service.retry(sig_id, broker["id"])
        assert self._row(sig_id)["id"] == sig_id

    def test_the_attempt_history_is_kept(self, sandbox):
        """attempt_count is not reset: a row that has failed three times
        should still look like one after a retry."""
        from services import meta_signal as service

        sig_id, broker = self._signal(sandbox, attempt_count=3)
        service.retry(sig_id, broker["id"])
        assert self._row(sig_id)["attempt_count"] == 3

    def test_a_sent_signal_is_refused(self, sandbox):
        """Re-sending a conversion Meta already counted is a DIFFERENT request
        from retrying a failure, and this button is not it."""
        from fastapi import HTTPException
        from services import meta_signal as service

        sig_id, broker = self._signal(sandbox, status="Sent")
        with pytest.raises(HTTPException) as err:
            service.retry(sig_id, broker["id"])
        assert err.value.status_code == 409
        assert self._row(sig_id)["status"] == "Sent", "must not have been touched"

    def test_a_pending_signal_is_refused(self, sandbox):
        """Already queued — retrying means nothing, and saying so is better
        than a no-op that looks like it worked."""
        from fastapi import HTTPException
        from services import meta_signal as service

        sig_id, broker = self._signal(sandbox, status="Pending")
        with pytest.raises(HTTPException) as err:
            service.retry(sig_id, broker["id"])
        assert err.value.status_code == 409

    def test_a_missing_signal_is_a_404(self, sandbox):
        from fastapi import HTTPException
        from services import meta_signal as service

        with pytest.raises(HTTPException) as err:
            service.retry(str(uuid.uuid4()), str(uuid.uuid4()))
        assert err.value.status_code == 404

    def test_the_retry_is_audited(self, sandbox):
        """An outbound event to an ad platform is worth a record of who caused
        it."""
        from services import meta_signal as service

        sig_id, broker = self._signal(sandbox)
        service.retry(sig_id, broker["id"])

        rows = supabase.table("audit_log").select("action, actor_broker_id").eq(
            "entity_type", "meta_signal").eq("entity_id", sig_id).execute().data
        assert rows and rows[0]["action"] == "retry_meta_signal"
        assert rows[0]["actor_broker_id"] == broker["id"]
        supabase.table("audit_log").delete().eq("entity_id", sig_id).execute()

    def test_the_screen_shows_the_error_code(self, sandbox):
        """list_recent is what the dashboard renders — the code has to reach
        it, or an admin cannot tell an expired token from a bad payload."""
        from services import meta_signal as service

        sig_id, _ = self._signal(
            sandbox, error_message="Error validating access token", error_code=190,
        )
        row = next(r for r in service.list_recent() if r["id"] == sig_id)
        assert row["error_code"] == 190
        assert row["lead"]["name"], "the screen needs to say WHOSE conversion failed"


class TestABrokerCanOptThemselvesIn:
    """The opt-in reaches the broker it was built for.

    Everything below the surface already worked — the service, the daily
    reset, and the backlog sweep that hands over leads already waiting. But
    PATCH /brokers/{id}/ready-for-more sits on the require_admin router, so a
    broker calling it for themselves got a 403, and the frontend helper
    setBrokerReadyForMore had ZERO callers. The feature existed and nobody
    could use it.

    PATCH /my-ready-for-more is its own route rather than a relaxed guard on
    the admin one. That route NAMES a broker, which is exactly the capability
    a broker must not have: an admin opting someone else in is a different act
    from a broker opting themselves in.
    """

    def _route(self):
        from routes import broker as broker_routes

        return next(
            r for r in broker_routes.broker_router.routes
            if getattr(r, "path", "") == "/my-ready-for-more"
        )

    def test_the_route_is_on_the_broker_router_not_the_admin_one(self):
        """If it were on the admin router, every broker would get a 403 — the
        exact failure this exists to remove."""
        from routes import broker as broker_routes

        admin_paths = [getattr(r, "path", "") for r in broker_routes.router.routes]
        assert "/my-ready-for-more" not in admin_paths
        assert self._route() is not None

    def test_the_route_takes_no_broker_id(self):
        """THE GUARANTEE. The id comes from the token, and there is no
        parameter to carry someone else's — a broker cannot opt a colleague in
        (or out) however the request is shaped."""
        import inspect

        params = inspect.signature(self._route().endpoint).parameters
        assert "broker_id" not in params
        assert set(params) == {"request", "broker"}

    def test_the_request_body_carries_only_the_flag(self):
        """A second field here would be a second thing a broker could set
        about themselves without an admin."""
        from schemas.broker import SetReadyForMoreRequest

        assert set(SetReadyForMoreRequest.model_fields) == {"ready_for_more"}

    def test_a_broker_turning_it_on_gets_a_lead_already_waiting(self, sandbox):
        """THE WHOLE FEATURE, in one action, through the route's own service
        call. The lead is sitting unassigned because the broker is at their
        daily cap; saying "I'll take more" should hand it over."""
        from schemas.broker import SetReadyForMoreRequest
        from services import broker as broker_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "Solo", daily_cap=1, max_cap=5,
                                ready_for_more=False)

        first = sandbox.lead(group["id"], "L1")
        assert sandbox.assign(first["id"]) == broker["id"]

        # Over cap, so this one has nowhere to go.
        waiting = sandbox.lead(group["id"], "L2")
        assert sandbox.assign(waiting["id"]) is None

        broker_service.set_ready_for_more(
            broker["id"], SetReadyForMoreRequest(ready_for_more=True)
        )

        holder = supabase.table("lead").select("assigned_broker_id").eq(
            "id", waiting["id"]).execute().data[0]["assigned_broker_id"]
        assert holder == broker["id"], "the waiting lead should have been handed over"

    def test_the_flag_is_visible_on_their_own_profile(self, sandbox):
        """The header reads /my-profile for the avatar already, so the toggle
        needs no extra request — but only if the flag is actually in that
        deny-by-default response."""
        from services import broker as broker_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "P", daily_cap=5, max_cap=5,
                                ready_for_more=True)
        assert broker_service.get_my_profile(broker["id"])["ready_for_more"] is True

    def test_an_archived_broker_is_still_refused(self, sandbox):
        """The existing guard has to survive the new entry point — otherwise
        this route is a way around it."""
        from fastapi import HTTPException
        from schemas.broker import SetReadyForMoreRequest
        from services import broker as broker_service

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "Gone", daily_cap=5, max_cap=5)
        supabase.table("broker").update({"is_archived": True}).eq(
            "id", broker["id"]).execute()

        with pytest.raises(HTTPException) as err:
            broker_service.set_ready_for_more(
                broker["id"], SetReadyForMoreRequest(ready_for_more=True)
            )
        assert err.value.status_code in (400, 404, 409)


# ── DASH prefill ─────────────────────────────────────────────────────────
class TestDashPrefill:
    """A DASH report fills what is blank and never touches what the broker
    typed.

    THAT RULE IS THE WHOLE FEATURE. Without it this is not an option beside
    typing, it is a thing that silently undoes a broker's own correction — and
    a broker who fixed a licence number on the phone would have no way to know
    it had been reverted.
    """

    def _driver(self, sandbox, **cols):
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)
        lead = sandbox.lead(group["id"], "dash")
        assert sandbox.assign(lead["id"]) == broker["id"]
        row = supabase.table("driver").insert({
            "lead_id": lead["id"], "is_principal": True,
            "relationship_to_applicant": "Applicant", **cols,
        }).execute().data[0]
        return row, broker

    def _markdown(self):
        from pathlib import Path
        return (Path(__file__).parent / "fixtures" / "dash_sample.md").read_text(encoding="utf-8")

    def test_blank_fields_are_filled(self, sandbox):
        """Otherwise the feature does nothing at all."""
        from services import dash_apply

        driver, _ = self._driver(sandbox, name="Typed Name")
        dash_apply.apply_report(driver["id"], self._markdown())

        row = supabase.table("driver").select("*").eq("id", driver["id"]).execute().data[0]
        assert row["license_number"] == "S1234-56789-01234"
        assert row["gender"] == "Male"
        assert row["city"] == "SAMPLETOWN"
        assert row["postal_code"] == "M1A1A1"

    def test_a_field_the_broker_typed_is_never_overwritten(self, sandbox):
        """THE SAFETY RULE. The report says S1234-56789-01234; the broker
        typed something else, on the phone, with the customer. Theirs wins."""
        from services import dash_apply

        driver, _ = self._driver(
            sandbox, name="Typed Name", license_number="BROKER-TYPED-THIS",
            gender="Female",
        )
        result = dash_apply.apply_report(driver["id"], self._markdown())

        row = supabase.table("driver").select("*").eq("id", driver["id"]).execute().data[0]
        assert row["license_number"] == "BROKER-TYPED-THIS"
        assert row["gender"] == "Female"
        assert "license_number" in result["driver_skipped"]
        assert "gender" in result["driver_skipped"]

    def test_it_reports_what_it_filled_and_what_it_skipped(self, sandbox):
        """So the broker can be told "6 filled, 2 already had values" rather
        than comparing the form against the PDF themselves."""
        from services import dash_apply

        driver, _ = self._driver(sandbox, gender="Female")
        result = dash_apply.apply_report(driver["id"], self._markdown())

        assert "gender" in result["driver_skipped"]
        assert "license_number" in result["driver_filled"]

    def test_both_cars_are_added(self, sandbox):
        """The current policy has two. A parser bug returned one and looked
        healthy — see tests/test_dash_parse.py."""
        from services import dash_apply

        driver, _ = self._driver(sandbox)
        result = dash_apply.apply_report(driver["id"], self._markdown())
        assert result["vehicles_added"] == 2

        vins = {v["vin"] for v in supabase.table("vehicle").select("vin").eq(
            "driver_id", driver["id"]).execute().data}
        assert vins == {"1AAAA1A1AAA000001", "2BBBB2B2BBB000002"}

    def test_a_car_the_broker_already_entered_is_not_duplicated(self, sandbox):
        """Matched by VIN — the rule the deleted services/vehicle.py had
        already verified against the live database."""
        from services import dash_apply

        driver, _ = self._driver(sandbox)
        supabase.table("vehicle").insert({
            "driver_id": driver["id"], "vin": "1AAAA1A1AAA000001",
            "year_make_model": "BROKER TYPED THIS",
        }).execute()

        dash_apply.apply_report(driver["id"], self._markdown())

        rows = supabase.table("vehicle").select("vin, year_make_model").eq(
            "driver_id", driver["id"]).execute().data
        assert len(rows) == 2, "the known VIN should not have been added twice"
        typed = [r for r in rows if r["vin"] == "1AAAA1A1AAA000001"][0]
        assert typed["year_make_model"] == "BROKER TYPED THIS", "left exactly as typed"

    def test_a_driver_on_someone_elses_lead_is_refused(self, sandbox):
        """The guard runs BEFORE Azure is called — reading another broker's
        customer and then refusing to save would still have sent that report
        to a third party."""
        from fastapi import HTTPException
        from services import dash as dash_service

        driver, _ = self._driver(sandbox)
        other = sandbox.broker(sandbox.group()["id"], "Other", daily_cap=5, max_cap=5)

        with pytest.raises(HTTPException) as err:
            dash_service.apply_pdf(driver["id"], other["id"], b"%PDF-fake")
        assert err.value.status_code == 403


# ── The policy type is a rule, not a moment ──────────────────────────────
class TestPolicyRulesAreEnforcedAfterTheSwitch:
    """set_policy_type deletes the vehicles a property-only policy cannot have
    and clears the premiums it cannot hold. Nothing used to stop either coming
    straight back — the switch was a moment, and the rule was supposed to be
    permanent.

    Each test below describes a door a broker can walk through in ordinary
    use, not a contrived request.
    """

    def _lead_with_applicant(self, sandbox, policy_type):
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "PB", daily_cap=5, max_cap=5)
        lead = sandbox.lead(group["id"], combined_policy_type=policy_type)
        sandbox.assign(lead["id"])
        return lead, broker, sandbox.applicant(lead["id"])

    def test_a_property_only_policy_refuses_a_vehicle(self, sandbox):
        """THE DELETION UNDONE IN TWO CLICKS.

        Pick Homeowner Only, accept a warning that deletes the cars, then add
        one back — the policy has no auto side for it to belong to.
        """
        from fastapi import HTTPException
        from schemas.vehicle import VehiclePayload
        from services import vehicle as vehicle_service

        _, _, driver = self._lead_with_applicant(sandbox, "Homeowner Only")

        with pytest.raises(HTTPException) as err:
            vehicle_service.add_vehicle(driver["id"], VehiclePayload())
        assert err.value.status_code == 422
        # Names the policy type, so the broker knows which answer to change
        # rather than being told a vehicle is simply not allowed.
        assert "homeowner only" in err.value.detail.lower()

    def test_an_auto_policy_still_accepts_vehicles(self, sandbox):
        """The guard must not be the thing that breaks ordinary work."""
        from schemas.vehicle import VehiclePayload
        from services import vehicle as vehicle_service

        _, _, driver = self._lead_with_applicant(sandbox, "Auto and Homeowner")
        created = vehicle_service.add_vehicle(driver["id"], VehiclePayload())
        assert created["driver_id"] == driver["id"]

    def test_a_premium_outside_the_policy_is_refused(self, sandbox):
        """THE FIGURE THAT COMES BACK INVISIBLY.

        These fields save on BLUR, so a save already in flight when the policy
        changed lands afterwards. Accepted, it keeps feeding
        total_new_premium_annual — a generated column — which is what
        commission multiplies and what Meta receives as its event value. The
        screen cannot show it, because home is hidden.
        """
        from fastapi import HTTPException
        from schemas.premium_history import SavePremiumRequest
        from services import premium_history as premium_service

        lead, _, _ = self._lead_with_applicant(sandbox, "Auto Only")

        with pytest.raises(HTTPException) as err:
            premium_service.save_for_lead(
                lead["id"],
                SavePremiumRequest(new_homeowner_premium_annual="100"),
            )
        assert err.value.status_code == 422
        assert "home" in err.value.detail.lower()

    def test_a_premium_inside_the_policy_still_saves(self, sandbox):
        lead, _, _ = self._lead_with_applicant(sandbox, "Auto Only")
        from schemas.premium_history import SavePremiumRequest
        from services import premium_history as premium_service

        saved = premium_service.save_for_lead(
            lead["id"], SavePremiumRequest(new_auto_premium_annual="200")
        )
        assert saved["new_auto_premium_annual"] is not None

    def test_issued_needs_every_product_priced(self, sandbox):
        """Auto and Homeowner with only the auto figure reached Issued, and
        commission silently skipped the home half."""
        import uuid as _uuid

        from fastapi import HTTPException
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        lead, broker, _ = self._lead_with_applicant(sandbox, "Auto and Homeowner")
        supabase.table("premium_history").insert({
            "lead_id": lead["id"],
            "new_auto_premium_annual": "200",
        }).execute()

        # THE DATE IS SUPPLIED so the premium is what refuses this. The
        # premium check moved below the issued-date check in 0100 (it had to
        # go below the applicant check, and the date check sits between them),
        # so without a date this would fail on "Enter the date this policy was
        # issued" and prove nothing about premiums.
        import datetime as _dt

        with pytest.raises(HTTPException) as err:
            lead_service.update_stage(
                _uuid.UUID(lead["id"]),
                UpdateStageRequest(
                    stage="Issued", issued_at=_dt.date.today()
                ),
                _uuid.UUID(broker["id"]),
            )
        assert err.value.status_code == 422
        assert "home" in err.value.detail.lower()

    def test_sold_does_not_need_the_carrier(self, sandbox):
        """A broker marking Sold on the phone before the carrier is confirmed
        is ordinary work, so the INSURER check waits for Issued.

        The PREMIUMS do not wait. They are required from Quote Done onward
        (0100), so by the time a lead reaches Sold the figures are already in
        — this test pins that the INSURER is still not asked for. An earlier
        version asserted that Sold accepted a partial premium, which was the
        decision this suite later had to reverse.
        """
        import uuid as _uuid

        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        lead, broker, _ = self._lead_with_applicant(sandbox, "Auto and Homeowner")
        supabase.table("premium_history").insert({
            "lead_id": lead["id"],
            "new_auto_premium_annual": "200",
            "new_homeowner_premium_annual": "100",
        }).execute()

        lead_service.update_stage(
            _uuid.UUID(lead["id"]),
            UpdateStageRequest(stage="Sold"),
            _uuid.UUID(broker["id"]),
        )


class TestTheGuardsHoldOnEveryDoor:
    """A rule enforced on one write path and not another is not a rule.

    daaf1c6 guarded add_vehicle and said the policy type was now permanent.
    DASH writes vehicles through the repository directly, so it walked round
    the guard — and Sold still let a partial premium reach Meta, which is the
    one mistake nothing can take back.
    """

    def _lead(self, sandbox, policy_type):
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "GB", daily_cap=5, max_cap=5)
        lead = sandbox.lead(group["id"], combined_policy_type=policy_type)
        sandbox.assign(lead["id"])
        return lead, broker, sandbox.applicant(lead["id"])

    def _report(self):
        from pathlib import Path

        return Path(__file__).parent.joinpath(
            "fixtures", "dash_sample.md"
        ).read_text(encoding="utf-8")

    def test_dash_adds_no_vehicles_to_a_property_only_policy(self, sandbox):
        """THE DOOR LEFT OPEN. add_vehicle refuses a car here; dash_apply wrote
        straight to the repository, so the cars landed in the database while
        the UI hid them — invisible rows on a policy that insures none."""
        from services import dash_apply

        lead, _, driver = self._lead(sandbox, "Homeowner Only")
        result = dash_apply.apply_report(driver["id"], self._report())

        assert result["vehicles_added"] == 0
        rows = supabase.table("vehicle").select("id").eq(
            "driver_id", driver["id"]).execute().data
        assert rows == [], "hidden vehicles were written to a property-only policy"

    def test_the_driver_is_still_filled_on_a_property_only_policy(self, sandbox):
        """SKIPPED, NOT REFUSED. The applicant's own fields are what the broker
        is waiting on; losing the whole upload over the cars would trade the
        important half for the irrelevant one."""
        from services import dash_apply

        _, _, driver = self._lead(sandbox, "Homeowner Only")
        result = dash_apply.apply_report(driver["id"], self._report())

        # licence number, not gender: the sandbox's applicant already HAS a
        # gender, so DASH correctly skips it — the never-overwrite rule doing
        # its job would otherwise look like this guard breaking things.
        assert "license_number" in result["driver_filled"]
        assert result["subject_name"] == "SAMPLE,TESTCASE A"

    def test_dash_still_adds_vehicles_to_an_auto_policy(self, sandbox):
        """The guard must not be what breaks the ordinary case."""
        from services import dash_apply

        _, _, driver = self._lead(sandbox, "Auto and Homeowner")
        assert dash_apply.apply_report(driver["id"], self._report())["vehicles_added"] == 2

    def test_quote_done_needs_every_product_priced(self, sandbox):
        """THE VALUE META CANNOT UNLEARN, and since 0100 this is where it is
        decided.

        Quote Done fires CompleteRegistration, which snapshots
        total_new_premium_annual at that moment and is deduplicated on
        event_id. An Auto and Homeowner lead quoted on its auto figure alone
        teaches Meta a conversion worth half the policy, permanently —
        entering the home premium afterwards sends nothing.

        REFUSED BEFORE THE WRITE. The check runs ahead of the stage update and
        therefore ahead of the trigger, so this lead ends with NO signal row
        at all rather than one carrying half a policy. That ordering is the
        whole reason a valueless CompleteRegistration is impossible.
        """
        import uuid as _uuid

        from fastapi import HTTPException
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        lead, broker, _ = self._lead(sandbox, "Auto and Homeowner")
        supabase.table("premium_history").insert({
            "lead_id": lead["id"], "new_auto_premium_annual": "200",
        }).execute()

        with pytest.raises(HTTPException) as err:
            lead_service.update_stage(
                _uuid.UUID(lead["id"]),
                UpdateStageRequest(stage="Quote Done"),
                _uuid.UUID(broker["id"]),
            )
        assert err.value.status_code == 422
        assert "home" in err.value.detail.lower()

        signals = supabase.table("meta_signal").select("event_name").eq(
            "lead_id", lead["id"]
        ).execute().data
        assert "CompleteRegistration" not in [r["event_name"] for r in signals], (
            "a refused move must leave no signal behind"
        )

    def test_a_zero_premium_is_refused_like_a_blank_one(self, sandbox):
        """value: 0 is a CLAIM, not an absence — it tells Meta the conversion
        was worth nothing. No special case is needed for it: `not row.get(...)`
        is falsy for 0, so this pins behaviour that already falls out of the
        guard rather than behaviour someone has to remember."""
        import uuid as _uuid

        from fastapi import HTTPException
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        lead, broker, _ = self._lead(sandbox, "Auto Only")
        supabase.table("premium_history").insert({
            "lead_id": lead["id"], "new_auto_premium_annual": "0",
        }).execute()

        with pytest.raises(HTTPException) as err:
            lead_service.update_stage(
                _uuid.UUID(lead["id"]),
                UpdateStageRequest(stage="Quote Done"),
                _uuid.UUID(broker["id"]),
            )
        assert err.value.status_code == 422
        assert "auto" in err.value.detail.lower()

    def test_sold_needs_every_product_priced(self, sandbox):
        """The guard still holds at Sold.

        Since 0100 it will normally have fired earlier, at Quote Done — see
        test_quote_done_needs_every_product_priced, which is where the value
        is actually decided. This stays because stages are free-form: a lead
        can reach Sold without passing through Quote Done, and that is the
        back door this closes.
        """
        import uuid as _uuid

        from fastapi import HTTPException
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        lead, broker, _ = self._lead(sandbox, "Auto and Homeowner")
        supabase.table("premium_history").insert({
            "lead_id": lead["id"], "new_auto_premium_annual": "200",
        }).execute()

        with pytest.raises(HTTPException) as err:
            lead_service.update_stage(
                _uuid.UUID(lead["id"]),
                UpdateStageRequest(stage="Sold"),
                _uuid.UUID(broker["id"]),
            )
        assert err.value.status_code == 422
        assert "home" in err.value.detail.lower()

    def test_pending_needs_every_product_priced(self, sandbox):
        """Pending is a quote out with the customer, so it needs the figures.

        Added 2026-10-05. Nothing reaches Meta at Pending; the rule is that a
        lead cannot read as quoted while its quote has no amount on it.
        """
        import uuid as _uuid

        from fastapi import HTTPException
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        lead, broker, _ = self._lead(sandbox, "Auto and Homeowner")
        supabase.table("premium_history").insert({
            "lead_id": lead["id"], "new_auto_premium_annual": "200",
        }).execute()

        with pytest.raises(HTTPException) as err:
            lead_service.update_stage(
                _uuid.UUID(lead["id"]),
                UpdateStageRequest(stage="Pending"),
                _uuid.UUID(broker["id"]),
            )
        assert err.value.status_code == 422
        assert "home" in err.value.detail.lower()

    def test_sold_needs_only_the_figures(self, sandbox):
        """Premiums are the ONLY thing Sold requires.

        A broker marking a lead sold on the phone has the numbers in hand and
        little else — and the numbers are what Meta's CompleteRegistration
        event carries. Nothing about carriers gates this (0083 removed the
        last such guard).
        """
        import uuid as _uuid

        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        lead, broker, _ = self._lead(sandbox, "Auto and Homeowner")
        supabase.table("premium_history").insert({
            "lead_id": lead["id"],
            "new_auto_premium_annual": "200",
            "new_homeowner_premium_annual": "100",
        }).execute()

        lead_service.update_stage(
            _uuid.UUID(lead["id"]),
            UpdateStageRequest(stage="Sold"),
            _uuid.UUID(broker["id"]),
        )


# â”€â”€ A broker who stopped their own leads (0082) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class TestBrokerSelfPause:
    """self_paused must be read at all FOUR eligibility sites in assign_lead.

    Three of four is the dangerous outcome, not a partial one: a broker who
    stops receiving normal leads and keeps receiving overtime looks, to the
    person who just switched themselves off, exactly like the feature not
    working.
    """

    def test_a_paused_broker_is_skipped_and_a_groupmate_gets_the_lead(self, sandbox):
        """Site 1 â€” Group 1. The whole point of the feature."""
        group = sandbox.group()
        away = sandbox.broker(group["id"], "Away", daily_cap=5, max_cap=5,
                              self_paused=True)
        here = sandbox.broker(group["id"], "Here", daily_cap=5, max_cap=5)
        names = {away["id"]: "Away", here["id"]: "Here"}

        got = []
        for i in range(4):
            lead = sandbox.lead(group["id"], f"L{i}")
            got.append(names.get(sandbox.assign(lead["id"])))

        assert set(got) == {"Here"}, got

    def test_a_paused_broker_gets_no_OVERTIME_either(self, sandbox):
        """Site 4 â€” Group 2, and the one easiest to leave out.

        ready_for_more survives a pause: it is a separate daily opt-in, so a
        broker who volunteered for overtime this morning and then went home
        still has the flag set. Without the predicate here they would keep
        receiving overtime leads all day while believing they had stopped.
        """
        group = sandbox.group()
        away = sandbox.broker(group["id"], "Away", daily_cap=1, max_cap=9,
                              ready_for_more=True, self_paused=True)
        here = sandbox.broker(group["id"], "Here", daily_cap=1, max_cap=9,
                              ready_for_more=True)
        names = {away["id"]: "Away", here["id"]: "Here"}

        got = []
        for i in range(6):
            lead = sandbox.lead(group["id"], f"L{i}")
            got.append(names.get(sandbox.assign(lead["id"])))

        assert "Away" not in got, got
        # And overtime really did open â€” otherwise this test would pass just
        # because nothing was assigned at all.
        assert got.count("Here") > 1, got

    def test_a_paused_broker_does_not_hold_the_overtime_gate_shut(self, sandbox):
        """Sites 2 and 3 â€” both halves of the gate.

        A paused broker sitting under their cap must not count as "someone
        still owed their share". If they did, the gate would wait for a cap
        they cannot approach and overtime would never open for anybody â€” the
        opposite failure, and a whole group stops receiving leads because one
        person went home.
        """
        group = sandbox.group()
        away = sandbox.broker(group["id"], "Away", daily_cap=9, max_cap=9,
                              self_paused=True)
        keen = sandbox.broker(group["id"], "Keen", daily_cap=1, max_cap=5,
                              ready_for_more=True)
        names = {away["id"]: "Away", keen["id"]: "Keen"}

        got = []
        for i in range(4):
            lead = sandbox.lead(group["id"], f"L{i}")
            got.append(names.get(sandbox.assign(lead["id"])))

        assert "Away" not in got, got
        # Past Keen's daily_cap of 1, so these came through the gate.
        assert got.count("Keen") == 4, got

    def test_resuming_hands_over_the_leads_already_waiting(self, sandbox):
        """Switching back on must offer the BACKLOG, not just the next lead.

        A broker who comes back to leads that arrived while they were away,
        and finds none of them, has been given a switch that quietly cost them
        the morning.
        """
        group = sandbox.group()
        away = sandbox.broker(group["id"], "Away", daily_cap=5, max_cap=5,
                              self_paused=True)

        # Nobody else in the group, so these can go nowhere while paused.
        for i in range(3):
            lead = sandbox.lead(group["id"], f"L{i}")
            assert sandbox.assign(lead["id"]) is None

        supabase.rpc("set_broker_self_paused", {
            "p_broker_id": away["id"], "p_paused": False,
        }).execute()
        supabase.rpc("assign_pending_leads_for_group", {
            "p_group_id": group["id"],
        }).execute()

        placed = supabase.table("lead").select("assigned_broker_id").in_(
            "id", sandbox.lead_ids
        ).execute().data
        assert all(row["assigned_broker_id"] == away["id"] for row in placed), placed

    def test_the_backlog_is_shared_by_rotation_not_given_to_the_returner(self, sandbox):
        """Coming back does not jump the queue.

        The drain runs through the normal rotation rather than handing the
        pool to whoever just switched on. Here the colleague is already at
        their cap, so the waiting leads are legitimately the returner's â€” what
        must not happen is them being stranded.
        """
        group = sandbox.group()
        away = sandbox.broker(group["id"], "Away", daily_cap=5, max_cap=5,
                              self_paused=True)
        here = sandbox.broker(group["id"], "Here", daily_cap=4, max_cap=4)
        names = {away["id"]: "Away", here["id"]: "Here"}

        # Four arrive while Away is paused â€” all land on Here, filling its cap.
        for i in range(4):
            sandbox.assign(sandbox.lead(group["id"], f"L{i}")["id"])

        # Four more that nobody can take: Here is full, Away is paused.
        waiting = [sandbox.lead(group["id"], f"W{i}") for i in range(4)]
        for lead in waiting:
            assert sandbox.assign(lead["id"]) is None

        supabase.rpc("set_broker_self_paused", {
            "p_broker_id": away["id"], "p_paused": False,
        }).execute()
        supabase.rpc("assign_pending_leads_for_group", {
            "p_group_id": group["id"],
        }).execute()

        placed = supabase.table("lead").select("id, assigned_broker_id").in_(
            "id", [lead["id"] for lead in waiting]
        ).execute().data
        got = Counter(names.get(row["assigned_broker_id"]) for row in placed)
        assert got["Away"] == 4, got

    def test_two_resumes_at_once_neither_collide_nor_strand_leads(self, sandbox):
        """THE BUG THIS MIGRATION FIXES, and it predates the feature.

        assign_pending_leads_for_group had no lock. Two drains for the same
        group therefore read the SAME list of unassigned leads: one assigned
        them, the other hit the unique index on lead_delivery(lead_id),
        aborted, and left leads it had not yet reached still waiting.

        Two brokers coming back at the same moment is the ordinary way to hit
        it. Asserting on what is LEFT rather than on the exception, because
        the damage is the stranded leads, not the error.
        """
        import threading

        group = sandbox.group()
        a = sandbox.broker(group["id"], "A", daily_cap=10, max_cap=10,
                           self_paused=True)
        b = sandbox.broker(group["id"], "B", daily_cap=10, max_cap=10,
                           self_paused=True)

        for i in range(12):
            sandbox.assign(sandbox.lead(group["id"], f"L{i}")["id"])

        for broker_id in (a["id"], b["id"]):
            supabase.rpc("set_broker_self_paused", {
                "p_broker_id": broker_id, "p_paused": False,
            }).execute()

        errors = []

        def drain():
            try:
                supabase.rpc("assign_pending_leads_for_group", {
                    "p_group_id": group["id"],
                }).execute()
            except Exception as err:  # noqa: BLE001
                errors.append(err)

        threads = [threading.Thread(target=drain) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        stranded = supabase.table("lead").select("id").in_(
            "id", sandbox.lead_ids
        ).is_("assigned_broker_id", "null").execute().data

        # BOTH ASSERTIONS, and the first one is the one that makes this test
        # mean anything. It was missing at first: `errors` was collected and
        # then only interpolated into the other message, never asserted on.
        #
        # Without it the unlocked implementation could still pass. One drain
        # gets through all twelve leads while the other aborts on the unique
        # index — stranded is empty, the test is green, and a crashing drain
        # goes unreported. Whether leads are stranded then depends on which
        # drain won the race and how far it had got, which is timing, not
        # behaviour.
        #
        # A drain must never raise. It is called from the daily reset (0020),
        # where an exception aborts the loop and leaves every group after this
        # one undrained.
        assert errors == [], f"a drain raised: {errors}"
        assert stranded == [], f"{len(stranded)} leads left unassigned"

    def test_clearing_self_pause_does_not_clear_an_admin_block(self, sandbox):
        """The separation that makes a broker-facing switch safe at all.

        If self-pause and the admin block shared a column, a broker could lift
        their own block â€” which is precisely the capability an admin block
        exists to have.
        """
        group = sandbox.group()
        blocked = sandbox.broker(group["id"], "Blocked", daily_cap=5, max_cap=5,
                                 self_paused=True, is_blocked_by_admin=True)

        supabase.rpc("set_broker_self_paused", {
            "p_broker_id": blocked["id"], "p_paused": False,
        }).execute()

        row = supabase.table("broker").select(
            "self_paused, is_blocked_by_admin"
        ).eq("id", blocked["id"]).execute().data[0]
        assert row["self_paused"] is False
        assert row["is_blocked_by_admin"] is True, "a broker must not lift their own block"

        # And the block still keeps leads away.
        assert sandbox.assign(sandbox.lead(group["id"], "L")["id"]) is None

    def test_the_rpc_returns_the_group_and_none_for_a_stranger(self, sandbox):
        """The service uses the returned group id to drain, and treats None as
        404. A wrong answer either way is a request that reports success and
        changes nothing."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "B", daily_cap=5, max_cap=5)

        returned = supabase.rpc("set_broker_self_paused", {
            "p_broker_id": broker["id"], "p_paused": True,
        }).execute().data
        assert returned == group["id"]

        missing = supabase.rpc("set_broker_self_paused", {
            "p_broker_id": str(uuid.uuid4()), "p_paused": True,
        }).execute().data
        assert missing is None

    def test_an_admin_can_still_hand_a_paused_broker_a_lead(self, sandbox):
        """Deliberate assignment is untouched â€” the same rule 0074 set for
        admins. Self-pause governs the automatic rotation, not what a human
        decides on purpose."""
        group = sandbox.group()
        away = sandbox.broker(group["id"], "Away", daily_cap=5, max_cap=5,
                              self_paused=True)
        admin = sandbox.broker(group["id"], "Admin", role="admin",
                               daily_cap=0, max_cap=0)
        lead = sandbox.lead(group["id"], "L")

        assert sandbox.admin_assign(lead["id"], away["id"], admin["id"]) is None

        row = supabase.table("lead").select("assigned_broker_id").eq(
            "id", lead["id"]
        ).execute().data[0]
        assert row["assigned_broker_id"] == away["id"]

    def test_a_paused_broker_can_still_create_and_hold_a_manual_lead(self, sandbox):
        """Self-pause stops the ROTATION reaching them. It does not stop them
        working.

        A broker who is away from the phones but still doing paperwork â€” or
        who takes a call from a customer directly â€” must be able to enter that
        lead and keep it. Blocking creation would make the switch mean "stop
        working" rather than "stop sending me new leads", and a broker who
        found their own customer could not record them.

        The lead goes through the same round-robin as any other, so it is NOT
        assigned to the paused broker automatically. It is theirs by
        deliberate assignment, which is the admin_assign path â€” the same rule
        0074 set for admins.
        """
        group = sandbox.group()
        away = sandbox.broker(group["id"], "Away", daily_cap=5, max_cap=5,
                              self_paused=True)

        # Created while paused: the row exists, which is the point.
        lead = sandbox.lead(group["id"], "Manual")
        assert lead["id"] is not None

        # The rotation does not hand it to them.
        assert sandbox.assign(lead["id"]) is None

        # But they can be given it deliberately, and they keep it.
        assert sandbox.admin_assign(lead["id"], away["id"], away["id"]) is None
        held = supabase.table("lead").select("assigned_broker_id").eq(
            "id", lead["id"]
        ).execute().data[0]
        assert held["assigned_broker_id"] == away["id"]

        # And pausing again does NOT take it away. Leads already in hand are
        # untouched â€” the switch governs what ARRIVES, nothing else. This is
        # what the confirmation dialog promises the broker in as many words.
        supabase.rpc("set_broker_self_paused", {
            "p_broker_id": away["id"], "p_paused": True,
        }).execute()
        still_held = supabase.table("lead").select("assigned_broker_id").eq(
            "id", lead["id"]
        ).execute().data[0]
        assert still_held["assigned_broker_id"] == away["id"]


class TestTheDailyResetLeavesSelfPauseAlone:
    """The overnight reset, exercised rather than read.

    THIS CLASS USED TO ASSERT THE OPPOSITE. Until migration 0105 the reset
    cleared self_paused for everybody each night, and this test proved it did.
    0105 removed that step: a broker stops their own leads because they are
    away or overloaded, states that do not end at Ontario midnight, and
    switching them back on every morning meant the feature undid itself before
    they were back. What is checked here now is that the flag SURVIVES the
    reset — and that ready_for_more, the per-day flag sitting beside it in the
    same step, still does not.

    ISOLATED, because daily_lead_allocation_reset is GLOBAL â€” it promotes
    every pending cap, clears every broker's flags and drains every group in
    the database. Run carelessly against the shared database it would move
    other people's data.

    Two things make it safe here:

      * the assertions only ever look at THIS sandbox's own broker and lead,
        never at a global count, so another developer's rows cannot change the
        result;
      * lead_allocation_state.last_reset_at is captured before and RESTORED
        afterwards in a finally, so the clock this test winds back does not
        stay wound back for everyone else.

    It still writes globally for the instant it runs, which is why it is one
    test rather than several: the reset is idempotent per Ontario day, so a
    second run the same day is a no-op anyway.
    """

    def test_the_reset_keeps_the_pause_clears_overtime_and_only_runs_once(
        self, sandbox
    ):
        # REFUSES TO RUN IF THE DATABASE IS BUSY, rather than trusting that it
        # is quiet. The reset is global: it clears every broker's
        # ready_for_more, promotes every pending cap and drains every group.
        # (It no longer touches self_paused — 0105 — but a paused broker still
        # means somebody is mid-workflow, so it stays in the check.) That was
        # harmless the day this was written â€” nothing was paused, nobody had
        # opted into overtime, no lead was waiting â€” but "harmless today" is
        # not a property of the test, and the next person to run the suite
        # deserves better than a silent side effect.
        #
        # SKIPPED, not failed: a colleague legitimately using the app is not a
        # broken test.
        busy_paused = supabase.table("broker").select("id").eq(
            "self_paused", True
        ).execute().data
        busy_overtime = supabase.table("broker").select("id").eq(
            "ready_for_more", True
        ).execute().data
        busy_leads = supabase.table("lead").select("id").is_(
            "assigned_broker_id", "null"
        ).execute().data
        if busy_paused or busy_overtime or busy_leads:
            pytest.skip(
                "the daily reset is global â€” refusing to run it while other "
                f"rows would be touched ({len(busy_paused)} paused, "
                f"{len(busy_overtime)} on overtime, {len(busy_leads)} leads "
                "waiting). Run this against a disposable database."
            )

        group = sandbox.group()
        # self_paused_at is set EXPLICITLY, and to a date well in the past, so
        # the assertion below proves the reset leaves a real non-null value
        # alone. Inserted directly by the sandbox rather than through the RPC,
        # which is what lets it be backdated.
        paused_at_before = "2026-01-05T14:30:00+00:00"
        away = sandbox.broker(group["id"], "Away", daily_cap=5, max_cap=5,
                              self_paused=True, self_paused_at=paused_at_before,
                              ready_for_more=True)

        # A lead that can go nowhere: the only broker in the group is paused.
        lead = sandbox.lead(group["id"], "Waiting")
        assert sandbox.assign(lead["id"]) is None

        state = supabase.table("lead_allocation_state").select(
            "last_reset_at"
        ).execute().data[0]
        original_reset_at = state["last_reset_at"]

        try:
            # Wind the clock back a day so the idempotency guard lets the
            # reset run. Ontario DATES are what it compares, so anything
            # yesterday or earlier qualifies.
            #
            # eq("id", True) because lead_allocation_state is a SINGLE-ROW
            # table whose primary key is a boolean pinned to true — that is
            # what stops a second clock ever existing. PostgREST also refuses
            # an UPDATE with no WHERE clause, so the filter is required
            # regardless.
            supabase.table("lead_allocation_state").update({
                "last_reset_at": "2020-01-01T05:00:00+00:00",
            }).eq("id", True).execute()

            try:
                supabase.rpc("daily_lead_allocation_reset", {}).execute()
            except Exception as err:  # noqa: BLE001
                # CANNOT BE RUN THROUGH POSTGREST, and this is a property of
                # the connection, not a bug in the function.
                #
                # Step 2 of the reset is `update lead_allocation_state set
                # last_reset_at = now()` with no WHERE clause — correct, since
                # it is a single-row table. Supabase enables its `safeupdate`
                # guard on the service-role session, which rejects any
                # unqualified UPDATE with SQLSTATE 21000, so the statement
                # fails HERE while succeeding in production.
                #
                # pg_cron runs the function as a superuser without that guard.
                # Verified 2026-08-10: last_reset_at was that morning's
                # Ontario midnight, and broker_activated / daily_cap_promoted
                # rows sit at exactly 04:00:00 UTC on the days there was work
                # to do. The nightly job does run and does complete.
                #
                # Skipped rather than xfailed: on a database without the guard
                # (a local Postgres, a disposable branch) this test SHOULD run
                # and everything below it is the real check.
                if "21000" in str(err) or "WHERE clause" in str(err):
                    pytest.skip(
                        "Supabase's safeupdate guard blocks the reset's "
                        "unqualified UPDATE on this connection; pg_cron runs "
                        "it as superuser without the guard. Run against a "
                        "database without safeupdate to exercise this."
                    )
                raise

            broker_after = supabase.table("broker").select(
                "self_paused, self_paused_at, ready_for_more"
            ).eq("id", away["id"]).execute().data[0]

            # THE WHOLE POINT OF 0105. A broker on vacation is still on
            # vacation in the morning. This is the assertion that used to say
            # the opposite.
            assert broker_after["self_paused"] is True, (
                "the reset must NOT clear a broker's own pause (0105)"
            )
            # And the clock is not restarted either — the reset does not touch
            # this column at all, so an absence keeps its real length.
            assert broker_after["self_paused_at"] == paused_at_before, (
                "the reset must not re-date an ongoing absence"
            )

            # ready_for_more STILL CLEARS. The two flags sit next to each
            # other in STEP 3 and look alike; this is what catches them being
            # changed together by mistake in either direction.
            assert broker_after["ready_for_more"] is False, (
                "overtime is still a per-day choice"
            )

            # THE BACKLOG STAYS PUT. Step 4's drain runs and correctly finds
            # nobody eligible — the only broker in this group is paused.
            # Before 0105, the deleted step 3a made this same lead land on
            # them on a morning they were still away.
            not_drained = supabase.table("lead").select("assigned_broker_id").eq(
                "id", lead["id"]
            ).execute().data[0]
            assert not_drained["assigned_broker_id"] is None, (
                "a paused broker must not be handed the morning's backlog"
            )

            # NOTHING WAS LOGGED. broker_self_pause_cleared was the reset's
            # own action name and nothing writes it any more; a row here would
            # mean step 3a had come back. (An admin resuming somebody writes
            # broker_leads_resumed_by_admin instead — a different name, so the
            # two can never be confused in the one table somebody reads to
            # answer "who did this".)
            logged = supabase.table("audit_log").select("id").eq(
                "action", "broker_self_pause_cleared"
            ).eq("entity_id", away["id"]).execute().data
            assert not logged, "the reset must no longer clear anybody's pause"

            # IDEMPOTENCY. A second run the same Ontario day is a no-op —
            # pg_cron fires this hourly, so it happens 23 more times today.
            #
            # ANCHORED ON ready_for_more, not self_paused. self_paused can no
            # longer prove anything here: it never changes, so it would pass
            # whether the guard worked or not. ready_for_more is now the only
            # flag the reset touches, which makes it the only one that can
            # show a second run doing nothing.
            supabase.table("broker").update({
                "ready_for_more": True,
            }).eq("id", away["id"]).execute()
            supabase.rpc("daily_lead_allocation_reset", {}).execute()

            unchanged = supabase.table("broker").select("ready_for_more").eq(
                "id", away["id"]
            ).execute().data[0]
            assert unchanged["ready_for_more"] is True, (
                "the guard must stop a second reset the same day"
            )
        finally:
            # ALWAYS, even on failure. Leaving the clock wound back would make
            # every later run of the real cron job think a new day had begun.
            supabase.table("lead_allocation_state").update({
                "last_reset_at": original_reset_at,
            }).eq("id", True).execute()


# -- The pause clock, stamped by the RPC that owns the flag -----------------
class TestSelfPausedAtTracksTheAbsence:
    """The three branches of the CASE in set_broker_self_paused (0105).

    WORTH TESTING BECAUSE THE MIDDLE BRANCH FAILS SILENTLY. Once self_paused
    persists, "how long" is the number an admin acts on — it is what the
    roster badge and the detail banner show, and the only guard against a
    broker losing a fortnight of leads to a switch they forgot. If a re-pause
    restarted the clock, nothing would break and nothing would raise; the
    number would just quietly get younger every time anything touched the
    switch, and the one figure built to be believed would be the one figure
    that could not be.

    Goes through the RPC rather than a plain UPDATE — the stamp lives inside
    it, so a direct write would prove nothing about the thing being tested.
    Cheap: no HTTP, no reset, no leads, and nothing global is touched.
    """

    def test_pausing_starts_the_clock(self, sandbox):
        group = sandbox.group()
        b = sandbox.broker(group["id"], "Newly away")
        assert b["self_paused_at"] is None

        supabase.rpc("set_broker_self_paused", {
            "p_broker_id": b["id"], "p_paused": True,
        }).execute()

        row = supabase.table("broker").select("self_paused, self_paused_at").eq(
            "id", b["id"]
        ).execute().data[0]
        assert row["self_paused"] is True
        assert row["self_paused_at"] is not None, "a pause must record when it began"

    def test_pausing_again_does_not_re_date_the_absence(self, sandbox):
        """THE STICKY BRANCH. A double click, a retried request, or an admin
        and a broker acting at once must not turn nine days into today."""
        group = sandbox.group()
        b = sandbox.broker(group["id"], "Long gone",
                           self_paused=True,
                           self_paused_at="2026-01-05T14:30:00+00:00")

        supabase.rpc("set_broker_self_paused", {
            "p_broker_id": b["id"], "p_paused": True,
        }).execute()

        row = supabase.table("broker").select("self_paused_at").eq(
            "id", b["id"]
        ).execute().data[0]
        assert row["self_paused_at"] == b["self_paused_at"], (
            "re-pausing must leave an absence already in progress alone"
        )

    def test_resuming_clears_the_clock(self, sandbox):
        group = sandbox.group()
        b = sandbox.broker(group["id"], "Back",
                           self_paused=True,
                           self_paused_at="2026-01-05T14:30:00+00:00")

        supabase.rpc("set_broker_self_paused", {
            "p_broker_id": b["id"], "p_paused": False,
        }).execute()

        row = supabase.table("broker").select("self_paused, self_paused_at").eq(
            "id", b["id"]
        ).execute().data[0]
        assert row["self_paused"] is False
        assert row["self_paused_at"] is None, (
            "no pause is in progress, so there is no date to show"
        )

    def test_pausing_after_a_resume_stamps_a_fresh_date(self, sandbox):
        """The clock is sticky WITHIN one absence, not across two. A broker
        back from vacation who stops again next month is newly away."""
        group = sandbox.group()
        b = sandbox.broker(group["id"], "Away twice",
                           self_paused=True,
                           self_paused_at="2026-01-05T14:30:00+00:00")

        for paused in (False, True):
            supabase.rpc("set_broker_self_paused", {
                "p_broker_id": b["id"], "p_paused": paused,
            }).execute()

        row = supabase.table("broker").select("self_paused_at").eq(
            "id", b["id"]
        ).execute().data[0]
        assert row["self_paused_at"] is not None
        assert row["self_paused_at"] != b["self_paused_at"], (
            "a new absence must not inherit the old one's start date"
        )


# -- Postgres computes the premium totals, honouring the unit --------------
class TestPremiumTotalsHonourTheUnit:
    """The six product columns hold WHAT THE BROKER TYPED; the unit columns say
    whether that is a month or a year; and the totals are generated columns
    that multiply by twelve when they need to (0103).

    WHY THE DATABASE AND NOT PYTHON. Five attempts to do this in application
    code each shipped a defect: one divided stored figures for display, one
    multiplied by twelve TWICE (3650 -> 43800 -> 525600), one compared a typed
    value against an already-updated draft and silently saved nothing. A
    generated column is recomputed on every write, so there is no ordering to
    get wrong and no retry that can multiply twice.

    These replace TestPremiumsConvertAtQuoteDone, which tested a conversion
    that no longer exists: services/lead.py used to rewrite the row at the
    stage move, and now only asks about an implausible figure.
    """

    def _lead(self, sandbox, policy_type="Auto Only", **premium):
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "PB", daily_cap=5, max_cap=5)
        lead = sandbox.lead(
            group["id"], "meta", source="meta", is_qualified=True,
            platform_leadgen_id=f"{PREFIX}-{uuid.uuid4().hex[:14]}",
            combined_policy_type=policy_type,
        )
        assert sandbox.assign(lead["id"]) == broker["id"]
        sandbox.applicant(lead["id"])
        supabase.table("premium_history").insert(
            {"lead_id": lead["id"], **premium}
        ).execute()
        return lead, broker

    def _row(self, lead_id):
        return supabase.table("premium_history").select("*").eq(
            "lead_id", lead_id
        ).execute().data[0]

    def _move(self, lead_id, broker_id, stage="Quote Done", confirm=False):
        from schemas.lead import UpdateStageRequest
        from services import lead as lead_service

        return lead_service.update_stage(
            uuid.UUID(str(lead_id)),
            UpdateStageRequest(stage=stage, confirm=confirm),
            uuid.UUID(str(broker_id)),
        )

    # -- the totals themselves ---------------------------------------------
    def test_a_monthly_row_totals_twelve_times(self, sandbox):
        """THE BUG THIS FIXES. 390 + 90 a month read as 480 a year."""
        lead, _ = self._lead(
            sandbox, "Auto and Homeowner",
            # TYPED INTO THE MONTHLY COLUMNS (0106) — the trigger derives the
            # annual pair. Before 0106 these figures went into columns named
            # *_annual and a flag said to read them as monthly, which is the
            # arrangement that produced the bug in this test's name.
            current_auto_premium_monthly=390,
            current_homeowner_premium_monthly=90,
            current_premium_unit="month",
        )
        row = self._row(lead["id"])
        assert float(row["total_current_premium_annual"]) == 5760
        assert float(row["total_current_premium_monthly"]) == 480
        # And the typed figures are untouched, in the columns that name them.
        assert float(row["current_auto_premium_monthly"]) == 390
        assert float(row["current_auto_premium_annual"]) == 4680

    def test_a_yearly_row_is_left_alone(self, sandbox):
        lead, _ = self._lead(
            sandbox, current_auto_premium_annual=1800, current_premium_unit="year",
        )
        row = self._row(lead["id"])
        assert float(row["total_current_premium_annual"]) == 1800
        assert float(row["total_current_premium_monthly"]) == 150

    def test_each_side_carries_its_own_unit(self, sandbox):
        """A broker can read Current off a monthly statement and quote the new
        policy yearly. Subtracting one from the other before both are annual is
        the original bug in miniature."""
        lead, _ = self._lead(
            sandbox, "Auto and Homeowner",
            current_auto_premium_monthly=390,
            current_homeowner_premium_monthly=90,
            current_premium_unit="month",
            new_auto_premium_annual=4200,
            new_homeowner_premium_annual=900,
            new_premium_unit="year",
        )
        row = self._row(lead["id"])
        assert float(row["total_current_premium_annual"]) == 5760
        assert float(row["total_new_premium_annual"]) == 5100
        assert float(row["savings_annual"]) == 660
        assert float(row["savings_monthly"]) == 55

    def test_a_negative_saving_is_kept(self, sandbox):
        """The proposed policy sometimes costs more, and the broker has to see
        that before the customer does."""
        lead, _ = self._lead(
            sandbox,
            current_auto_premium_annual=1200,
            new_auto_premium_annual=1800,
            current_premium_unit="year",
            new_premium_unit="year",
        )
        assert float(self._row(lead["id"])["savings_annual"]) == -600

    def test_switching_the_unit_alone_changes_no_money(self, sandbox):
        """THIS TEST ASSERTED THE OPPOSITE BEFORE 0106, and the old behaviour
        was a slow corruption dressed as a feature.

        The totals used to be generated from the typed figures TIMES the unit,
        so flipping the button re-read $300 a year as $300 a month and the
        total leapt to $3,600. A view control was silently restating what the
        customer pays.

        Both readings are stored now, and the trigger only re-derives when the
        TYPED figure moves. Without that guard a unit flip would push every
        number through a round trip it does not survive: $1,819 a year becomes
        $152 a month, and 152 x 12 is $1,824. Five dollars invented by
        clicking. Toggling again invents five more.

        So the money stands still and only the reading the form reopens on
        changes — which is all a unit switch ever meant.
        """
        lead, _ = self._lead(
            sandbox, current_auto_premium_annual=300, current_premium_unit="year",
        )
        assert float(self._row(lead["id"])["total_current_premium_annual"]) == 300

        supabase.table("premium_history").update(
            {"current_premium_unit": "month"}
        ).eq("lead_id", lead["id"]).execute()

        row = self._row(lead["id"])
        assert float(row["total_current_premium_annual"]) == 300, (
            "switching the button must not restate the premium"
        )
        assert float(row["current_auto_premium_annual"]) == 300
        assert float(row["current_auto_premium_monthly"]) == 25

    def test_a_rounded_figure_survives_a_unit_round_trip(self, sandbox):
        """The drift the guard above exists to stop, measured. $1,819 a year
        is $151.58 a month, stored as 152 — and 152 x 12 is 1,824. Flipping
        the unit twice must not turn 1,819 into 1,824."""
        lead, _ = self._lead(
            sandbox, current_auto_premium_annual=1819, current_premium_unit="year",
        )
        for unit in ("month", "year", "month"):
            supabase.table("premium_history").update(
                {"current_premium_unit": unit}
            ).eq("lead_id", lead["id"]).execute()

        assert float(self._row(lead["id"])["current_auto_premium_annual"]) == 1819

    def test_the_typed_figures_are_never_rewritten(self, sandbox):
        """The row keeps what the broker typed. Moving the stage used to
        multiply these columns in place; nothing does now."""
        lead, broker = self._lead(
            sandbox, new_auto_premium_monthly=150, new_premium_unit="month",
        )
        self._move(lead["id"], broker["id"])

        row = self._row(lead["id"])
        # THE TYPED FIGURE STANDS, IN THE COLUMN THAT NAMES IT (0106). This
        # used to assert new_auto_premium_annual == 150 — a column called
        # annual holding a monthly figure, which is the whole thing 0106 ended.
        assert float(row["new_auto_premium_monthly"]) == 150, "the typed figure must stand"
        assert float(row["new_auto_premium_annual"]) == 1800, "and the derived one is annual"
        assert row["new_premium_unit"] == "month", "the unit must stand too"
        assert float(row["total_new_premium_annual"]) == 1800

    # -- what Meta receives -------------------------------------------------
    def test_the_signal_snapshot_holds_the_annual_total(self, sandbox):
        """THE ONE THAT MATTERS. The trigger reads total_new_premium_annual at
        the instant the stage row is written, and event_id is what Meta
        deduplicates on — so a monthly figure sent unconverted would be wrong
        forever."""
        lead, broker = self._lead(
            sandbox, new_auto_premium_monthly=150, new_premium_unit="month",
        )
        self._move(lead["id"], broker["id"])

        signals = supabase.table("meta_signal").select("event_name,payload").eq(
            "lead_id", lead["id"]
        ).eq("event_name", "CompleteRegistration").execute().data
        assert signals, "Quote Done must fire CompleteRegistration"
        snapshot = signals[0]["payload"]["total_new_premium_at_registration"]
        assert float(snapshot) == 1800, f"Meta would have received {snapshot}"

    # -- the low-auto question ----------------------------------------------
    def test_a_low_auto_premium_asks_before_proceeding(self, sandbox):
        """No real Ontario auto policy costs $480 a year, so it is far more
        likely to be a monthly figure with the unit left on Year.

        409, not 422: nothing about the request is malformed. The broker is
        being asked a question, not corrected."""
        from fastapi import HTTPException

        lead, broker = self._lead(
            sandbox, new_auto_premium_annual=480, new_premium_unit="year",
        )
        with pytest.raises(HTTPException) as err:
            self._move(lead["id"], broker["id"])

        assert err.value.status_code == 409
        assert "480" in err.value.detail

    def test_the_question_reads_the_annual_figure(self, sandbox):
        """150 a month is 1,800 a year and must pass SILENTLY. Asking here
        would be asking the broker to confirm the very thing they just told
        us."""
        lead, broker = self._lead(
            sandbox, new_auto_premium_monthly=150, new_premium_unit="month",
        )
        self._move(lead["id"], broker["id"])  # must not raise

        assert float(self._row(lead["id"])["total_new_premium_annual"]) == 1800

    def test_refusing_the_question_changes_nothing(self, sandbox):
        """The 409 is raised before the stage write, so declining leaves the
        lead exactly where it was."""
        from fastapi import HTTPException

        lead, broker = self._lead(
            sandbox, new_auto_premium_annual=480, new_premium_unit="year",
        )
        with pytest.raises(HTTPException):
            self._move(lead["id"], broker["id"])

        stage = supabase.table("lead").select("stage").eq(
            "id", lead["id"]
        ).execute().data[0]["stage"]
        assert stage != "Quote Done"
        assert float(self._row(lead["id"])["new_auto_premium_annual"]) == 480

    def test_confirming_lets_it_through(self, sandbox):
        lead, broker = self._lead(
            sandbox, new_auto_premium_annual=480, new_premium_unit="year",
        )
        self._move(lead["id"], broker["id"], confirm=True)

        assert float(self._row(lead["id"])["new_auto_premium_annual"]) == 480

    def test_a_low_home_premium_does_not_ask(self, sandbox):
        """AUTO ONLY. Five live premium rows hold a genuine home or tenant
        premium under $1,000, so warning on those would teach brokers to click
        through the warning that matters."""
        lead, broker = self._lead(
            sandbox, "Auto and Homeowner",
            new_auto_premium_annual=1800,
            new_homeowner_premium_annual=480,
            new_premium_unit="year",
        )
        self._move(lead["id"], broker["id"])  # must not raise

        assert float(self._row(lead["id"])["new_homeowner_premium_annual"]) == 480


# -- The reminders the system sets itself ---------------------------------
class TestRenewalRemindersSweep:
    """Two rules, both keyed off the principal driver's renewal_date (0098),
    and a unique index (0104) that makes running them hourly free.

    WHY HOURLY AND NOT DAILY. A once-a-day sweep has a one-day window, so a
    lead moved to Pending at 10am -- after the 1am run -- is four days out by
    the next one and the five-day rule never fires at all.

    WHY THE CYCLE DATE. Keying the index on status = 'Open' would mean
    dismissing a reminder removes it from the index, so the next sweep
    recreates the thing the broker just dismissed. Keying on the renewal date
    blocks that AND still allows next year's.
    """

    def _lead_renewing_in(self, sandbox, days, stage):
        from datetime import date, timedelta

        group = sandbox.group()
        broker = sandbox.broker(group["id"], "RB", daily_cap=5, max_cap=5)
        lead = sandbox.lead(group["id"], "renewal lead", combined_policy_type="Auto Only")
        assert sandbox.assign(lead["id"]) == broker["id"]
        driver = sandbox.applicant(lead["id"])

        renewal = (date.today() + timedelta(days=days)).isoformat()
        supabase.table("driver").update({"renewal_date": renewal}).eq(
            "id", driver["id"]
        ).execute()
        supabase.table("lead").update({"stage": stage}).eq("id", lead["id"]).execute()
        return lead, broker, renewal

    def _reminders(self, lead_id):
        return supabase.table("reminder").select("*").eq(
            "lead_id", lead_id
        ).execute().data

    def _sweep(self):
        from services import reminder as reminder_service

        return reminder_service.sweep()

    # -- the rules fire ----------------------------------------------------
    def test_a_pending_lead_five_days_out_gets_a_reminder(self, sandbox):
        """The one that matters most: miss a renewal and the customer
        auto-renewed with their existing insurer for a year."""
        lead, broker, renewal = self._lead_renewing_in(sandbox, 5, "Pending")
        self._sweep()

        rows = [r for r in self._reminders(lead["id"]) if r["auto_rule"]]
        assert len(rows) == 1
        assert rows[0]["auto_rule"] == "renewal_pending"
        assert rows[0]["kind"] == "Renewal"
        assert rows[0]["auto_for_renewal_date"] == renewal
        assert rows[0]["broker_id"] == broker["id"], "must come off the lead's assignment"

    def test_a_lost_lead_thirty_days_out_gets_a_reminder(self, sandbox):
        """A customer who said no in March is a different conversation in
        September, when their own renewal is in front of them."""
        lead, _, renewal = self._lead_renewing_in(sandbox, 30, "Lost")
        self._sweep()

        rows = [r for r in self._reminders(lead["id"]) if r["auto_rule"]]
        assert len(rows) == 1
        assert rows[0]["auto_rule"] == "renewal_lost"

    def test_the_window_is_exact(self, sandbox):
        """Four days out and six days out are both silent. The rule is a
        boundary, not a range -- a range would fire every day it stayed true
        and the index would be doing all the work."""
        for days in (4, 6):
            lead, _, _ = self._lead_renewing_in(sandbox, days, "Pending")
            self._sweep()
            assert not [r for r in self._reminders(lead["id"]) if r["auto_rule"]]

    def test_the_stage_has_to_match(self, sandbox):
        """A New lead five days from renewal is not the Pending rule's
        business -- nobody has quoted it yet."""
        lead, _, _ = self._lead_renewing_in(sandbox, 5, "New")
        self._sweep()
        assert not [r for r in self._reminders(lead["id"]) if r["auto_rule"]]

    def test_no_renewal_date_means_no_reminder(self, sandbox):
        """Most of the book is in this state -- 59 of 78 live drivers -- and a
        rule that guessed would be worse than one that stays quiet."""
        group = sandbox.group()
        broker = sandbox.broker(group["id"], "RB", daily_cap=5, max_cap=5)
        lead = sandbox.lead(group["id"], "no renewal")
        assert sandbox.assign(lead["id"]) == broker["id"]
        sandbox.applicant(lead["id"])
        supabase.table("lead").update({"stage": "Pending"}).eq("id", lead["id"]).execute()

        self._sweep()
        assert not [r for r in self._reminders(lead["id"]) if r["auto_rule"]]

    def test_a_past_renewal_date_never_fires(self, sandbox):
        """Four live rows carry a date already gone, one by 2,483 days. "In N
        days" simply never matches, which is the correct outcome and not a
        special case."""
        lead, _, _ = self._lead_renewing_in(sandbox, -30, "Pending")
        self._sweep()
        assert not [r for r in self._reminders(lead["id"]) if r["auto_rule"]]

    # -- idempotence, which is what lets it run hourly ---------------------
    def test_sweeping_three_times_creates_one_reminder(self, sandbox):
        """THE GUARD. Without it a lead matching for a month becomes hundreds
        of identical rows, and a list full of near-duplicates is how a reminder
        system dies."""
        lead, _, _ = self._lead_renewing_in(sandbox, 5, "Pending")
        self._sweep()
        self._sweep()
        self._sweep()

        assert len([r for r in self._reminders(lead["id"]) if r["auto_rule"]]) == 1

    def test_a_dismissed_reminder_is_not_recreated(self, sandbox):
        """The bug a status-filtered index would have: dismissing removes the
        row from the index, so the next sweep an hour later recreates what the
        broker just dismissed. The system would argue with them hourly."""
        lead, _, _ = self._lead_renewing_in(sandbox, 5, "Pending")
        self._sweep()

        row = [r for r in self._reminders(lead["id"]) if r["auto_rule"]][0]
        supabase.table("reminder").update(
            {"status": "Dismissed", "completed_at": "now()"}
        ).eq("id", row["id"]).execute()

        self._sweep()

        rows = [r for r in self._reminders(lead["id"]) if r["auto_rule"]]
        assert len(rows) == 1, "the dismissal must not be undone"
        assert rows[0]["status"] == "Dismissed"

    def test_next_year_gets_its_own_reminder(self, sandbox):
        """The other half of keying on the renewal date: a customer must get a
        reminder every cycle, not one ever."""
        from datetime import date, timedelta

        lead, broker, _ = self._lead_renewing_in(sandbox, 5, "Pending")
        self._sweep()

        driver = supabase.table("driver").select("id").eq(
            "lead_id", lead["id"]
        ).eq("is_principal", True).execute().data[0]
        next_cycle = (date.today() + timedelta(days=5 + 365)).isoformat()
        supabase.table("driver").update({"renewal_date": next_cycle}).eq(
            "id", driver["id"]
        ).execute()

        # Not due yet -- a year out, so the boundary does not match.
        self._sweep()
        assert len([r for r in self._reminders(lead["id"]) if r["auto_rule"]]) == 1

    def test_a_brokers_own_reminders_are_never_blocked(self, sandbox):
        """The index constrains system rows only. Postgres treats NULLs as
        distinct, so a broker can set as many of their own on one lead as they
        like -- which is the whole reason no partial predicate is needed."""
        from datetime import datetime, timedelta, timezone

        from services import reminder as reminder_service

        lead, broker, _ = self._lead_renewing_in(sandbox, 5, "Pending")
        due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        for i in range(3):
            reminder_service.create(
                lead_id=lead["id"], broker_id=broker["id"], due_at=due,
                note=f"manual {i}",
            )

        manual = [r for r in self._reminders(lead["id"]) if not r["auto_rule"]]
        assert len(manual) == 3

    def test_an_unassigned_lead_is_skipped_not_fatal(self, sandbox):
        """Cannot happen today -- every one of the 93 live leads is assigned --
        but the column is nullable, and one bad row must not cost every other
        reminder that hour."""
        lead, _, _ = self._lead_renewing_in(sandbox, 5, "Pending")
        supabase.table("lead").update({"assigned_broker_id": None}).eq(
            "id", lead["id"]
        ).execute()

        result = self._sweep()  # must not raise
        assert not [r for r in self._reminders(lead["id"]) if r["auto_rule"]]
        assert result["created"] == 0

    # -- what the page reads -----------------------------------------------
    def test_the_list_carries_stage_and_renewal_date(self, sandbox):
        """What the Reminders page triages on. They come from different tables
        -- stage from lead, renewal_date from the principal driver -- and the
        service flattens the second onto the summary."""
        from services import reminder as reminder_service

        lead, broker, renewal = self._lead_renewing_in(sandbox, 5, "Pending")
        self._sweep()

        rows = reminder_service.list_mine(broker["id"])
        mine = [r for r in rows if r["lead_id"] == lead["id"]]
        assert mine, "the broker should see their own reminder"
        assert mine[0]["lead"]["stage"] == "Pending"
        assert mine[0]["lead"]["renewal_date"] == renewal
        assert "driver" not in mine[0]["lead"], "the nesting should be flattened away"

    def test_the_list_is_scoped_to_the_broker(self, sandbox):
        """A broker must not read anyone else's follow-ups."""
        from services import reminder as reminder_service

        lead, _, _ = self._lead_renewing_in(sandbox, 5, "Pending")
        self._sweep()

        other_group = sandbox.group()
        other = sandbox.broker(other_group["id"], "OB", daily_cap=5, max_cap=5)

        assert not [
            r
            for r in reminder_service.list_mine(other["id"])
            if r["lead_id"] == lead["id"]
        ]
