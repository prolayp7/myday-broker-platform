"""Repository-shape tests for repositories.meta_signal — asserting the FILTERS
the Supabase query carries, not the data it returns.

Here because count_sent_since (added for the Meta Quality screen's 7-day
learning count) has to filter on four things at once (status, event_name,
dataset_target, sent_at) and getting any one wrong gives a plausible-looking
but inflated number: an older row, a sent 'common' row (August history), or a
Pending one. A silent inflation is the exact failure the screen must not have
— it would say Meta has learned when it has not.

A fake Supabase client records every method call rather than hitting a DB.
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")):
    pytest.skip(
        "needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY — the module reaches db.py "
        "at import time (no call is made)",
        allow_module_level=True,
    )

from repositories import meta_signal as repo  # noqa: E402


class _FakeTable:
    """Chainable stub: every call appends (method, args, kwargs) to .calls and
    returns self; .execute() returns a .count sentinel so the service can read
    .count without blowing up."""

    def __init__(self):
        self.calls = []

    def _record(self, method, *args, **kwargs):
        self.calls.append((method, args, kwargs))
        return self

    def __getattr__(self, name):
        def chainable(*args, **kwargs):
            return self._record(name, *args, **kwargs)
        return chainable

    def execute(self):
        self.calls.append(("execute", (), {}))
        class R:
            count = 7
        return R()


@pytest.fixture
def fake_table(monkeypatch):
    """Replace supabase.table so repo methods talk to our stub."""
    table = _FakeTable()
    monkeypatch.setattr(repo.supabase, "table", lambda name: _record_table(table, name))
    return table


def _record_table(table, name):
    table.calls.append(("table", (name,), {}))
    return table


class TestCountSentSince:
    def test_hits_the_meta_signal_table(self, fake_table):
        repo.count_sent_since("CompleteRegistration", "2026-10-18T00:00:00+00:00")
        assert ("table", ("meta_signal",), {}) in fake_table.calls

    def test_asks_for_an_exact_head_count(self, fake_table):
        repo.count_sent_since("CompleteRegistration", "2026-10-18T00:00:00+00:00")
        # select("id", count="exact") is what makes PostgREST return a count
        # without the rows themselves.
        [select] = [c for c in fake_table.calls if c[0] == "select"]
        assert select == ("select", ("id",), {"count": "exact"})

    def test_filters_sent_rows_only(self, fake_table):
        repo.count_sent_since("CompleteRegistration", "2026-10-18T00:00:00+00:00")
        eqs = [(args, kwargs) for name, args, kwargs in fake_table.calls if name == "eq"]
        assert (("status", "Sent"), {}) in eqs

    def test_filters_by_the_event_the_caller_asked_for(self, fake_table):
        repo.count_sent_since("CompleteRegistration", "2026-10-18T00:00:00+00:00")
        eqs = [(args, kwargs) for name, args, kwargs in fake_table.calls if name == "eq"]
        assert (("event_name", "CompleteRegistration"), {}) in eqs

    def test_only_counts_legacy_rows(self, fake_table):
        """August 'common' rows must never count toward the learning figure —
        this is the whole reason the filter exists."""
        repo.count_sent_since("CompleteRegistration", "2026-10-18T00:00:00+00:00")
        eqs = [(args, kwargs) for name, args, kwargs in fake_table.calls if name == "eq"]
        assert (("dataset_target", "legacy"), {}) in eqs

    def test_filters_by_sent_at_not_created_at(self, fake_table):
        """created_at is when the row was queued; sent_at is when Meta had it.
        The question "did we get Meta 50 this week" is about the second."""
        repo.count_sent_since("CompleteRegistration", "2026-10-18T00:00:00+00:00")
        gtes = [(args, kwargs) for name, args, kwargs in fake_table.calls if name == "gte"]
        assert (("sent_at", "2026-10-18T00:00:00+00:00"), {}) in gtes

    def test_returns_the_count_from_supabase(self, fake_table):
        result = repo.count_sent_since("CompleteRegistration", "2026-10-18T00:00:00+00:00")
        assert result.count == 7
