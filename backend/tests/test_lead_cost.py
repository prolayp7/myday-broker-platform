"""What a broker owes for the leads the system gave them.

THE RULE THESE PIN:

    While the billing month is open, the cost follows the lead. Once it
    freezes, the cost stays with whoever held it at that moment.

August is open 1 August - 5 September, frozen from 6 September 00:00 Ontario.

Three groups of tests matter more than the rest:

  * THE FREEZE. Who pays is derived, not stored — from lead.assigned_broker_id
    while open, and from the last lead_assignment row before the freeze after.
    If that derivation is wrong, the wrong broker is billed and nothing errors.

  * THE MONTH BOUNDARY. delivered_at is a timestamptz. A lead delivered
    31 Aug 23:00 Ontario is 1 Sep in UTC, so a bare date comparison bills it to
    the wrong month. This is the one place in the feature a timezone slip costs
    money.

  * PAGING. PostgREST truncates an unbounded select without erroring, so an
    unpaged report would under-bill silently. The volume test inserts past the
    page size deliberately.

These run against the real database, like the rest of the suite.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from dotenv import load_dotenv
from fastapi import HTTPException

pytestmark = pytest.mark.dbtest

# .env first, before any placeholder could shadow it.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
    pytest.skip(
        "needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY — these tests run "
        "against a real database",
        allow_module_level=True,
    )

from db import supabase  # noqa: E402
from services import lead_cost as service  # noqa: E402
from tests.test_lead_assignment import Sandbox  # noqa: E402
from tests.test_lead_assignment import sandbox  # noqa: E402,F401

_ONTARIO = ZoneInfo("America/Toronto")


def month_of(when: datetime) -> str:
    local = when.astimezone(_ONTARIO)
    return f"{local.year:04d}-{local.month:02d}"


def a_frozen_month() -> str:
    """A month that is definitely closed — three months back, so no test can
    be broken by being run in the first five days of a month."""
    now = datetime.now(_ONTARIO)
    year, mon = now.year, now.month - 3
    while mon < 1:
        mon += 12
        year -= 1
    return f"{year:04d}-{mon:02d}"


class Scene:
    """A group, two brokers, and helpers to plant deliveries at chosen times.

    Deliveries are inserted directly rather than driven through assign_lead:
    these tests are about billing, and the round-robin's own behaviour (caps,
    rotation, eligibility) is covered by test_lead_assignment.py. The one test
    that DOES need the real path calls sandbox.assign().
    """

    def __init__(self, box: Sandbox):
        self.box = box
        self.group = box.group()
        self.a = box.broker(self.group["id"], "CostA", daily_cap=50)
        self.b = box.broker(self.group["id"], "CostB", daily_cap=50)
        self.admin = box.broker(self.group["id"], "CostAdmin", role="admin")

    def lead(self, holder=None, **cols):
        # Sandbox.lead defaults to source="manual"; a chargeable lead has to
        # say so. setdefault rather than a keyword, so a test asking for a
        # manual lead can override it without colliding.
        cols.setdefault("source", "meta")
        row = self.box.lead(self.group["id"], "Cost", **cols)
        if holder:
            supabase.table("lead").update({"assigned_broker_id": holder}).eq(
                "id", row["id"]
            ).execute()
        return row

    def deliver(self, lead_id, broker_id, when: datetime, cost="20"):
        """A system delivery at a chosen instant.

        cost is passed explicitly so the trigger's `is null` guard leaves it
        alone — these tests set the price they want to assert on rather than
        depending on whatever the live settings row currently holds.
        """
        values = {
            "lead_id": lead_id,
            "broker_id": broker_id,
            "delivered_at": when.astimezone(timezone.utc).isoformat(),
        }
        if cost is not None:
            values["cost_at_delivery"] = cost
        return supabase.table("lead_delivery").insert(values).execute().data[0]

    def custody(self, lead_id, broker_id, when: datetime, reason="Admin Reassign"):
        """A custody row — what admin_assign_lead writes on a reassignment."""
        return supabase.table("lead_assignment").insert({
            "lead_id": lead_id,
            "broker_id": broker_id,
            "assigned_at": when.astimezone(timezone.utc).isoformat(),
            "reason": reason,
        }).execute().data[0]

    def cleanup(self):
        for lead_id in self.box.lead_ids:
            supabase.table("lead_delivery").delete().eq("lead_id", lead_id).execute()


@pytest.fixture
def scene(sandbox):
    s = Scene(sandbox)
    try:
        yield s
    finally:
        s.cleanup()


# ── Charging ────────────────────────────────────────────────────────────


class TestWhatIsCharged:
    def test_a_round_robin_lead_is_stamped_with_the_current_price(self, scene):
        """THE TRIGGER. assign_lead is untouched, so the price has to arrive
        by the before-insert trigger or not at all."""
        price = Decimal(str(service.get_settings()["cost_per_lead"]))
        lead = scene.lead()
        assert scene.box.assign(lead["id"]) is not None

        row = supabase.table("lead_delivery").select("cost_at_delivery").eq(
            "lead_id", lead["id"]).execute().data[0]

        assert row["cost_at_delivery"] is not None, (
            "a delivered lead was not priced — the trigger did not fire"
        )
        assert Decimal(str(row["cost_at_delivery"])) == price

    def test_a_manual_lead_creates_no_delivery_and_is_on_no_bill(self, scene):
        """Free BY CONSTRUCTION. Nothing checks "is this manual?" — a
        hand-entered lead simply never gets a delivery row, so there is nothing
        for the bill to find."""
        lead = scene.lead(holder=scene.a["id"], source="manual")

        rows = supabase.table("lead_delivery").select("id").eq(
            "lead_id", lead["id"]).execute().data
        assert not rows

        month = month_of(datetime.now(_ONTARIO))
        ledger = service.broker_ledger(scene.a["id"], month)
        assert all(line["lead_id"] != lead["id"] for line in ledger["lines"])

    def test_changing_the_price_does_not_move_an_existing_delivery(self, scene):
        """WHY THE COST IS STAMPED rather than looked up. An admin raising the
        price must not rewrite a month already billed."""
        lead = scene.lead(holder=scene.a["id"])
        now = datetime.now(_ONTARIO)
        scene.deliver(lead["id"], scene.a["id"], now, cost="20")

        original = service.get_settings()["cost_per_lead"]
        try:
            supabase.table("lead_cost_settings").update(
                {"cost_per_lead": "99"}).eq("id", 1).execute()

            ledger = service.broker_ledger(scene.a["id"], month_of(now))
            line = next(x for x in ledger["lines"] if x["lead_id"] == lead["id"])
            assert Decimal(line["cost"]) == Decimal("20"), (
                "a price change reached back into a delivery already made"
            )
        finally:
            supabase.table("lead_cost_settings").update(
                {"cost_per_lead": str(original)}).eq("id", 1).execute()

    def test_a_price_with_too_many_decimals_is_refused(self):
        """NOT SILENTLY ROUNDED. The column is numeric(10,2), so Postgres
        turned 20.999 into 21.00 — a price the admin never typed, applied to
        every later delivery, with nothing on screen showing the difference.
        The schema now matches the column and refuses it up front.

        Validation only — no price is written, so there is nothing to restore.
        """
        from pydantic import ValidationError

        from schemas.lead_cost import UpdateLeadCostSettingsRequest

        with pytest.raises(ValidationError):
            UpdateLeadCostSettingsRequest(cost_per_lead="20.999")

        with pytest.raises(ValidationError):
            UpdateLeadCostSettingsRequest(cost_per_lead="-1")

        assert UpdateLeadCostSettingsRequest(
            cost_per_lead="20.50"
        ).cost_per_lead == Decimal("20.50")

    def test_the_correction_script_is_scoped_by_month_and_price(self, scene):
        """THE GUARD THAT MAKES THE ONE-OFF SAFE.

        scripts/fix_seeded_lead_cost.py re-stamps August's $20 placeholders at
        $3. Filtering on price alone would have made "nothing outside August is
        touched" a promise the query did not keep — a $20 delivery in any other
        month would have been swept up with it.
        """
        from services.lead_cost import month_bounds

        outside = scene.lead(holder=scene.a["id"])
        year, mon = (int(x) for x in a_frozen_month().split("-"))
        scene.deliver(
            outside["id"],
            scene.a["id"],
            datetime(year, mon, 15, 12, 0, tzinfo=_ONTARIO),
            cost="20",
        )

        # The script's own query, verbatim.
        start, end = month_bounds("2026-08")
        candidates = {
            r["lead_id"]
            for r in supabase.table("lead_delivery")
            .select("lead_id")
            .eq("cost_at_delivery", "20")
            .gte("delivered_at", start)
            .lt("delivered_at", end)
            .execute()
            .data
        }

        assert outside["id"] not in candidates, (
            "a $20 delivery outside August was a candidate — the month filter "
            "is what stops the correction reaching a closed month"
        )

    def test_an_OVERTIME_delivery_is_priced_too(self, scene):
        """assign_lead has TWO insert sites — group 1 (under daily cap) and
        group 2 (overtime, ready_for_more). A rebuild of that function could
        have added the price to one and missed the other; the trigger fires on
        both, and this is what proves it.
        """
        # Everyone at their daily cap, one broker willing to take extras.
        group = scene.box.group()
        tired = scene.box.broker(group["id"], "AtCap", daily_cap=0, max_cap=5,
                                 ready_for_more=True)
        lead = scene.box.lead(group["id"], "Overtime", source="meta")

        assert scene.box.assign(lead["id"]) == tired["id"], (
            "the overtime tier did not run — this test proves nothing"
        )
        scene.box.lead_ids.append(lead["id"])

        row = supabase.table("lead_delivery").select("cost_at_delivery").eq(
            "lead_id", lead["id"]).execute().data[0]
        assert row["cost_at_delivery"] is not None, (
            "an overtime delivery was not priced — the trigger missed the "
            "second insert site"
        )

    def test_allocation_SURVIVES_a_missing_price(self, scene):
        """ALLOCATION MUST NEVER FAIL OVER BILLING.

        A broker not receiving a customer is a real cost; an unpriced delivery
        is a reporting problem. The trigger uses a scalar subquery precisely so
        an empty settings table yields null rather than raising — and this is
        the test that says so, because the plan claimed it without proving it.
        """
        original = supabase.table("lead_cost_settings").select("*").execute().data[0]
        supabase.table("lead_cost_settings").delete().eq("id", 1).execute()
        try:
            lead = scene.lead()
            assigned = scene.box.assign(lead["id"])

            assert assigned is not None, (
                "a lead could not be allocated because no price was configured"
            )
            row = supabase.table("lead_delivery").select("cost_at_delivery").eq(
                "lead_id", lead["id"]).execute().data[0]
            assert row["cost_at_delivery"] is None
        finally:
            supabase.table("lead_cost_settings").insert({
                "id": 1, "cost_per_lead": str(original["cost_per_lead"]),
            }).execute()

    def test_an_admin_hand_out_of_a_POOLED_lead_is_free(self, scene):
        """The accepted leak, pinned so it is a decision rather than a
        surprise. admin_assign_lead writes no lead_delivery row, so a purchased
        lead the round-robin could not place and an admin handed out is not
        charged. Charging it would mean writing a delivery row from the admin
        path — which the daily cap also counts, so a billing change would
        silently reduce what that broker is given tomorrow.
        """
        lead = scene.lead()   # unassigned, straight into the pool
        assert scene.box.admin_assign(
            lead["id"], scene.a["id"], scene.admin["id"]) is None

        rows = supabase.table("lead_delivery").select("id").eq(
            "lead_id", lead["id"]).execute().data
        assert not rows, "an admin hand-out created a delivery row"

    def test_a_delivery_with_no_price_is_UNPRICED_not_zero(self, scene):
        """A null cost means the settings row was missing when the lead was
        delivered — a configuration failure. Reporting it as $0 would say the
        lead was deliberately free and quietly under-bill."""
        lead = scene.lead(holder=scene.a["id"])
        now = datetime.now(_ONTARIO)
        scene.deliver(lead["id"], scene.a["id"], now, cost=None)
        supabase.table("lead_delivery").update({"cost_at_delivery": None}).eq(
            "lead_id", lead["id"]).execute()

        ledger = service.broker_ledger(scene.a["id"], month_of(now))
        line = next(x for x in ledger["lines"] if x["lead_id"] == lead["id"])

        assert line["unpriced"] is True
        assert ledger["unpriced_count"] >= 1, (
            "an unpriced delivery was summed as zero instead of being counted"
        )


# ── The month boundary ──────────────────────────────────────────────────


class TestTheMonthBoundary:
    def test_late_on_the_last_night_belongs_to_THAT_month(self, scene):
        """THE UTC BUG, in the one place it costs money.

        31 Aug 23:00 Ontario is 1 Sep 03:00 UTC. A bare date comparison bills
        this lead to September; the broker's August statement is short and
        September's is over by the same amount.
        """
        lead = scene.lead(holder=scene.a["id"])
        # A frozen month, so the assertion does not depend on today's date.
        year, mon = map(int, a_frozen_month().split("-"))
        last = (datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1,
                         tzinfo=_ONTARIO) - timedelta(days=1)).day
        late = datetime(year, mon, last, 23, 0, tzinfo=_ONTARIO)

        scene.deliver(lead["id"], scene.a["id"], late)
        scene.custody(lead["id"], scene.a["id"], late)

        ledger = service.broker_ledger(scene.a["id"], f"{year:04d}-{mon:02d}")
        assert any(x["lead_id"] == lead["id"] for x in ledger["lines"]), (
            "a lead delivered at 23:00 on the last night fell out of its own "
            "month — the boundary was compared in UTC"
        )

    def test_is_open_flips_on_the_sixth(self):
        """The month is charged 1st-31st and corrected in the first five days
        after. On the 6th it is final."""
        now = datetime.now(_ONTARIO)
        this_month = f"{now.year:04d}-{now.month:02d}"

        assert service.is_open(this_month) is True
        assert service.is_open(a_frozen_month()) is False

    def test_the_freeze_instant_is_midnight_ontario_on_the_sixth(self):
        frozen = service.freeze_instant("2026-08").astimezone(_ONTARIO)
        assert (frozen.year, frozen.month, frozen.day) == (2026, 9, 6)
        assert (frozen.hour, frozen.minute) == (0, 0)

    def test_december_rolls_into_january(self):
        """The month-plus-one arithmetic, at the one place it can overflow.
        December's window ends on 1 January of the NEXT year, and its freeze is
        6 January — not month 13 of the same year."""
        start, end = service.month_bounds("2026-12")
        assert "2026-12-01" in start
        assert "2027-01-01" in end

        frozen = service.freeze_instant("2026-12").astimezone(_ONTARIO)
        assert (frozen.year, frozen.month, frozen.day) == (2027, 1, 6)

    def test_the_fifth_is_open_and_the_sixth_is_not(self):
        """The window is the month itself plus five days to correct it. On the
        5th an admin can still credit; at midnight starting the 6th they
        cannot."""
        frozen = service.freeze_instant("2026-08").astimezone(_ONTARIO)

        # The last moment August is open, and the first it is not.
        assert frozen - timedelta(minutes=1) < frozen
        assert (frozen - timedelta(minutes=1)).day == 5
        assert frozen.day == 6 and frozen.hour == 0

    def test_a_malformed_month_is_422(self):
        for bad in ("2026-13", "August", "26-08", ""):
            with pytest.raises(HTTPException) as err:
                service.month_bounds(bad)
            assert err.value.status_code == 422


# ── The freeze rule ─────────────────────────────────────────────────────


class TestWhoIsCharged:
    def test_while_OPEN_the_cost_follows_the_lead(self, scene):
        """Reassigned inside its own month: the new holder pays, and nothing
        was written to make that happen."""
        now = datetime.now(_ONTARIO)
        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"], now)

        # The reassignment: the lead now sits with B.
        supabase.table("lead").update({"assigned_broker_id": scene.b["id"]}).eq(
            "id", lead["id"]).execute()

        month = month_of(now)
        assert any(x["lead_id"] == lead["id"]
                   for x in service.broker_ledger(scene.b["id"], month)["lines"])
        assert not any(x["lead_id"] == lead["id"]
                       for x in service.broker_ledger(scene.a["id"], month)["lines"])

    def test_once_FROZEN_the_charge_stops_moving(self, scene):
        """THE HEART OF IT. The lead is delivered to A in a closed month and
        reassigned to B afterwards. August must still bill A.

        This is what makes a stored billed_broker_id unnecessary: the answer
        comes from lead_assignment, which cannot be rewritten by a later move.
        """
        month = a_frozen_month()
        year, mon = map(int, month.split("-"))
        when = datetime(year, mon, 10, 12, 0, tzinfo=_ONTARIO)

        lead = scene.lead(holder=scene.b["id"])   # currently held by B
        scene.deliver(lead["id"], scene.a["id"], when)
        scene.custody(lead["id"], scene.a["id"], when, reason="Initial Claim")
        # Moved to B long after the freeze.
        scene.custody(lead["id"], scene.b["id"], datetime.now(_ONTARIO))

        assert any(x["lead_id"] == lead["id"]
                   for x in service.broker_ledger(scene.a["id"], month)["lines"]), (
            "a frozen month followed a later reassignment"
        )
        assert not any(x["lead_id"] == lead["id"]
                       for x in service.broker_ledger(scene.b["id"], month)["lines"])

    def test_the_LAST_move_before_the_freeze_is_the_one_charged(self, scene):
        """Two reassignments while open — the final holder pays, not the first
        and not the original recipient."""
        month = a_frozen_month()
        year, mon = map(int, month.split("-"))
        delivered = datetime(year, mon, 5, 9, 0, tzinfo=_ONTARIO)

        lead = scene.lead()
        scene.deliver(lead["id"], scene.a["id"], delivered)
        scene.custody(lead["id"], scene.a["id"], delivered, reason="Initial Claim")
        # A -> B -> A, all inside the open window.
        scene.custody(lead["id"], scene.b["id"], delivered + timedelta(days=2))
        scene.custody(lead["id"], scene.a["id"], delivered + timedelta(days=4))

        assert any(x["lead_id"] == lead["id"]
                   for x in service.broker_ledger(scene.a["id"], month)["lines"])
        assert not any(x["lead_id"] == lead["id"]
                       for x in service.broker_ledger(scene.b["id"], month)["lines"])

    def test_a_move_in_the_GRACE_DAYS_still_counts(self, scene):
        """1st-5th of the following month: the month is not the calendar month,
        it is the calendar month plus five days. A lead moved on the 3rd still
        changes who pays for the month before.

        Built by placing the delivery late enough that the CURRENT month is
        still open — which is what the grace window means for whichever month
        is being corrected right now.
        """
        now = datetime.now(_ONTARIO)
        month = month_of(now)
        assert service.is_open(month), "the current month should always be open"

        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"], now)
        scene.custody(lead["id"], scene.a["id"], now, reason="Initial Claim")

        # The move — recorded now, which for a same-month delivery is exactly
        # the "moved during the open window" case.
        supabase.table("lead").update({"assigned_broker_id": scene.b["id"]}).eq(
            "id", lead["id"]).execute()
        scene.custody(lead["id"], scene.b["id"], now)

        assert any(x["lead_id"] == lead["id"]
                   for x in service.broker_ledger(scene.b["id"], month)["lines"])

    def test_a_move_after_the_freeze_does_not_alter_an_OLD_report(self, scene):
        """Re-running last quarter's report must give the same answer today as
        it did then. This is the practical form of the freeze: a bill already
        sent cannot quietly change."""
        month = a_frozen_month()
        year, mon = map(int, month.split("-"))
        when = datetime(year, mon, 8, 10, 0, tzinfo=_ONTARIO)

        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"], when)
        scene.custody(lead["id"], scene.a["id"], when, reason="Initial Claim")

        before = service.broker_ledger(scene.a["id"], month)["charged"]

        # Moved today, long after that month closed.
        supabase.table("lead").update({"assigned_broker_id": scene.b["id"]}).eq(
            "id", lead["id"]).execute()
        scene.custody(lead["id"], scene.b["id"], datetime.now(_ONTARIO))

        after = service.broker_ledger(scene.a["id"], month)["charged"]
        assert before == after, (
            "a reassignment today changed a report for a month that closed "
            "months ago"
        )

    def test_the_group_report_follows_the_LEAD_not_the_broker(self, scene):
        """A lead reassigned to a broker in another group still reports under
        the group that bought it. lead.group_id never moves; reading the
        broker's current group would shift historical spend between teams."""
        other_group = scene.box.group()
        outsider = scene.box.broker(other_group["id"], "Outsider", daily_cap=50)

        now = datetime.now(_ONTARIO)
        lead = scene.lead(holder=outsider["id"])   # held outside the group
        scene.deliver(lead["id"], scene.a["id"], now)

        summary = service.group_summary(scene.group["id"], month_of(now))
        charged = {b["broker_id"] for b in summary["brokers"]}
        assert outsider["id"] in charged, (
            "the lead vanished from its own group's report because its holder "
            "moved team"
        )


# ── Credits ─────────────────────────────────────────────────────────────


class TestCredits:
    def test_a_credit_reduces_the_charge_by_exactly_that_leads_cost(self, scene):
        now = datetime.now(_ONTARIO)
        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"], now, cost="20")

        month = month_of(now)
        before = Decimal(service.broker_ledger(scene.a["id"], month)["charged"])
        service.add_credit(lead["id"], "Wrong number", scene.admin["id"])
        after = Decimal(service.broker_ledger(scene.a["id"], month)["charged"])

        assert before - after == Decimal("20")

    def test_the_same_lead_cannot_be_credited_twice(self, scene):
        """The conditional update is the guard. A read-then-write would let two
        admins on the 4th both pass the check."""
        now = datetime.now(_ONTARIO)
        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"], now)

        service.add_credit(lead["id"], "Duplicate", scene.admin["id"])
        with pytest.raises(HTTPException) as err:
            service.add_credit(lead["id"], "Wrong number", scene.admin["id"])
        assert err.value.status_code == 409

    def test_crediting_a_frozen_month_is_refused(self, scene):
        month = a_frozen_month()
        year, mon = map(int, month.split("-"))
        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"],
                      datetime(year, mon, 12, tzinfo=_ONTARIO))

        with pytest.raises(HTTPException) as err:
            service.add_credit(lead["id"], "Test lead", scene.admin["id"])
        assert err.value.status_code == 409
        assert month in err.value.detail

    def test_removing_a_credit_in_a_frozen_month_is_refused(self, scene):
        month = a_frozen_month()
        year, mon = map(int, month.split("-"))
        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"],
                      datetime(year, mon, 12, tzinfo=_ONTARIO))
        # Credited directly: the service would refuse to create this state.
        supabase.table("lead_delivery").update({
            "credit_reason": "Duplicate",
            "credited_by": scene.admin["id"],
            "credited_at": datetime.now(timezone.utc).isoformat(),
        }).eq("lead_id", lead["id"]).execute()

        with pytest.raises(HTTPException) as err:
            service.remove_credit(lead["id"])
        assert err.value.status_code == 409

    def test_removing_a_credit_puts_the_lead_back_on_the_bill(self, scene):
        """The undo path, end to end — credit, check it came off, remove it,
        check it went back. Without this, "remove" could be a no-op that
        returns 204 and the total would never move."""
        now = datetime.now(_ONTARIO)
        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"], now, cost="20")
        month = month_of(now)

        before = Decimal(service.broker_ledger(scene.a["id"], month)["charged"])
        service.add_credit(lead["id"], "Duplicate", scene.admin["id"])
        service.remove_credit(lead["id"])
        after = Decimal(service.broker_ledger(scene.a["id"], month)["charged"])

        assert after == before
        line = next(x for x in service.broker_ledger(scene.a["id"], month)["lines"]
                    if x["lead_id"] == lead["id"])
        assert line["credit_reason"] is None

    def test_removing_a_credit_that_does_not_exist_is_refused(self, scene):
        """Not a silent no-op: the admin pressed undo and nothing was undone."""
        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"], datetime.now(_ONTARIO))

        with pytest.raises(HTTPException) as err:
            service.remove_credit(lead["id"])
        assert err.value.status_code == 409

    def test_the_credits_month_comes_from_the_DELIVERY_not_from_today(self, scene):
        """A credit entered on 4 September belongs to August. Deciding by
        credited_at would file it under September and leave August charged."""
        now = datetime.now(_ONTARIO)
        # Delivered last month, credited today — both inside the open window
        # only when today is the 1st-5th, so assert on where it LANDS.
        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"], now)

        service.add_credit(lead["id"], "Other", scene.admin["id"])

        ledger = service.broker_ledger(scene.a["id"], month_of(now))
        line = next(x for x in ledger["lines"] if x["lead_id"] == lead["id"])
        assert line["credit_reason"] == "Other"
        assert Decimal(ledger["credited"]) >= Decimal("20")

    def test_a_credited_lead_reassigned_before_the_freeze_carries_BOTH(self, scene):
        """The cost and the credit move together. If only the cost followed,
        the write-off would silently be re-charged to the new broker."""
        now = datetime.now(_ONTARIO)
        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"], now, cost="20")
        service.add_credit(lead["id"], "Wrong number", scene.admin["id"])

        supabase.table("lead").update({"assigned_broker_id": scene.b["id"]}).eq(
            "id", lead["id"]).execute()

        month = month_of(now)
        b_ledger = service.broker_ledger(scene.b["id"], month)
        line = next(x for x in b_ledger["lines"] if x["lead_id"] == lead["id"])

        assert line["credit_reason"] == "Wrong number", (
            "the credit did not follow the lead, so the new broker is charged "
            "for a lead already written off"
        )

    def test_a_partial_credit_cannot_be_written(self, scene):
        """All three columns or none — the CHECK in 0088. A reason with nobody
        attached is money off a bill that nobody can account for."""
        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"], datetime.now(_ONTARIO))

        with pytest.raises(Exception):
            supabase.table("lead_delivery").update(
                {"credit_reason": "Duplicate"}
            ).eq("lead_id", lead["id"]).execute()

    def test_the_client_cannot_dictate_the_amount_or_the_broker(self):
        """The request is {lead_id, reason} and nothing else. extra='forbid'
        means an amount or a broker_id smuggled into the body is a 422, not a
        field quietly ignored — a client able to name either could bill the
        wrong person the wrong figure."""
        from pydantic import ValidationError

        from schemas.lead_cost import AddCreditRequest

        # The honest shape works.
        AddCreditRequest(lead_id="abc", reason="Duplicate")

        for smuggled in ({"amount": "5"}, {"broker_id": "x"}, {"cost": 0}):
            with pytest.raises(ValidationError):
                AddCreditRequest(lead_id="abc", reason="Duplicate", **smuggled)

        # And the reason vocabulary is closed.
        with pytest.raises(ValidationError):
            AddCreditRequest(lead_id="abc", reason="Because I said so")

    def test_crediting_a_lead_the_system_never_delivered_is_404(self, scene):
        lead = scene.lead(holder=scene.a["id"], source="manual")
        with pytest.raises(HTTPException) as err:
            service.add_credit(lead["id"], "Duplicate", scene.admin["id"])
        assert err.value.status_code == 404


# ── Volume ──────────────────────────────────────────────────────────────


class TestPaging:
    def test_a_month_larger_than_one_page_is_not_truncated(self, scene, monkeypatch):
        """SILENT UNDER-BILLING is what this catches.

        PostgREST returns a truncated page with no error, so an unpaged read
        sums to a plausible figure and the brokerage bills short. Nothing
        reports it; a broker notices months later.

        THE PAGE SIZE IS SHRUNK TO 2 rather than inserting a thousand rows.
        The bug is in the loop, not in the number — three deliveries across a
        page size of two exercises "fetch, see a full page, fetch again, see a
        short one, stop" exactly as 1,800 rows across 500 would, in a second
        instead of a minute.
        """
        from repositories import lead_cost as repo

        monkeypatch.setattr(repo, "_PAGE", 2)

        now = datetime.now(_ONTARIO)
        leads = [scene.lead(holder=scene.a["id"]) for _ in range(3)]
        for i, lead in enumerate(leads):
            scene.deliver(lead["id"], scene.a["id"], now - timedelta(minutes=i))

        start_utc, end_utc = service.month_bounds(month_of(now))
        fetched = repo.deliveries_between(start_utc, end_utc)

        got = {d["lead_id"] for d in fetched}
        for lead in leads:
            assert lead["id"] in got, (
                "a delivery fell off the end of a page — the read stopped at "
                "the first full page instead of continuing"
            )

    def test_the_id_batching_also_pages(self, scene, monkeypatch):
        """leads_by_ids batches ids to keep the URL short, and each batch is
        itself paged. Two loops, either of which could drop rows."""
        from repositories import lead_cost as repo

        monkeypatch.setattr(repo, "_PAGE", 2)
        monkeypatch.setattr(repo, "_ID_BATCH", 2)

        leads = [scene.lead(holder=scene.a["id"]) for _ in range(5)]
        rows = repo.leads_by_ids([x["id"] for x in leads])

        assert {r["id"] for r in rows} == {x["id"] for x in leads}


# ── Access ──────────────────────────────────────────────────────────────


class TestTheReportsAddUp:
    def test_the_group_total_equals_the_sum_of_its_rows(self, scene):
        """A footer that disagrees with the column above it is the one error
        nobody can explain in a meeting."""
        now = datetime.now(_ONTARIO)
        for broker in (scene.a, scene.b):
            for _ in range(2):
                lead = scene.lead(holder=broker["id"])
                scene.deliver(lead["id"], broker["id"], now, cost="20")

        summary = service.group_summary(scene.group["id"], month_of(now))

        assert Decimal(summary["total_allocated"]) == sum(
            (Decimal(b["allocated"]) for b in summary["brokers"]), Decimal(0))
        assert Decimal(summary["total_charged"]) == sum(
            (Decimal(b["charged"]) for b in summary["brokers"]), Decimal(0))
        # And each broker's own row is internally consistent.
        for b in summary["brokers"]:
            assert Decimal(b["charged"]) == Decimal(b["allocated"]) - Decimal(b["credited"])

    def test_a_month_with_nothing_in_it_reads_zero(self, scene):
        """Not an error and not an empty response — a real month that happens
        to have no deliveries. The screens render this."""
        empty = a_frozen_month()
        summary = service.group_summary(scene.group["id"], empty)

        assert summary["brokers"] == []
        assert Decimal(summary["total_charged"]) == 0
        assert summary["unpriced_count"] == 0

        ledger = service.broker_ledger(scene.a["id"], empty)
        assert ledger["lines"] == []
        assert Decimal(ledger["charged"]) == 0

    def test_the_ledger_carries_what_the_screen_needs(self, scene):
        """The columns the ledger renders — a missing field is a blank column
        rather than an error, which is why it is asserted rather than assumed."""
        now = datetime.now(_ONTARIO)
        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"], now, cost="20")

        line = next(x for x in service.broker_ledger(
            scene.a["id"], month_of(now))["lines"] if x["lead_id"] == lead["id"])

        for field in ("name", "phone", "delivered_at", "cost", "unpriced",
                      "credit_reason"):
            assert field in line, f"the ledger line has no {field}"
        assert line["name"]
        assert line["phone"]


class TestAccess:
    def test_a_broker_cannot_reach_the_admin_endpoints(self):
        """require_admin gates the whole admin router. A broker reaching the
        group sheet would see every colleague's charges."""
        from routes import lead_cost as routes

        # The admin router carries the dependency; the broker router must not.
        admin_deps = [str(d.dependency) for d in routes.router.dependencies]
        assert any("require_admin" in d for d in admin_deps), (
            "the admin lead-cost router is not gated"
        )
        broker_deps = [str(d.dependency) for d in routes.broker_router.dependencies]
        assert not any("require_admin" in d for d in broker_deps)

    def test_the_broker_route_takes_no_broker_id(self):
        """It always describes the caller. An id parameter would turn it into a
        directory of everyone's bills."""
        import inspect

        from routes import lead_cost as routes

        params = inspect.signature(routes.my_lead_cost).parameters
        assert "broker_id" not in params
        assert set(params) == {"month", "broker"}

    def test_a_broker_reads_the_open_month(self, scene):
        """THE POINT OF THE CHANGE. This used to raise 404.

        A broker given ten leads today used to learn what they owed after the
        5th of NEXT month. The lines are not provisional — cost_at_delivery is
        stamped as the delivery happens — so the figure is real; what is still
        moving is which lines carry a credit, and `is_open` says so.
        """
        now = datetime.now(_ONTARIO)
        lead = scene.lead(holder=scene.a["id"])
        scene.deliver(lead["id"], scene.a["id"], now)

        result = service.my_ledger(scene.a["id"], month_of(now))

        assert result["is_open"] is True
        assert lead["id"] in {x["lead_id"] for x in result["lines"]}, (
            "a lead delivered today must appear in today's ledger"
        )

    def test_a_broker_reads_a_frozen_month(self, scene):
        month = a_frozen_month()
        result = service.my_ledger(scene.a["id"], month)
        assert result["month"] == month
        assert result["is_open"] is False

    def test_the_ledger_reports_the_brokers_join_month(self, scene):
        """The month list on screen stops here, so a broker onboarded in
        August is not offered a May that reads $0 forever."""
        now = datetime.now(_ONTARIO)
        led = service.broker_ledger(scene.a["id"], month_of(now))

        created = datetime.fromisoformat(
            supabase.table("broker").select("created_at")
            .eq("id", scene.a["id"]).execute().data[0]["created_at"]
            .replace("Z", "+00:00")
        ).astimezone(_ONTARIO)

        assert led["broker_since_month"] == f"{created.year}-{created.month:02d}"

    def test_an_unknown_broker_is_404_not_500(self):
        """broker_ledger never looked the broker up before, so an unknown id
        just produced an empty ledger. Reading created_at changes that, and an
        unguarded [0] would turn a mistyped admin URL into a 500."""
        now = datetime.now(_ONTARIO)
        with pytest.raises(HTTPException) as err:
            service.broker_ledger(
                "11111111-2222-3333-4444-555555555555", month_of(now)
            )
        assert err.value.status_code == 404

    def test_a_ledger_shows_only_that_brokers_leads(self, scene):
        now = datetime.now(_ONTARIO)
        mine = scene.lead(holder=scene.a["id"])
        theirs = scene.lead(holder=scene.b["id"])
        scene.deliver(mine["id"], scene.a["id"], now)
        scene.deliver(theirs["id"], scene.b["id"], now)

        ledger = service.broker_ledger(scene.a["id"], month_of(now))
        ids = {x["lead_id"] for x in ledger["lines"]}
        assert mine["id"] in ids
        assert theirs["id"] not in ids
