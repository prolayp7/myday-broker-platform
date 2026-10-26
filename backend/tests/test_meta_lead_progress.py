"""What the admin Meta Conversions screen is told — services/meta_signal.py's
lead_progress (the list) and the stuck-sender count in health (its warning).

WHY THIS FILE EXISTS. The admin screen shows one mark per milestone per lead,
folded from up to three signals. The folding is where it can go wrong in a way
nobody would notice: a lead shown "waiting" for ever in an environment with no
dataset configured, or a warning about a stopped sender when there is simply
nowhere to send.

No database. The reads are mocked; what is tested is what is made of them.
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Credentials before the import: services.meta_signal reaches db.py, which
# reads SUPABASE_URL at module level. Nothing below talks to the database.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")):
    pytest.skip(
        "needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY — services import db.py "
        "(no call is made)",
        allow_module_level=True,
    )

from services import meta_signal  # noqa: E402


def sig(status, event="Lead", **extra):
    return {"id": event, "event_name": event, "status": status,
            "dataset_target": "legacy", **extra}


class TestOneMilestone:

    def test_nothing_queued_has_not_happened(self):
        assert meta_signal._milestone_state([], True) == "none"

    def test_sent_is_sent(self):
        assert meta_signal._milestone_state([sig("Sent")], True) == "sent"

    def test_still_to_go_is_waiting(self):
        assert meta_signal._milestone_state([sig("Pending")], True) == "waiting"

    def test_a_refusal_is_failed(self):
        assert meta_signal._milestone_state([sig("Failed")], True) == "failed"

    def test_nowhere_to_send_is_not_waiting(self):
        # With no dataset configured a row stays Pending by design — for ever.
        assert meta_signal._milestone_state([sig("Pending")], False) == "not_set_up"


class _Result:
    def __init__(self, data):
        self.data = data


class _Count:
    def __init__(self, count):
        self.count = count


def lead(signals, **cols):
    return {"id": "L1", "name": "Priya Sharma", "stage": "New",
            "created_at": "2026-09-29T14:00:00+00:00", "broker": {"name": "Anita"},
            "meta_signal": signals, **cols}


@pytest.fixture
def progress(monkeypatch):
    monkeypatch.setenv("META_CAPI_DATASET_ID", "123")
    # DEFAULT-MOCK the new count read too (Meta Quality screen's 7-day learning
    # count), or every existing test here would open a real database call via
    # the new path. Each test needing a real figure overrides.
    monkeypatch.setattr(
        meta_signal.repo, "count_sent_since",
        lambda event_name, since_iso: _Count(0),
    )

    def run(rows):
        monkeypatch.setattr(meta_signal.lead_repo, "list_meta_with_signals", lambda since: _Result(rows))
        return meta_signal.lead_progress(30)["leads"]
    return run


class TestOneLead:

    def test_a_new_lead_shows_its_broker_arrival_and_first_milestone(self, progress):
        [row] = progress([lead([sig("Sent")])])
        assert row["name"] == "Priya Sharma" and row["broker_name"] == "Anita"
        assert row["arrived_at"] == "2026-09-29T14:00:00+00:00"
        assert row["milestones"] == {"Lead": "sent", "CompleteRegistration": "none", "Purchase": "none"}
        assert row["status"] == "sent" and row["failures"] == []

    def test_the_worst_milestone_is_the_leads_status(self, progress):
        [row] = progress([lead([
            sig("Sent"),
            sig("Pending", event="CompleteRegistration"),
        ])])
        assert row["status"] == "waiting"

        [row] = progress([lead([
            sig("Pending"),
            sig("Failed", event="CompleteRegistration", error_code=100, error_message="too old"),
        ])])
        assert row["status"] == "failed"
        assert row["failures"] == [{
            "id": "CompleteRegistration", "event_name": "CompleteRegistration",
            "dataset_target": "legacy", "error_code": 100, "error_message": "too old",
        }]

    def test_an_unassigned_lead_has_no_broker_name(self, progress):
        [row] = progress([lead([sig("Sent")], broker=None)])
        assert row["broker_name"] is None

    def test_a_lead_with_no_signals_at_all_is_still_listed(self, progress):
        # The lead an admin most needs to see: something went wrong before
        # anything was even queued.
        [row] = progress([lead([])])
        assert row["status"] == "none"
        assert set(row["milestones"].values()) == {"none"}

    def test_a_sale_waiting_to_send_is_waiting(self, progress):
        [row] = progress([lead([
            sig("Sent"),
            sig("Sent", event="CompleteRegistration"),
            sig("Pending", event="Purchase"),
        ], stage="Sold")])
        assert row["milestones"]["Purchase"] == "waiting"
        assert row["status"] == "waiting"


class TestRetiredDatasetRows:
    """Rows from the five-dataset setup (before 0133) are history."""

    def test_an_old_failure_does_not_mark_the_lead_failed(self, progress):
        [row] = progress([lead([
            sig("Sent"),
            sig("Failed", dataset_target="preferred", error_code=100, error_message="old"),
        ])])
        assert row["status"] == "sent"
        assert row["failures"] == []

    def test_an_old_row_that_reached_meta_still_counts(self, progress):
        # Production's August 'common' rows did reach Meta.
        [row] = progress([lead([sig("Sent", dataset_target="common")])])
        assert row["milestones"]["Lead"] == "sent"


@pytest.fixture
def stuck_reads(monkeypatch):
    """health() with Meta and the database replaced; returns every call made
    to count_pending_before, so a test can say that it was — or was not — asked."""
    monkeypatch.setattr(meta_signal.meta_capi, "check_token", lambda: {"ok": True})
    monkeypatch.setattr(meta_signal.repo, "list_recent", lambda **kwargs: _Result([]))
    calls = []

    def count_pending_before(iso):
        calls.append(iso)
        return _Count(3)

    monkeypatch.setattr(meta_signal.repo, "count_pending_before", count_pending_before)
    return calls


class TestStuckSender:

    def test_old_pending_rows_are_counted_as_stuck(self, monkeypatch, stuck_reads):
        monkeypatch.setenv("META_CAPI_DATASET_ID", "123")
        assert meta_signal.health()["stuck_count"] == 3
        assert len(stuck_reads) == 1

    def test_nothing_is_stuck_and_nothing_is_read_with_no_dataset(self, monkeypatch, stuck_reads):
        monkeypatch.delenv("META_CAPI_DATASET_ID", raising=False)
        assert meta_signal.health()["stuck_count"] == 0
        assert stuck_reads == []


# ── New fields the Meta Quality screen reads (2026-10-25) ────────────────────
#
# These exercise lead_progress's additions: qualified (3-state), broker_id,
# group/community embeds, sale_value from a Sent legacy Purchase, and the
# top-level qualified_sent_7d head count.

def _rich_lead(signals, **cols):
    """Like lead() but shaped like what list_meta_with_signals returns now:
    broker as {id, name} and the broker_group → community embed included."""
    return {
        "id": "L1", "name": "Priya Sharma", "stage": "New",
        "created_at": "2026-10-25T14:00:00+00:00",
        "is_qualified": None,
        "broker": {"id": "B1", "name": "Anita"},
        "broker_group": {"id": "G1", "name": "Toronto A",
                         "community": {"id": "C1", "name": "Dragon"}},
        "meta_signal": signals,
        **cols,
    }


def _progress_one(monkeypatch, lead_row, count=0):
    """Run lead_progress against one lead + a given count. The count fixture
    is replaced per test, since the default returns 0."""
    monkeypatch.setenv("META_CAPI_DATASET_ID", "123")
    monkeypatch.setattr(meta_signal.repo, "count_sent_since",
                        lambda event_name, since_iso: _Count(count))
    monkeypatch.setattr(meta_signal.lead_repo, "list_meta_with_signals",
                        lambda since: _Result([lead_row]))
    return meta_signal.lead_progress(30)


class TestQualifiedIsThreeState:
    def test_true_is_passed_through(self, monkeypatch):
        [row] = _progress_one(monkeypatch, _rich_lead([sig("Sent")], is_qualified=True))["leads"]
        assert row["qualified"] is True

    def test_false_is_preserved_not_null(self, monkeypatch):
        """"Not qualified" and "Not asked yet" must stay distinct — that is the
        whole reason this field is on the row."""
        [row] = _progress_one(monkeypatch, _rich_lead([sig("Sent")], is_qualified=False))["leads"]
        assert row["qualified"] is False

    def test_null_stays_null(self, monkeypatch):
        [row] = _progress_one(monkeypatch, _rich_lead([sig("Sent")]))["leads"]
        assert row["qualified"] is None


class TestBrokerAndRelationships:
    def test_broker_id_and_name_pass_through(self, monkeypatch):
        [row] = _progress_one(monkeypatch, _rich_lead([sig("Sent")]))["leads"]
        assert row["broker_id"] == "B1"
        assert row["broker_name"] == "Anita"

    def test_group_and_community_pass_through(self, monkeypatch):
        [row] = _progress_one(monkeypatch, _rich_lead([sig("Sent")]))["leads"]
        assert row["group"] == {"id": "G1", "name": "Toronto A"}
        assert row["community"] == {"id": "C1", "name": "Dragon"}

    def test_unassigned_lead_has_null_broker_fields(self, monkeypatch):
        [row] = _progress_one(monkeypatch, _rich_lead([sig("Sent")], broker=None))["leads"]
        assert row["broker_id"] is None
        assert row["broker_name"] is None

    def test_a_deleted_group_leaves_both_null(self, monkeypatch):
        # If the lead's group row has been removed since arrival, PostgREST
        # returns null for the embed. The screen should read "no group".
        [row] = _progress_one(monkeypatch, _rich_lead([sig("Sent")], broker_group=None))["leads"]
        assert row["group"] is None
        assert row["community"] is None


class TestSaleValue:
    def _purchase(self, status, dataset_target="legacy", amount="2400"):
        s = sig(status, event="Purchase")
        s["dataset_target"] = dataset_target
        s["payload"] = {"total_new_premium_at_purchase": amount}
        return s

    def test_sent_legacy_purchase_sets_the_amount(self, monkeypatch):
        [row] = _progress_one(
            monkeypatch, _rich_lead([sig("Sent"), self._purchase("Sent")]),
        )["leads"]
        assert row["sale_value"] == 2400.0

    def test_a_pending_purchase_leaves_sale_value_null(self, monkeypatch):
        [row] = _progress_one(
            monkeypatch, _rich_lead([sig("Sent"), self._purchase("Pending")]),
        )["leads"]
        assert row["sale_value"] is None

    def test_a_failed_purchase_leaves_sale_value_null(self, monkeypatch):
        [row] = _progress_one(
            monkeypatch,
            _rich_lead([sig("Sent"), self._purchase("Failed", amount="2400")]),
        )["leads"]
        assert row["sale_value"] is None

    def test_a_sent_common_purchase_does_not_set_the_amount(self, monkeypatch):
        """Historical sales (pre-0133 'common' label) still mark the Sale cell
        as Sent through the milestone — but without an amount, by design."""
        [row] = _progress_one(
            monkeypatch,
            _rich_lead([sig("Sent"), self._purchase("Sent", dataset_target="common")]),
        )["leads"]
        assert row["sale_value"] is None
        assert row["milestones"]["Purchase"] == "sent"

    def test_a_lead_with_no_purchase_at_all_has_null(self, monkeypatch):
        [row] = _progress_one(monkeypatch, _rich_lead([sig("Sent")]))["leads"]
        assert row["sale_value"] is None


class TestQualifiedSent7d:
    def test_the_head_count_is_returned_at_the_top(self, monkeypatch):
        result = _progress_one(monkeypatch, _rich_lead([sig("Sent")]), count=12)
        assert result["qualified_sent_7d"] == 12

    def test_a_zero_count_is_returned_as_zero_not_omitted(self, monkeypatch):
        result = _progress_one(monkeypatch, _rich_lead([sig("Sent")]), count=0)
        assert result["qualified_sent_7d"] == 0

    def test_the_count_reads_from_count_sent_since_with_the_right_event(self, monkeypatch):
        """The service must ask for CompleteRegistration, not Lead or
        Purchase; the repo filter will then enforce legacy + Sent + the date."""
        monkeypatch.setenv("META_CAPI_DATASET_ID", "123")
        asked = {}
        def fake(event_name, since_iso):
            asked["event_name"] = event_name
            asked["since_iso"] = since_iso
            return _Count(5)
        monkeypatch.setattr(meta_signal.repo, "count_sent_since", fake)
        monkeypatch.setattr(meta_signal.lead_repo, "list_meta_with_signals",
                            lambda since: _Result([]))
        meta_signal.lead_progress(30)
        assert asked["event_name"] == "CompleteRegistration"
        # ~7 days ago in ISO; just check the shape so a timezone/day-boundary
        # tweak later does not break the test.
        assert "T" in asked["since_iso"] and asked["since_iso"].endswith("+00:00")


class TestNoHashedDataLeaks:
    """A Purchase payload carries user_data and the stored Meta response. None
    of those should surface in the Meta Quality screen's rows."""

    def test_user_data_and_response_never_appear(self, monkeypatch):
        purchase = sig("Sent", event="Purchase")
        purchase["dataset_target"] = "legacy"
        purchase["payload"] = {
            "total_new_premium_at_purchase": "2400",
            "user_data": {"em": ["hashed-email"], "ph": ["hashed-phone"]},
            "response": {"events_received": 1, "fbtrace_id": "ABC"},
            "dataset_id": "3253348891512104",
        }
        result = _progress_one(monkeypatch, _rich_lead([sig("Sent"), purchase]))
        text = str(result)
        assert "hashed-email" not in text
        assert "hashed-phone" not in text
        assert "fbtrace_id" not in text
        assert "events_received" not in text
        # The one Purchase key that IS used reaches the row as a number only.
        assert result["leads"][0]["sale_value"] == 2400.0

    def test_a_malformed_amount_is_null_not_a_crash(self, monkeypatch):
        """A payload whose amount is not a number (or a payload that is not a
        dict at all) must leave sale_value null rather than 500 the screen."""
        bad = sig("Sent", event="Purchase")
        bad["dataset_target"] = "legacy"
        bad["payload"] = {"total_new_premium_at_purchase": "not-a-number"}
        [row] = _progress_one(monkeypatch, _rich_lead([sig("Sent"), bad]))["leads"]
        assert row["sale_value"] is None

        worse = sig("Sent", event="Purchase")
        worse["dataset_target"] = "legacy"
        worse["payload"] = None
        [row] = _progress_one(monkeypatch, _rich_lead([sig("Sent"), worse]))["leads"]
        assert row["sale_value"] is None


class TestMalformedPayloadsNeverBreakTheScreen:
    """From review of d859b35: a payload that is not a dict, or an amount that
    float() accepts but JSON cannot carry, must read as no amount — never an
    exception, never a NaN in the response."""

    def _purchase_with(self, payload):
        s = sig("Sent", event="Purchase")
        s["dataset_target"] = "legacy"
        s["payload"] = payload
        return s

    @pytest.mark.parametrize("payload", [["a", "b"], "a string", 42, True])
    def test_a_non_dict_payload_reads_as_no_amount(self, monkeypatch, payload):
        [row] = _progress_one(monkeypatch, _rich_lead([sig("Sent"), self._purchase_with(payload)]))["leads"]
        assert row["sale_value"] is None

    @pytest.mark.parametrize("amount", ["NaN", "nan", "inf", "-inf", "Infinity", "0", "-100"])
    def test_an_amount_that_is_not_finite_and_positive_reads_as_none(self, monkeypatch, amount):
        [row] = _progress_one(
            monkeypatch,
            _rich_lead([sig("Sent"), self._purchase_with({"total_new_premium_at_purchase": amount})]),
        )["leads"]
        assert row["sale_value"] is None

    def test_the_response_serialises_to_strict_json(self, monkeypatch):
        """allow_nan=False is what a strict JSON encoder does; a NaN anywhere
        would raise here, as it would break the browser's parse."""
        import json
        result = _progress_one(
            monkeypatch,
            _rich_lead([sig("Sent"), self._purchase_with({"total_new_premium_at_purchase": "NaN"})]),
        )
        json.dumps(result, allow_nan=False)
