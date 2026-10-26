"""Reading back from Meta: what it says it received.

THE FIXTURES BELOW ARE CAPTURED FROM THE REAL API, not written from the
documentation. /stats nests as data[].data[], the inner shape differs per
aggregation, and neither is described in Meta's own SDK model — so a
hand-written fixture would only prove the parser matches a guess.
"""

import httpx
import pytest

import meta_reporting


# Two hourly buckets, as Meta actually returns them. Summing across buckets is
# the whole job: reading only the first reports one hour as a fortnight.
EVENT_TOTALS = {
    "data": [
        {
            "start_time": "2026-08-07T03:42:21+0000",
            "aggregation": "event_total_counts",
            "data": [
                {"value": "Lead", "count": 30},
                {"value": "CompleteRegistration", "count": 6},
            ],
        },
        {
            "start_time": "2026-08-08T03:42:21+0000",
            "aggregation": "event_total_counts",
            "data": [
                {"value": "Lead", "count": 8},
                {"value": "CompleteRegistration", "count": 2},
            ],
        },
    ]
}

# match_keys carries an EXTRA key the other aggregation does not: `event`.
MATCH_KEYS = {
    "data": [
        {
            "start_time": "2026-08-07T18:00:00+0000",
            "aggregation": "match_keys",
            "data": [
                {"event": "Lead", "value": "email", "count": 40},
                {"event": "Lead", "value": "fn", "count": 5},
                {"event": "CompleteRegistration", "value": "email", "count": 8},
            ],
        },
        {
            "start_time": "2026-08-07T19:00:00+0000",
            "aggregation": "match_keys",
            "data": [
                {"event": "Lead", "value": "email", "count": 9},
                {"event": "Lead", "value": "fn", "count": 3},
            ],
        },
    ]
}

DATASET = {
    "id": "3253348891512104",
    "name": "Auto Insurance",
    "last_fired_time": "2026-08-20T13:52:21+0000",
}


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setenv("META_DATASET_QUALITY_TOKEN", "reporting-token")
    monkeypatch.setenv("META_CAPI_DATASET_ID", "3253348891512104")


def _stub(monkeypatch, *, capture=None, fail=None):
    """Replace the transport only. The parsing under test runs as written."""

    def _get(url, params=None, headers=None, timeout=None):
        if capture is not None:
            capture.append({"url": url, "params": params, "headers": headers})
        if fail:
            raise fail
        if "/stats" in url:
            body = MATCH_KEYS if params.get("aggregation") == "match_keys" else EVENT_TOTALS
        else:
            body = DATASET
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(meta_reporting.httpx, "get", _get)


class TestParsing:
    def test_hourly_buckets_are_summed_across_the_window(self, monkeypatch):
        _stub(monkeypatch)
        snapshot = meta_reporting.dataset_snapshot(days=14)
        # 30 + 8, not 30 and not 8.
        assert snapshot["totals"] == {"Lead": 38, "CompleteRegistration": 8}

    def test_match_keys_are_grouped_by_event(self, monkeypatch):
        """The aggregation Meta keys by `event` as well as `value` — the shape
        event_total_counts does not have."""
        _stub(monkeypatch)
        snapshot = meta_reporting.dataset_snapshot(days=14)
        assert snapshot["match_keys"]["Lead"] == {"email": 49, "fn": 8}
        assert snapshot["match_keys"]["CompleteRegistration"] == {"email": 8}

    def test_last_fired_time_comes_from_the_dataset_object(self, monkeypatch):
        _stub(monkeypatch)
        assert meta_reporting.dataset_snapshot()["last_fired_time"] == (
            "2026-08-20T13:52:21+0000"
        )


class TestTheWindow:
    """THE DEFAULT WINDOW IS ABOUT 24 HOURS, which is what made a healthy
    integration look 12 days stale during the investigation that prompted all
    of this. The window has to be sent, and sent in the right unit."""

    def test_start_and_end_are_unix_seconds_spanning_the_requested_days(self, monkeypatch):
        calls = []
        _stub(monkeypatch, capture=calls)
        meta_reporting.dataset_snapshot(days=14)

        stats = [c for c in calls if "/stats" in c["url"]]
        assert stats, "no /stats call was made"
        for call in stats:
            start = call["params"]["start_time"]
            end = call["params"]["end_time"]
            assert isinstance(start, int) and isinstance(end, int)
            # Seconds, not milliseconds: a ms value is ~1000x too large and
            # Meta reads it as a date far in the future.
            assert 1_000_000_000 < start < 10_000_000_000
            assert end - start == pytest.approx(14 * 86400, abs=5)

    def test_a_different_days_value_moves_the_window(self, monkeypatch):
        calls = []
        _stub(monkeypatch, capture=calls)
        meta_reporting.dataset_snapshot(days=1)
        stats = [c for c in calls if "/stats" in c["url"]][0]
        span = stats["params"]["end_time"] - stats["params"]["start_time"]
        assert span == pytest.approx(86400, abs=5)


class TestItNeverRaises:
    """This feeds a panel whose other half is read from our own database and
    is still true when Meta is unreachable. Taking the page down over a failed
    external read would hide correct information to report an unavailable
    one."""

    def test_no_token_returns_none(self, monkeypatch):
        monkeypatch.delenv("META_DATASET_QUALITY_TOKEN", raising=False)
        assert meta_reporting.dataset_snapshot() is None

    def test_a_network_failure_returns_none(self, monkeypatch):
        _stub(monkeypatch, fail=httpx.ConnectError("unreachable"))
        assert meta_reporting.dataset_snapshot() is None

    def test_a_timeout_returns_none(self, monkeypatch):
        _stub(monkeypatch, fail=httpx.ReadTimeout("slow"))
        assert meta_reporting.dataset_snapshot() is None

    def test_a_400_returns_none(self, monkeypatch):
        def _get(url, params=None, headers=None, timeout=None):
            return httpx.Response(
                400,
                json={"error": {"message": "(#270) Insufficient permissions", "code": 270}},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(meta_reporting.httpx, "get", _get)
        assert meta_reporting.dataset_snapshot() is None

    def test_a_non_json_body_returns_none(self, monkeypatch):
        def _get(url, params=None, headers=None, timeout=None):
            return httpx.Response(
                200, text="<html>502</html>", request=httpx.Request("GET", url)
            )

        monkeypatch.setattr(meta_reporting.httpx, "get", _get)
        assert meta_reporting.dataset_snapshot() is None


class TestTheTokenDoesNotLeak:
    def test_the_token_travels_in_the_header_not_the_query(self, monkeypatch):
        """httpx logs request URLs at INFO, so a token in the query string
        lands in every server log line."""
        calls = []
        _stub(monkeypatch, capture=calls)
        meta_reporting.dataset_snapshot()

        for call in calls:
            assert call["headers"]["Authorization"] == "Bearer reporting-token"
            assert "reporting-token" not in str(call["params"])
            assert "access_token" not in (call["params"] or {})

    def test_a_failure_logs_the_status_and_code_only(self, monkeypatch, caplog):
        """Meta quotes the access token back inside some error messages, so
        the full body must never reach a log line."""

        def _get(url, params=None, headers=None, timeout=None):
            return httpx.Response(
                400,
                json={"error": {"message": "Bad token reporting-token", "code": 190}},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(meta_reporting.httpx, "get", _get)
        with caplog.at_level("WARNING"):
            meta_reporting.dataset_snapshot()

        assert "reporting-token" not in caplog.text
        assert "190" in caplog.text


class TestFieldNormalisation:
    """WE AND META NAME FIVE OF THESE FIELDS DIFFERENTLY. We send Meta's own
    short Conversions API parameter names; /stats reports several of them back
    under longer ones. Stacking the two rows raw would put "em 35/35" above
    "email 49" and read as two unrelated fields."""

    def test_every_reported_field_has_a_label_and_a_meta_key(self):
        from services.meta_signal import _REPORTED_FIELDS, field_labels

        labels = field_labels()
        assert [f["field"] for f in labels] == list(_REPORTED_FIELDS), (
            "the display order must follow _REPORTED_FIELDS, so a field absent "
            "from every event still holds its place instead of vanishing"
        )
        for entry in labels:
            assert entry["label"] and entry["meta_key"]

    def test_the_five_renamed_fields_map_correctly(self):
        from services.meta_signal import field_labels

        by_field = {f["field"]: f["meta_key"] for f in field_labels()}
        assert by_field["em"] == "email"
        assert by_field["ph"] == "phone"
        assert by_field["db"] == "dob"
        assert by_field["ge"] == "gen"
        assert by_field["zp"] == "zip"

    def test_the_other_seven_are_named_the_same_on_both_sides(self):
        """Which is exactly why the mismatch is easy to miss."""
        from services.meta_signal import field_labels

        for entry in field_labels():
            if entry["field"] not in {"em", "ph", "db", "ge", "zp"}:
                assert entry["meta_key"] == entry["field"]

    def test_the_map_matches_what_meta_actually_returned(self):
        """Guard rail against drift: these are the key names in the captured
        live response at the top of this file."""
        from services.meta_signal import field_labels

        meta_keys = {f["meta_key"] for f in field_labels()}
        seen = {e["value"] for b in MATCH_KEYS["data"] for e in b["data"]}
        assert seen <= meta_keys, f"Meta returned keys we do not map: {seen - meta_keys}"


class TestMatchKeysAreOccurrencesNotCoverage:
    """THE COUNTS CAN EXCEED THE EVENT TOTAL, so they are not a fraction.

    Meta counts key OCCURRENCES, and build_user_data sends em/ph as arrays —
    the lead's contact plus the applicant's, deduplicated after hashing — so
    one event can contribute two. Live: Meta reported 41 Lead events and
    email 52. Dividing rendered "52/41" in green, a coverage figure over 100%
    that means nothing.

    This test exists to stop anyone reintroducing a denominator.
    """

    def test_a_key_count_may_exceed_the_event_total(self, monkeypatch):
        totals = {
            "data": [{
                "start_time": "2026-08-07T03:42:21+0000",
                "aggregation": "event_total_counts",
                "data": [{"value": "Lead", "count": 41}],
            }]
        }
        keys = {
            "data": [{
                "start_time": "2026-08-07T18:00:00+0000",
                "aggregation": "match_keys",
                "data": [{"event": "Lead", "value": "email", "count": 52}],
            }]
        }

        def _get(url, params=None, headers=None, timeout=None):
            if "/stats" in url:
                body = keys if params.get("aggregation") == "match_keys" else totals
            else:
                body = DATASET
            return httpx.Response(200, json=body, request=httpx.Request("GET", url))

        monkeypatch.setattr(meta_reporting.httpx, "get", _get)
        snapshot = meta_reporting.dataset_snapshot(days=14)

        assert snapshot["match_keys"]["Lead"]["email"] == 52
        assert snapshot["totals"]["Lead"] == 41
        # Reported as-is. The service must not normalise, cap or divide these
        # — the UI shows a bare count precisely because this is possible.
        assert snapshot["match_keys"]["Lead"]["email"] > snapshot["totals"]["Lead"]


class TestPartialFailure:
    """One aggregation failing must not fabricate a denominator of zero."""

    def test_missing_totals_leaves_match_keys_usable(self, monkeypatch):
        keys = {
            "data": [{
                "start_time": "2026-08-07T18:00:00+0000",
                "aggregation": "match_keys",
                "data": [{"event": "Lead", "value": "email", "count": 49}],
            }]
        }

        def _get(url, params=None, headers=None, timeout=None):
            if params and params.get("aggregation") == "event_total_counts":
                raise httpx.ConnectError("down")
            body = keys if "/stats" in url else DATASET
            return httpx.Response(200, json=body, request=httpx.Request("GET", url))

        monkeypatch.setattr(meta_reporting.httpx, "get", _get)
        snapshot = meta_reporting.dataset_snapshot(days=14)

        assert snapshot is not None, "a partial failure still carries real information"
        assert snapshot["totals"] == {}
        assert snapshot["match_keys"]["Lead"]["email"] == 49
