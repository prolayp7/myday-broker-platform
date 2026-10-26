"""My Day's premium and commission cards, and the month picker behind them.

services/commission.py::broker_commission turns issued policies into what the
broker earned; services/broker.py::get_my_day picks the month and returns both
figures. No database: the reads are replaced, what is tested is what is made
of them.
"""

import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi import HTTPException

# Credentials before the import: services reach db.py, which reads
# SUPABASE_URL at module level. Nothing below talks to the database.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")):
    pytest.skip("needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY (no call is made)",
                allow_module_level=True)

from services import broker as broker_service  # noqa: E402
from services import commission  # noqa: E402

RATES = {"auto_rate_pct": "12.5", "property_rate_pct": "20", "broker_share_pct": "55"}


def policy(auto=None, home=None, tenant=None):
    """One premium_history row as the repository returns it. The total is the
    database's generated column, mirrored here."""
    total = sum(Decimal(str(v)) for v in (auto, home, tenant) if v is not None)
    return {
        "new_auto_premium_annual": None if auto is None else str(auto),
        "new_homeowner_premium_annual": None if home is None else str(home),
        "new_tenant_premium_annual": None if tenant is None else str(tenant),
        "total_new_premium_annual": str(total),
    }


class TestBrokerCommission:
    def test_auto_is_12_5_percent_then_the_brokers_55(self):
        # $2,400 x 12.5% = $300 commission; 55% of it is $165.
        assert commission.broker_commission([policy(auto=2400)], RATES) == Decimal("165")

    def test_home_condo_and_rental_are_20_percent(self):
        # Condo and rentals sit in the homeowner column. $1,200 x 20% = $240; 55% = $132.
        assert commission.broker_commission([policy(home=1200)], RATES) == Decimal("132")

    def test_tenant_is_20_percent_too(self):
        # $500 x 20% = $100; 55% = $55.
        assert commission.broker_commission([policy(tenant=500)], RATES) == Decimal("55")

    def test_a_bundle_adds_both_products(self):
        # $300 + $240 = $540 commission; 55% = $297.
        assert commission.broker_commission([policy(auto=2400, home=1200)], RATES) == Decimal("297")

    def test_several_policies_add_up(self):
        rows = [policy(auto=2400), policy(home=1200), policy(auto=1600, tenant=400)]
        # 300 + 240 + (200 + 80) = 820 commission; 55% = 451.
        assert commission.broker_commission(rows, RATES) == Decimal("451")

    def test_no_policies_is_zero(self):
        assert commission.broker_commission([], RATES) == Decimal("0")

    def test_the_share_setting_is_what_is_applied(self):
        rates = {**RATES, "broker_share_pct": "60"}
        # 60% of $300 = $180.
        assert commission.broker_commission([policy(auto=2400)], rates) == Decimal("180")

    def test_a_policy_missing_a_product_counts_it_as_zero(self):
        assert commission.broker_commission([policy()], RATES) == Decimal("0")


@pytest.fixture
def my_day(monkeypatch):
    """get_my_day with every read replaced. Returns (run, asked): asked records
    the date range the premium read was given."""
    asked = {}
    issued = []

    class _R:
        def __init__(self, data=None, count=None):
            self.data, self.count = data, count

    monkeypatch.setattr(broker_service, "_safe_broker_or_404", lambda _id: {"name": "Anita Rao"})
    monkeypatch.setattr(broker_service.lead_repo, "broker_stage_counts", lambda _id: _R({}))
    monkeypatch.setattr(broker_service.lead_repo, "count_assigned_since", lambda _id, _since: _R(count=0))
    monkeypatch.setattr(broker_service, "_count_stale", lambda _id, _now: 0)
    monkeypatch.setattr(broker_service.commission_service, "get_rates", lambda: RATES)

    def _issued(broker_id, start_date, end_date):
        asked.update(start=start_date, end=end_date)
        return _R(list(issued))

    monkeypatch.setattr(broker_service.premium_repo, "issued_between_for_broker", _issued)

    def run(month=None, rows=()):
        issued[:] = rows
        return broker_service.get_my_day("broker-1", month)

    return run, asked


class TestMyDayMonth:
    def test_both_cards_come_from_the_same_policies(self, my_day):
        run, _ = my_day
        out = run("2026-09", [policy(auto=2400), policy(home=1200)])
        p = out["premium"]
        assert p["month"] == "2026-09"
        assert Decimal(p["written_this_month"]) == Decimal("3600")
        assert p["policy_count"] == 2
        # $165 on the auto policy + $132 on the home one.
        assert Decimal(p["commission_earned"]) == Decimal("297")

    def test_the_month_is_its_calendar_dates_end_exclusive(self, my_day):
        run, asked = my_day
        run("2026-09")
        assert asked == {"start": "2026-09-01", "end": "2026-10-01"}

    def test_december_ends_on_the_first_of_january(self, my_day):
        run, asked = my_day
        run("2025-12")
        assert asked == {"start": "2025-12-01", "end": "2026-01-01"}

    def test_no_month_means_the_current_ontario_month(self, my_day):
        run, asked = my_day
        out = run()
        now = datetime.now(broker_service._ONTARIO_TZ)
        assert out["premium"]["month"] == f"{now.year:04d}-{now.month:02d}"
        assert asked["start"] == f"{now.year:04d}-{now.month:02d}-01"

    def test_a_future_month_is_refused(self, my_day):
        run, _ = my_day
        now = datetime.now(broker_service._ONTARIO_TZ)
        future = f"{now.year + 1:04d}-{now.month:02d}"
        with pytest.raises(HTTPException) as err:
            run(future)
        assert err.value.status_code == 422

    @pytest.mark.parametrize("bad", ["2026-13", "2026-00", "1999-05", "abcd-ef"])
    def test_a_malformed_month_is_refused(self, my_day, bad):
        run, _ = my_day
        with pytest.raises(HTTPException) as err:
            run(bad)
        assert err.value.status_code == 422

    def test_an_empty_month_is_zero_on_both_cards(self, my_day):
        run, _ = my_day
        out = run("2026-08", [])
        assert out["premium"]["written_this_month"] == "0"
        assert out["premium"]["policy_count"] == 0
        assert Decimal(out["premium"]["commission_earned"]) == 0


class TestAMissingShareIsNotZero:
    def test_broker_commission_refuses_a_missing_share(self):
        with pytest.raises(ValueError):
            commission.broker_commission([policy(auto=2400)], {"auto_rate_pct": "12.5", "property_rate_pct": "20"})

    def test_my_day_shows_no_figure_rather_than_zero(self, my_day, monkeypatch):
        run, _ = my_day
        monkeypatch.setattr(broker_service.commission_service, "get_rates",
                            lambda: {"auto_rate_pct": "12.5", "property_rate_pct": "20"})
        out = run("2026-09", [policy(auto=2400)])
        assert out["premium"]["commission_earned"] is None
        # The premium card is unaffected.
        assert Decimal(out["premium"]["written_this_month"]) == Decimal("2400")


class TestTheSettingsReadCarriesEveryField:
    """The live test on staging found the share missing from the settings
    SELECT, so every read saw no share. Pinned here: whatever the screen and
    My Day need must be in the columns the repository asks for."""

    def test_the_select_names_all_three_figures(self):
        from repositories import commission_settings
        cols = {c.strip() for c in commission_settings._SETTINGS_SELECT.split(",")}
        assert {"auto_rate_pct", "property_rate_pct", "broker_share_pct"} <= cols

    def test_the_response_schema_matches(self):
        from schemas.commission import CommissionRatesResponse
        assert {"auto_rate_pct", "property_rate_pct", "broker_share_pct"} <= set(CommissionRatesResponse.model_fields)
