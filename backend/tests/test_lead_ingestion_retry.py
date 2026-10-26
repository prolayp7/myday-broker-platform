"""Tests for the webhook retry drain.

WHY THIS FILE EXISTS. services/lead_ingestion.py promised that a failed event
was "always recoverable later ... never lost data". The raw payload really was
stored forever — but ingest_meta_event had exactly one caller, the webhook, for
the lead it had just received. Nothing swept failed rows and there was no
replay route, so "recoverable" meant someone writing SQL by hand.

Meta's own redelivery could not rescue it either: record_meta_event dedups on
leadgen_id, so a retry of a FAILED event was discarded as a duplicate before
ingestion was reconsidered. The one mechanism that would naturally retry was
deliberately suppressed.

A real lead was found stranded exactly that way, with
"Could not fetch lead data from Meta: [WinError 10061]" — a transient network
failure that would have succeeded on any later attempt.

These tests are split deliberately:

  * the BACKOFF SCHEDULE is pure arithmetic and is tested without a database,
    so the rules stay checked even offline;
  * the DRAIN ITSELF needs real rows, and is marked dbtest.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import load_dotenv

# CREDENTIALS BEFORE THE IMPORT, and this order is load-bearing.
#
# services.lead_ingestion imports db.py, which reads os.environ["SUPABASE_URL"]
# at module level — a KeyError, not a skip. An earlier version of this file
# imported lead_ingestion at line 33 and checked for credentials at line 110,
# so on a clean checkout the whole module died during COLLECTION and even the
# database-free backoff tests never ran. The guard that was supposed to make a
# fresh checkout work was written after the thing that broke it.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_HAS_DB = bool(
    os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
)

if not _HAS_DB:
    pytest.skip(
        "needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY — services.lead_ingestion "
        "reaches db.py at import time",
        allow_module_level=True,
    )

from services import lead_ingestion  # noqa: E402


# ── The retry schedule (no database needed) ──────────────────────────────
class TestBackoffSchedule:
    """What _fail writes: how many attempts have been spent, and when — if
    ever — to try again."""

    def _capture(self, attempt_count):
        """Run _fail and return the values it wrote, without a database."""
        with patch.object(lead_ingestion.webhook_event_repo, "mark_failed") as marked:
            lead_ingestion._fail("event-1", "boom", attempt_count)
        return marked.call_args.kwargs

    def test_a_first_failure_schedules_a_retry(self):
        written = self._capture(0)
        assert written["attempt_count"] == 1
        assert written["next_retry_at"] is not None

    @pytest.mark.parametrize(
        "already_spent,expected_delay_minutes",
        [(0, 1), (1, 5), (2, 30)],
    )
    def test_the_delay_grows_with_each_attempt(self, already_spent, expected_delay_minutes):
        """1, 5, 30 minutes. The first retry is nearly immediate because the
        common failure is a blip; the later ones give a real outage — or a
        human mapping a form — time to happen."""
        before = datetime.now(timezone.utc)
        written = self._capture(already_spent)
        scheduled = datetime.fromisoformat(written["next_retry_at"])

        delay = scheduled - before
        assert delay >= timedelta(minutes=expected_delay_minutes) - timedelta(seconds=5)
        assert delay <= timedelta(minutes=expected_delay_minutes) + timedelta(seconds=30)

    def test_it_gives_up_after_the_cap(self):
        """next_retry_at of None IS the terminal state — there is no separate
        'exhausted' status that could disagree with it."""
        written = self._capture(lead_ingestion.MAX_ATTEMPTS - 1)
        assert written["attempt_count"] == lead_ingestion.MAX_ATTEMPTS
        assert written["next_retry_at"] is None

    def test_giving_up_is_logged_at_error_not_warning(self, caplog):
        """A row that will not fix itself is the one worth noticing in a log
        full of transient warnings."""
        import logging

        with caplog.at_level(logging.WARNING, logger="myday.lead_ingestion"):
            with patch.object(lead_ingestion.webhook_event_repo, "mark_failed"):
                lead_ingestion._fail("event-1", "boom", lead_ingestion.MAX_ATTEMPTS - 1)
        assert any(r.levelno == logging.ERROR and "GAVE UP" in r.message for r in caplog.records)

    def test_a_retryable_failure_is_only_a_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="myday.lead_ingestion"):
            with patch.object(lead_ingestion.webhook_event_repo, "mark_failed"):
                lead_ingestion._fail("event-1", "boom", 0)
        assert all(r.levelno < logging.ERROR for r in caplog.records)
        assert "retrying" in caplog.text

    def test_the_reason_is_preserved_verbatim(self):
        """The failure text is the only diagnostic a person has when the row
        finally stops retrying."""
        with patch.object(lead_ingestion.webhook_event_repo, "mark_failed") as marked:
            lead_ingestion._fail("event-1", "No lead_form mapping for form_id=123", 0)
        assert marked.call_args.args[1] == "No lead_form mapping for form_id=123"


# ── The drain, against real rows ─────────────────────────────────────────
# No second credentials check here: the module-level skip above already
# guarantees they exist, since nothing in this file can even import without
# them.


@pytest.mark.dbtest
class TestTheDrainPicksUpFailedEvents:
    """The behaviour that was missing entirely: something, eventually, tries
    a failed event again."""

    @pytest.fixture
    def event(self):
        """A failed webhook_event due for retry right now. Removed afterwards."""
        import uuid

        from db import supabase

        row = supabase.table("webhook_event").insert({
            "source": "meta",
            "dedup_key": f"ZZTEST-retry-{uuid.uuid4().hex}",
            "raw_payload": {"entry": [{"changes": [{"value": {
                "leadgen_id": f"zz-{uuid.uuid4().hex[:12]}",
                "form_id": "zz-unmapped-form",
            }}]}]},
            "signature_valid": True,
            "status": "failed",
            "failure_reason": "seeded by a test",
            "attempt_count": 0,
            "next_retry_at": datetime.now(timezone.utc).isoformat(),
        }).execute().data[0]
        try:
            yield row
        finally:
            supabase.table("webhook_event").delete().eq("id", row["id"]).execute()

    def test_a_due_event_is_found_and_retried(self, event):
        """The whole point. Before this, nothing ever read these rows."""
        from db import supabase

        result = lead_ingestion.retry_failed_events()
        assert result["checked"] >= 1

        after = supabase.table("webhook_event").select(
            "attempt_count, next_retry_at, status"
        ).eq("id", event["id"]).execute().data[0]

        # The form is deliberately unmapped, so the retry fails again — but it
        # must be RECORDED as an attempt and rescheduled, which is what proves
        # the drain reached it at all.
        assert after["attempt_count"] == 1, after
        assert after["next_retry_at"] is not None, after

    def test_an_event_not_yet_due_is_left_alone(self):
        """Backoff has to actually hold the row back, or the drain becomes a
        hot loop against Meta."""
        import uuid

        from db import supabase

        row = supabase.table("webhook_event").insert({
            "source": "meta",
            "dedup_key": f"ZZTEST-future-{uuid.uuid4().hex}",
            "raw_payload": {"entry": [{"changes": [{"value": {
                "leadgen_id": f"zz-{uuid.uuid4().hex[:12]}", "form_id": "zz-form",
            }}]}]},
            "signature_valid": True,
            "status": "failed",
            "attempt_count": 1,
            "next_retry_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }).execute().data[0]
        try:
            lead_ingestion.retry_failed_events()
            after = supabase.table("webhook_event").select("attempt_count").eq(
                "id", row["id"]).execute().data[0]
            assert after["attempt_count"] == 1, "a future retry was run early"
        finally:
            supabase.table("webhook_event").delete().eq("id", row["id"]).execute()

    def test_an_exhausted_event_is_left_alone(self):
        """next_retry_at is null once the attempts are spent, so the row stops
        moving on its own and waits for a person."""
        import uuid

        from db import supabase

        row = supabase.table("webhook_event").insert({
            "source": "meta",
            "dedup_key": f"ZZTEST-done-{uuid.uuid4().hex}",
            "raw_payload": {"entry": [{"changes": [{"value": {
                "leadgen_id": f"zz-{uuid.uuid4().hex[:12]}", "form_id": "zz-form",
            }}]}]},
            "signature_valid": True,
            "status": "failed",
            "attempt_count": lead_ingestion.MAX_ATTEMPTS,
            "next_retry_at": None,
        }).execute().data[0]
        try:
            lead_ingestion.retry_failed_events()
            after = supabase.table("webhook_event").select("attempt_count").eq(
                "id", row["id"]).execute().data[0]
            assert after["attempt_count"] == lead_ingestion.MAX_ATTEMPTS
        finally:
            supabase.table("webhook_event").delete().eq("id", row["id"]).execute()

    def test_an_unexpected_exception_still_leaves_a_retryable_event(self):
        """THE PERMANENT STRAND. ingest_meta_event's docstring says it never
        raises, but three calls sat outside any try — the form lookup, the
        parser, and mark_processed. Proven reachable:

            parse_field_data(['not-a-dict']) -> AttributeError

        An escape on the first attempt left status='received', and the drain
        selects status='failed' only. So the event was stranded forever — the
        exact failure the drain was built to end.

        Asserts the OUTCOME (a retryable row), not the mechanism, so it stays
        honest if the guard is restructured later.
        """
        import uuid

        from db import supabase

        # A REAL, MAPPED form id. An earlier version used a made-up one, so
        # ingestion failed at the form lookup and returned before ever reaching
        # the parser — the test passed without exercising the bug at all.
        form = supabase.table("lead_form").select(
            "platform_form_id").limit(1).execute().data
        if not form:
            pytest.skip("needs at least one lead_form mapping")

        row = supabase.table("webhook_event").insert({
            "source": "meta",
            "dedup_key": f"ZZTEST-boom-{uuid.uuid4().hex}",
            # Valid leadgen shape and a mapped form, so it gets all the way to
            # the parser — which then chokes on field_data.
            "raw_payload": {"entry": [{"changes": [{"value": {
                "leadgen_id": f"zz-{uuid.uuid4().hex[:12]}",
                "form_id": form[0]["platform_form_id"],
            }}]}]},
            "signature_valid": True,
            "status": "received",
            "attempt_count": 0,
        }).execute().data[0]
        try:
            with patch.object(
                lead_ingestion.meta_graph, "fetch_lead_data",
                return_value={"field_data": ["not-a-dict"]},
            ):
                # Must not raise out of ingestion at all.
                lead_ingestion.ingest_meta_event(row)

            after = supabase.table("webhook_event").select(
                "status, attempt_count, next_retry_at, failure_reason"
            ).eq("id", row["id"]).execute().data[0]

            assert after["status"] == "failed", (
                f"left as {after['status']} — the drain only looks at 'failed', "
                "so this event would never be retried"
            )
            assert after["next_retry_at"] is not None
            assert after["attempt_count"] == 1
            assert after["failure_reason"], "a stranded event must say why"
        finally:
            supabase.table("webhook_event").delete().eq("id", row["id"]).execute()

    def test_an_abandoned_received_event_is_swept_up(self):
        """A row stuck at 'received' is invisible to a drain that only selects
        'failed'. The exception boundary stops new ones appearing, but a
        process dying between storing the event and writing an outcome can
        still produce one — as could anything stranded before that boundary
        existed."""
        import uuid
        from datetime import timedelta as _timedelta

        from db import supabase

        old = (datetime.now(timezone.utc) - _timedelta(hours=2)).isoformat()
        row = supabase.table("webhook_event").insert({
            "source": "meta",
            "dedup_key": f"ZZTEST-stale-{uuid.uuid4().hex}",
            "raw_payload": {"entry": [{"changes": [{"value": {
                "leadgen_id": f"zz-{uuid.uuid4().hex[:12]}",
                "form_id": "zz-unmapped",
            }}]}]},
            "signature_valid": True,
            "status": "received",
            "attempt_count": 0,
            "created_at": old,
        }).execute().data[0]
        try:
            lead_ingestion.retry_failed_events()
            after = supabase.table("webhook_event").select(
                "status, attempt_count").eq("id", row["id"]).execute().data[0]
            assert after["status"] == "failed", (
                "an abandoned 'received' row was not swept up, so nothing "
                "would ever look at it again"
            )
            assert after["attempt_count"] == 1
        finally:
            supabase.table("webhook_event").delete().eq("id", row["id"]).execute()

    def test_a_RECENT_received_event_is_left_alone(self):
        """An event that just arrived may still be mid-ingestion in another
        request. Sweeping it would run ingestion twice concurrently."""
        import uuid

        from db import supabase

        row = supabase.table("webhook_event").insert({
            "source": "meta",
            "dedup_key": f"ZZTEST-fresh-{uuid.uuid4().hex}",
            "raw_payload": {"entry": [{"changes": [{"value": {
                "leadgen_id": f"zz-{uuid.uuid4().hex[:12]}",
                "form_id": "zz-unmapped",
            }}]}]},
            "signature_valid": True,
            "status": "received",
            "attempt_count": 0,
        }).execute().data[0]
        try:
            lead_ingestion.retry_failed_events()
            after = supabase.table("webhook_event").select(
                "status, attempt_count").eq("id", row["id"]).execute().data[0]
            assert after["status"] == "received", "a fresh event was swept early"
            assert after["attempt_count"] == 0
        finally:
            supabase.table("webhook_event").delete().eq("id", row["id"]).execute()

    def test_an_existing_lead_is_reconciled_as_success(self):
        """A retry after a PARTIAL success must not burn attempts.

        create_lead and mark_processed are separate transactions. If the lead
        is created but marking fails, the row stays 'failed' — and every retry
        then hits the unique index on platform_leadgen_id, is recorded as a
        failure, and eventually gives up. The customer's lead exists the whole
        time while the event claims permanent failure.

        The right reading of "this lead already exists" is that ingestion
        SUCCEEDED, not that it failed.
        """
        import uuid

        from db import supabase

        leadgen_id = f"zzdup-{uuid.uuid4().hex[:12]}"
        form = supabase.table("lead_form").select(
            "platform_form_id, group_id").limit(1).execute().data
        if not form:
            pytest.skip("needs at least one lead_form mapping")
        form = form[0]

        existing = supabase.table("lead").insert({
            "group_id": form["group_id"], "source": "meta",
            "name": "ZZTEST already here", "phone": "+15550001111",
            "email": f"zzdup-{uuid.uuid4().hex[:10]}@example.com",
            "platform_leadgen_id": leadgen_id,
        }).execute().data[0]

        row = supabase.table("webhook_event").insert({
            "source": "meta",
            "dedup_key": f"ZZTEST-dup-{uuid.uuid4().hex}",
            "raw_payload": {"entry": [{"changes": [{"value": {
                "leadgen_id": leadgen_id,
                "form_id": form["platform_form_id"],
            }}]}]},
            "signature_valid": True,
            "status": "failed",
            "attempt_count": 1,
            "next_retry_at": datetime.now(timezone.utc).isoformat(),
        }).execute().data[0]
        try:
            with patch.object(
                lead_ingestion.meta_graph, "fetch_lead_data",
                return_value={"field_data": [
                    {"name": "full_name", "values": ["ZZTEST already here"]},
                ]},
            ):
                lead_ingestion.ingest_meta_event(row)

            after = supabase.table("webhook_event").select(
                "status, attempt_count").eq("id", row["id"]).execute().data[0]
            assert after["status"] == "processed", (
                "the lead already exists, so ingestion succeeded — recording a "
                "failure here burns attempts and eventually gives up on a lead "
                "that was never lost"
            )

            duplicates = supabase.table("lead").select("id").eq(
                "platform_leadgen_id", leadgen_id).execute().data
            assert len(duplicates) == 1, "a second lead was created"
        finally:
            for t in ("lead_stage_history", "lead_assignment", "lead_delivery"):
                supabase.table(t).delete().eq("lead_id", existing["id"]).execute()
            supabase.table("audit_log").delete().eq("entity_id", existing["id"]).execute()
            supabase.table("lead").delete().eq("id", existing["id"]).execute()
            supabase.table("webhook_event").delete().eq("id", row["id"]).execute()

    def test_a_processed_event_is_never_retried(self):
        """Re-ingesting a success would try to create a second lead for one
        customer — the exact thing the dedup exists to prevent."""
        import uuid

        from db import supabase

        row = supabase.table("webhook_event").insert({
            "source": "meta",
            "dedup_key": f"ZZTEST-ok-{uuid.uuid4().hex}",
            "raw_payload": {"entry": [{"changes": [{"value": {
                "leadgen_id": f"zz-{uuid.uuid4().hex[:12]}", "form_id": "zz-form",
            }}]}]},
            "signature_valid": True,
            "status": "processed",
            "attempt_count": 0,
            # Deliberately set, to prove status is what excludes it — a
            # processed row must not be retried even if something left a
            # retry time on it.
            "next_retry_at": datetime.now(timezone.utc).isoformat(),
        }).execute().data[0]
        try:
            lead_ingestion.retry_failed_events()
            after = supabase.table("webhook_event").select("attempt_count, status").eq(
                "id", row["id"]).execute().data[0]
            assert after["status"] == "processed"
            assert after["attempt_count"] == 0, "a processed event was re-ingested"
        finally:
            supabase.table("webhook_event").delete().eq("id", row["id"]).execute()
