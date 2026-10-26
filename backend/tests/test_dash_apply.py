"""Tests for writing a parsed DASH report onto the broker's form.

WHY THIS FILE EXISTS. dash_apply.py had NO tests, and it holds the rule the
whole feature rests on: never overwrite the broker. That rule is invisible
when it works — the field simply keeps the value it already had — so a change
that broke it would pass every other test in this suite and be noticed only
when a broker's correction had already been silently replaced.

The other half is the report's SUBJECT. Nothing here rejects a report for the
wrong person; the broker decides that. But the name and licence have to come
back so the screen can show them, and the case worth protecting is the one a
naive implementation gets wrong — reporting only what was WRITTEN goes quiet
exactly when the broker already typed a name, which is when a mismatch is
likeliest.

The repositories are stubbed rather than hit: this is about the decision
dash_apply makes, not about Supabase.
"""

import pytest

from services import dash_apply


class _Result:
    """Stands in for a PostgREST response, which is `.data` and nothing else."""

    def __init__(self, data):
        self.data = data


@pytest.fixture
def driver_row():
    """A driver as it exists just after a lead is opened.

    Name and phone are seeded from the lead; everything else is null, which is
    what makes a report worth uploading at all.
    """
    return {
        "id": "d1",
        "lead_id": "l1",
        "name": None,
        "license_number": None,
        "license_province": None,
        "gender": None,
        "marital_status": None,
        "street_address": None,
        "city": None,
        "province": None,
        "postal_code": None,
    }


@pytest.fixture
def stub(monkeypatch, driver_row):
    """Wire dash_apply to in-memory doubles, and record what it writes.

    Returns the recorder so a test can assert on the UPDATE rather than on a
    return value alone — "it said it filled the city" and "it wrote the city"
    are different claims, and only the second one is the feature.
    """
    written: dict = {}
    inserted: list[dict] = []
    added_drivers: list[dict] = []
    state = {"driver": driver_row, "vehicles": [], "drivers": [driver_row]}

    monkeypatch.setattr(
        dash_apply.driver_repo, "get_by_id",
        lambda _id: _Result([state["driver"]]),
    )
    monkeypatch.setattr(
        dash_apply.driver_repo, "list_for_lead",
        lambda _id: _Result(state["drivers"]),
    )
    monkeypatch.setattr(
        dash_apply.driver_repo, "insert_additional",
        lambda lead_id, rel, values=None: (
            added_drivers.append({**(values or {}), "relationship": rel})
            or _Result([{}])
        ),
    )
    monkeypatch.setattr(
        dash_apply.driver_repo, "update",
        lambda _id, values: written.update(values) or _Result([values]),
    )
    monkeypatch.setattr(
        dash_apply.vehicle_repo, "list_for_driver",
        lambda _id: _Result(state["vehicles"]),
    )
    monkeypatch.setattr(
        dash_apply.vehicle_repo, "insert",
        lambda row: inserted.append(row) or _Result([row]),
    )
    # The policy-type question, answered yes by default. dash_apply asks it
    # before importing cars (a property-only policy insures none), and the
    # real one reads the lead — which these stubs do not create. Overridden by
    # the test that cares.
    monkeypatch.setattr(
        dash_apply.vehicle_service, "lead_insures_vehicles", lambda _lead_id: True
    )
    return {
        "written": written,
        "inserted": inserted,
        "drivers": added_drivers,
        "state": state,
    }


def _report(monkeypatch, driver: dict, vehicles: list[dict] | None = None):
    """Replace the PARSER, so these tests are about apply and nothing else.

    dash_parse has its own suite against a real fixture; re-reading that
    fixture here would make a parser change fail in two places and say nothing
    extra about the writing.
    """
    monkeypatch.setattr(dash_apply.dash_parse, "parse_driver", lambda _md: driver)
    monkeypatch.setattr(dash_apply.dash_parse, "parse_vehicles", lambda _md: vehicles or [])
    # Defaults to none, so a test about the driver's own fields never reaches
    # the operator path. Overridden by _operators() below.
    monkeypatch.setattr(dash_apply.dash_parse, "parse_operators", lambda _md: [])


def _operators(monkeypatch, operators: list[dict]):
    monkeypatch.setattr(dash_apply.dash_parse, "parse_operators", lambda _md: operators)


# ── The subject of the report ────────────────────────────────────────────


def test_at_fault_claims_reach_the_driver_row(stub, monkeypatch):
    """THE TEST THAT CATCHES THE _DRIVER_FIELDS TRAP.

    Asserted on the UPDATE, not on the parse. dash_parse has its own passing
    test proving it reads this number — and that test would go on passing while
    _DRIVER_FIELDS omitted the field, the column stayed null, and the Preferred
    classifier read nothing. That is precisely how date_of_birth shipped: green
    suite, dead feature.

    An int, not a string: driver.at_fault_claims_6y is an int column (0114) and
    the write loop passes parsed values through without coercion.
    """
    _report(monkeypatch, {"at_fault_claims_6y": 2})

    dash_apply.apply_report("d1", "# report")

    assert stub["written"]["at_fault_claims_6y"] == 2
    assert isinstance(stub["written"]["at_fault_claims_6y"], int)


def test_zero_at_fault_claims_is_written_not_skipped(stub, monkeypatch):
    """ZERO IS THE ANSWER THAT MATTERS MOST, and the easiest to lose.

    It is the value that makes a lead Preferred, and it is falsy — so any guard
    written as `if new:` instead of `if new is None:` would drop exactly the
    leads this feature exists to find, while a report showing 3 claims wrote
    fine. Fifteen of the twenty-five real reports sampled carried 0.
    """
    _report(monkeypatch, {"at_fault_claims_6y": 0})

    dash_apply.apply_report("d1", "# report")

    assert stub["written"]["at_fault_claims_6y"] == 0


def test_an_unreadable_at_fault_count_writes_nothing(stub, monkeypatch):
    """A report that did not carry the line leaves the column alone.

    dash_parse omits the key entirely rather than defaulting to 0 — "no
    at-fault claims" and "we could not read it" are opposite facts, and storing
    the first when the second is true sends an unvetted lead to the premium
    dataset looking spotless.
    """
    _report(monkeypatch, {"name": "SAMPLE,TESTCASE A"})

    dash_apply.apply_report("d1", "# report")

    assert "at_fault_claims_6y" not in stub["written"]


def test_a_broker_value_is_not_overwritten_by_the_report(stub, monkeypatch):
    """The rule this whole module rests on, applied to the new field.

    A broker who has already recorded a count has spoken to the customer; a
    re-parsed report must not quietly replace their answer.
    """
    stub["state"]["driver"]["at_fault_claims_6y"] = 1

    _report(monkeypatch, {"at_fault_claims_6y": 3})

    result = dash_apply.apply_report("d1", "# report")

    assert "at_fault_claims_6y" not in stub["written"]
    assert "at_fault_claims_6y" in result["driver_skipped"]


def test_the_subject_is_returned(stub, monkeypatch):
    """Whose report was this? The screen cannot say so unless apply says so."""
    _report(monkeypatch, {"name": "SAMPLE,TESTCASE A", "license_number": "S1234-56789-01234"})

    result = dash_apply.apply_report("d1", "# report")

    assert result["subject_name"] == "SAMPLE,TESTCASE A"
    assert result["subject_license_number"] == "S1234-56789-01234"


def test_the_subject_is_returned_even_when_the_name_differs(stub, monkeypatch):
    """THE CASE THIS FEATURE EXISTS FOR.

    A name already on the lead is exactly when the broker is most likely to be
    holding the WRONG report — they have been working the lead by hand. The
    report's subject is returned from the parse itself, not from the list of
    fields written, so it is reported whether the name was replaced or not.
    An implementation that reported only what it WROTE would fall silent here,
    which is the one moment it must not.
    """
    stub["state"]["driver"]["name"] = "Mitali Chawla"

    _report(monkeypatch, {"name": "SAMPLE,TESTCASE A", "license_number": "S1234-56789-01234"})

    result = dash_apply.apply_report("d1", "# report")

    assert result["subject_name"] == "SAMPLE,TESTCASE A", (
        "the report's own subject must still be reported, or a mismatch is invisible"
    )


def test_the_name_is_not_reformatted(stub, monkeypatch):
    """DASH prints LAST,FIRST with no space. It stays that way.

    The broker is comparing this line against the document in front of them,
    so tidying it into "Testcase Sample" would make the comparison harder —
    which is the entire purpose of showing it.
    """
    _report(monkeypatch, {"name": "SAMPLE,TESTCASE A"})

    assert dash_apply.apply_report("d1", "# report")["subject_name"] == "SAMPLE,TESTCASE A"


def test_a_missing_driver_still_answers(monkeypatch):
    """No driver row: an empty result, not an exception.

    The keys have to be present regardless, or the screen has to guard every
    one of them.
    """
    monkeypatch.setattr(dash_apply.driver_repo, "get_by_id", lambda _id: _Result([]))

    result = dash_apply.apply_report("nobody", "# report")

    assert result["subject_name"] is None
    assert result["vehicles_added"] == 0
    assert result["driver_filled"] == []


# ── Never overwrite the broker ───────────────────────────────────────────


def test_a_full_date_of_birth_is_actually_written(stub, monkeypatch):
    """THE TEST THAT SHOULD HAVE EXISTED THE FIRST TIME.

    dash_parse gained full-date-of-birth support and a test proving it PARSES
    one. It passed. The feature still did nothing, because date_of_birth was
    never added to _DRIVER_FIELDS and apply dropped the value on the floor.

    The parser was never the question. This asserts on the UPDATE, which is
    the only thing that puts a date in front of a broker.
    """
    _report(monkeypatch, {"date_of_birth": "1997-04-12"})

    result = dash_apply.apply_report("d1", "# report")

    assert stub["written"].get("date_of_birth") == "1997-04-12"
    assert "date_of_birth" in result["driver_filled"]


def test_a_typed_date_of_birth_is_never_overwritten(stub, monkeypatch):
    """The never-overwrite rule reaches the new field too."""
    stub["state"]["driver"]["date_of_birth"] = "1990-01-01"

    _report(monkeypatch, {"date_of_birth": "1997-04-12"})

    result = dash_apply.apply_report("d1", "# report")

    assert "date_of_birth" not in stub["written"]
    assert "date_of_birth" in result["driver_skipped"]


def test_a_blank_field_is_filled(stub, monkeypatch):
    """The feature doing its job. Without this the rest is just refusal."""
    _report(monkeypatch, {"city": "SAMPLETOWN", "gender": "Male"})

    result = dash_apply.apply_report("d1", "# report")

    assert stub["written"]["city"] == "SAMPLETOWN"
    assert set(result["driver_filled"]) == {"city", "gender"}


def test_a_typed_field_is_never_overwritten(stub, monkeypatch):
    """THE RULE THE WHOLE FEATURE RESTS ON.

    A broker who corrected a licence number on the phone keeps it. A report
    uploaded afterwards cannot undo that, and the skip is REPORTED rather than
    hidden — a silent no-op looks like a failure.
    """
    stub["state"]["driver"]["license_number"] = "CORRECTED-BY-BROKER"

    _report(monkeypatch, {"license_number": "S1234-56789-01234", "city": "SAMPLETOWN"})

    result = dash_apply.apply_report("d1", "# report")

    assert "license_number" not in stub["written"], "the broker's own value was replaced"
    assert "license_number" in result["driver_skipped"]
    assert stub["written"]["city"] == "SAMPLETOWN", "other blanks must still fill"


def test_an_empty_string_counts_as_blank(stub, monkeypatch):
    """A field the broker CLEARED comes back as "", not null.

    Treating that as "already has a value" would leave it permanently
    unfillable.
    """
    stub["state"]["driver"]["city"] = ""

    _report(monkeypatch, {"city": "SAMPLETOWN"})

    dash_apply.apply_report("d1", "# report")

    assert stub["written"]["city"] == "SAMPLETOWN"


# ── Vehicles ─────────────────────────────────────────────────────────────


def test_a_known_vin_is_not_added_twice(stub, monkeypatch):
    """The only duplicate protection there is.

    Migration 0047 had a unique (lead_id, vin) index; 0048 dropped the whole
    table and 0050 recreated it without one. So this Python check is the whole
    guard, which is why it is tested.
    """
    stub["state"]["vehicles"] = [{"vin": "1AAAA1A1AAA000001"}]

    _report(monkeypatch, {}, [
        {"vin": "1AAAA1A1AAA000001", "year_make_model": "2024 HONDA - CIVIC"},
        {"vin": "2BBBB2B2BBB000002", "year_make_model": "2021 ACURA - TLX"},
    ])

    result = dash_apply.apply_report("d1", "# report")

    assert result["vehicles_added"] == 1
    assert [v["vin"] for v in stub["inserted"]] == ["2BBBB2B2BBB000002"]


def test_vin_matching_ignores_case(stub, monkeypatch):
    """A VIN typed in lower case is the same car."""
    stub["state"]["vehicles"] = [{"vin": "1aaaa1a1aaa000001"}]

    _report(monkeypatch, {}, [{"vin": "1AAAA1A1AAA000001"}])

    assert dash_apply.apply_report("d1", "# report")["vehicles_added"] == 0


# ── The other people on the policy ───────────────────────────────────────


def test_an_operator_becomes_an_additional_driver(stub, monkeypatch):
    """Name and licence written in the INSERT, relationship 'NA'.

    'NA' because DASH cannot know what this person is to OUR applicant — its
    own "Relationship to Policyholder" is about whoever owns the policy. The
    broker picks it afterwards.
    """
    _report(monkeypatch, {})
    _operators(monkeypatch, [
        {"name": "OTHERPERSON,SECOND B", "license_number": "O9876-54321-09876"},
    ])

    result = dash_apply.apply_report("d1", "# report")

    assert result["drivers_added"] == 1
    assert stub["drivers"] == [
        {
            "name": "OTHERPERSON,SECOND B",
            "license_number": "O9876-54321-09876",
            "relationship": "NA",
        }
    ]


def test_a_licence_already_on_the_lead_is_not_added_twice(stub, monkeypatch):
    """RETRY SAFETY. Uploading the same report twice must not create the
    spouse twice.

    A commit removed this check on the reasoning that "the report is not
    stored, so deduplication is unnecessary" — which was wrong. The report is
    not stored; THESE ROWS ARE. Exact match on the licence, the same trade the
    VIN check makes.
    """
    stub["state"]["drivers"] = [
        stub["state"]["driver"],
        {"id": "d2", "license_number": "O9876-54321-09876"},
    ]

    _report(monkeypatch, {})
    _operators(monkeypatch, [
        {"name": "OTHERPERSON,SECOND B", "license_number": "O9876-54321-09876"},
    ])

    assert dash_apply.apply_report("d1", "# report")["drivers_added"] == 0
    assert stub["drivers"] == []


def test_an_operator_without_a_licence_is_still_added(stub, monkeypatch):
    """The point is to save the broker asking for details the report carries.

    A name with no licence is still a name they do not have to type.
    """
    _report(monkeypatch, {})
    _operators(monkeypatch, [{"name": "NAMEONLY,PERSON"}])

    result = dash_apply.apply_report("d1", "# report")

    assert result["drivers_added"] == 1
    assert stub["drivers"] == [{"name": "NAMEONLY,PERSON", "relationship": "NA"}]


def test_a_partial_failure_counts_what_was_actually_added(stub, monkeypatch):
    """One inserts, the next does not. The count must say ONE.

    The old version wrapped the whole loop in one try and returned 0 on any
    failure — so a driver sat on screen while the toast said none were added.
    """
    calls = {"n": 0}

    def _second_fails(lead_id, rel, values=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("insert failed")
        stub["drivers"].append({**(values or {}), "relationship": rel})
        return _Result([{}])

    monkeypatch.setattr(dash_apply.driver_repo, "insert_additional", _second_fails)
    _report(monkeypatch, {})
    _operators(monkeypatch, [{"name": "FIRST,ONE"}, {"name": "SECOND,TWO"}])

    assert dash_apply.apply_report("d1", "# report")["drivers_added"] == 1


def test_an_operator_failure_does_not_lose_the_driver(stub, monkeypatch):
    """Same trade as the vehicles: the applicant's fields are already written."""
    def _boom(*_a, **_kw):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(dash_apply.driver_repo, "insert_additional", _boom)
    _report(monkeypatch, {"city": "SAMPLETOWN"})
    _operators(monkeypatch, [{"name": "X,Y", "license_number": "O9876-54321-09876"}])

    result = dash_apply.apply_report("d1", "# report")

    assert stub["written"]["city"] == "SAMPLETOWN"
    assert result["drivers_added"] == 0


def test_vehicles_count_what_was_actually_added(stub, monkeypatch):
    """One car inserts, the next does not. The count must say ONE.

    The driver loop was fixed for this; the vehicle loop kept the old
    whole-loop try, so a car could sit on screen while the toast said none
    were added.
    """
    calls = {"n": 0}

    def _second_fails(row):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("insert failed")
        stub["inserted"].append(row)
        return _Result([row])

    monkeypatch.setattr(dash_apply.vehicle_repo, "insert", _second_fails)
    _report(monkeypatch, {}, [
        {"vin": "1AAAA1A1AAA000001"},
        {"vin": "2BBBB2B2BBB000002"},
    ])

    assert dash_apply.apply_report("d1", "# report")["vehicles_added"] == 1


def test_the_same_licence_twice_in_one_report_inserts_once(stub, monkeypatch):
    """The half of the dedup that was missing.

    The check read `known` and never added to it, so a person listed on two
    policies in the SAME report was inserted twice — the check was there and
    could not see what the loop had just done.
    """
    _report(monkeypatch, {})
    _operators(monkeypatch, [
        {"name": "OTHERPERSON,SECOND B", "license_number": "O9876-54321-09876"},
        {"name": "OTHERPERSON,SECOND B", "license_number": "O9876-54321-09876"},
    ])

    assert dash_apply.apply_report("d1", "# report")["drivers_added"] == 1


def test_a_licence_matches_regardless_of_case(stub, monkeypatch):
    """`o9876…` and `O9876…` are one person, as VINs already were."""
    stub["state"]["drivers"] = [{"id": "d2", "license_number": "o9876-54321-09876"}]

    _report(monkeypatch, {})
    _operators(monkeypatch, [{"name": "X,Y", "license_number": "O9876-54321-09876"}])

    assert dash_apply.apply_report("d1", "# report")["drivers_added"] == 0


def test_a_value_the_database_would_refuse_is_skipped(stub, monkeypatch):
    """One unexpected word must not cost the broker every other field.

    gender and marital_status are CHECK-constrained. An update carrying a
    value outside the list raises 23514 and fails ENTIRELY — so the address
    and licence in the same call would be lost with it. Confirmed against the
    live database.
    """
    _report(monkeypatch, {"marital_status": "Separated", "city": "SAMPLETOWN"})

    result = dash_apply.apply_report("d1", "# report")

    assert "marital_status" not in stub["written"]
    assert stub["written"]["city"] == "SAMPLETOWN", "the rest must still be written"
    assert "marital_status" not in result["driver_filled"]


def test_a_vehicle_failure_does_not_lose_the_driver(stub, monkeypatch):
    """The driver's fields are what the broker is waiting on.

    Failing the whole upload because a vehicle row would not insert trades the
    important half for the convenient one.
    """
    def _boom(_row):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(dash_apply.vehicle_repo, "insert", _boom)
    _report(monkeypatch, {"city": "SAMPLETOWN"}, [{"vin": "2BBBB2B2BBB000002"}])

    result = dash_apply.apply_report("d1", "# report")

    assert stub["written"]["city"] == "SAMPLETOWN"
    assert result["vehicles_added"] == 0


# ── The name, split into the parts Meta matches on ───────────────────────
#
# The columns exist so Meta receives fn and ln separately. A column added to
# the table and nowhere else is invisible, and this file's own header records
# the date-of-birth feature that shipped with a passing parse test and wrote
# nothing. These assert on the UPDATE for that reason.


def _split_returns(monkeypatch, parts):
    """Pin the split, so these tests are about the WRITE, not about Claude.

    claude_client has its own suite for which name splits which way. Re-deciding
    that here would make a prompt change fail in two places and say nothing
    extra about whether dash_apply stores the result.
    """
    monkeypatch.setattr(dash_apply.claude_client, "split_name", lambda _n: parts)


def test_the_split_name_is_actually_written(stub, monkeypatch):
    """The whole point. Without this the columns stay null and CAPI keeps
    falling back to the last-space split of `name`."""
    _split_returns(monkeypatch, ("SAJIDA", "", "CHEEMA"))
    _report(monkeypatch, {"name": "CHEEMA,SAJIDA"})

    dash_apply.apply_report("d1", "# report")

    assert stub["written"]["first_name"] == "SAJIDA"
    assert stub["written"]["last_name"] == "CHEEMA"


def test_the_printed_name_is_kept_alongside_the_split(stub, monkeypatch):
    """`name` stays DASH's printed form — the broker compares it against the
    document in front of them. The split is OF that name, not a replacement."""
    _split_returns(monkeypatch, ("SAJIDA", "", "CHEEMA"))
    _report(monkeypatch, {"name": "CHEEMA,SAJIDA"})

    dash_apply.apply_report("d1", "# report")

    assert stub["written"]["name"] == "CHEEMA,SAJIDA"


def test_a_middle_name_is_written_and_kept_out_of_first(stub, monkeypatch):
    """The case middle_name exists for. Fused, fn hashes to 'johnmichael' and
    matches nobody."""
    _split_returns(monkeypatch, ("JOHN", "MICHAEL", "SMITH"))
    _report(monkeypatch, {"name": "SMITH,JOHN MICHAEL"})

    dash_apply.apply_report("d1", "# report")

    assert stub["written"]["first_name"] == "JOHN"
    assert stub["written"]["middle_name"] == "MICHAEL"


def test_the_report_replaces_a_seeded_name(stub, monkeypatch):
    """THE NAME FIELDS ARE THE EXCEPTION TO THE NEVER-OVERWRITE RULE.

    Every applicant is seeded at creation with the lead form's name, split
    automatically — nobody typed it. Keeping that over the licensing record
    would mean preferring a lead-form guess ("Sajida") to the document that
    carries the surname ("CHEEMA,SAJIDA").

    The broker's correction is still safe: they correct AFTER uploading, and
    nothing re-parses a report on its own.
    """
    stub["state"]["driver"]["name"] = "Sajida"
    stub["state"]["driver"]["first_name"] = "Sajida"
    stub["state"]["driver"]["last_name"] = None
    _split_returns(monkeypatch, ("SAJIDA", "", "CHEEMA"))
    _report(monkeypatch, {"name": "CHEEMA,SAJIDA"})

    dash_apply.apply_report("d1", "# report")

    assert stub["written"]["name"] == "CHEEMA,SAJIDA"
    assert stub["written"]["first_name"] == "SAJIDA"
    assert stub["written"]["last_name"] == "CHEEMA"


def test_a_typed_field_that_is_not_a_name_is_still_never_overwritten(stub, monkeypatch):
    """The exception is the four name fields and NOTHING else.

    Guards the blast radius: a change that widened _SEEDED_NAME_FIELDS to the
    whole form would start clobbering corrections everywhere, and this is the
    assertion that would catch it.
    """
    stub["state"]["driver"]["city"] = "Toronto"
    _split_returns(monkeypatch, ("SAJIDA", "", "CHEEMA"))
    _report(monkeypatch, {"name": "CHEEMA,SAJIDA", "city": "SAMPLETOWN"})

    dash_apply.apply_report("d1", "# report")

    assert "city" not in stub["written"], "a typed city must survive a re-parse"
    assert stub["written"]["first_name"] == "SAJIDA"


def test_an_unresolved_name_writes_no_parts(stub, monkeypatch):
    """Claude unavailable on an ambiguous name. The parse still completes and
    the boxes stay empty — a blank box gives the broker a reason to look, a
    wrongly-filled one does not."""
    _split_returns(monkeypatch, ("", "", ""))
    _report(monkeypatch, {"name": "SHARMA KARAN", "city": "SAMPLETOWN"})

    result = dash_apply.apply_report("d1", "# report")

    assert not stub["written"].get("first_name")
    assert not stub["written"].get("last_name")
    assert stub["written"]["city"] == "SAMPLETOWN", "the rest of the parse must survive"
    assert result["subject_name"] == "SHARMA KARAN"


def test_no_name_in_the_report_asks_nothing(stub, monkeypatch):
    """A report with no name must not spend a call to split one."""
    def _boom(_n):
        raise AssertionError("split_name called with no name to split")

    monkeypatch.setattr(dash_apply.claude_client, "split_name", _boom)
    _report(monkeypatch, {"city": "SAMPLETOWN"})

    dash_apply.apply_report("d1", "# report")

    assert stub["written"]["city"] == "SAMPLETOWN"

# ── The policy dates ─────────────────────────────────────────────────────


def _policies(monkeypatch, dates: dict):
    """Pin parse_policy_dates, so these tests are about the WRITE."""
    monkeypatch.setattr(dash_apply.dash_parse, "parse_policy_dates", lambda _md: dates)


def test_the_policy_dates_are_actually_written(stub, monkeypatch):
    """They come from a different section of the report than the driver's own
    details, so this asserts they survive the merge and reach the UPDATE."""
    _report(monkeypatch, {"city": "SAMPLETOWN"})
    _policies(
        monkeypatch,
        {
            "renewal_date": "2026-02-16",
            "insured_since": "2016-02-16",
            "current_insurer_since": "2024-02-16",
        },
    )

    dash_apply.apply_report("d1", "# report")

    assert stub["written"]["renewal_date"] == "2026-02-16"
    assert stub["written"]["insured_since"] == "2016-02-16"
    assert stub["written"]["current_insurer_since"] == "2024-02-16"


def test_a_typed_policy_date_is_never_overwritten(stub, monkeypatch):
    """ORDINARY FIELDS, so the blank-only rule holds. Only the four name
    fields are exempt (_SEEDED_NAME_FIELDS), because nobody typed those.
    """
    stub["state"]["driver"]["renewal_date"] = "2027-01-01"
    _report(monkeypatch, {"city": "SAMPLETOWN"})
    _policies(monkeypatch, {"renewal_date": "2026-02-16"})

    dash_apply.apply_report("d1", "# report")

    assert "renewal_date" not in stub["written"]


def test_a_report_with_no_policies_still_applies_the_driver(stub, monkeypatch):
    """The dates are a bonus; the driver's details are why the report was
    uploaded. An absent Policies section must not cost the broker those."""
    _report(monkeypatch, {"city": "SAMPLETOWN", "gender": "Male"})
    _policies(monkeypatch, {})

    dash_apply.apply_report("d1", "# report")

    assert stub["written"]["city"] == "SAMPLETOWN"
    assert "renewal_date" not in stub["written"]
