"""Tests for the Meta CAPI payload builders.

WHY THIS FILE EXISTS. Every function here fails SILENTLY. Meta accepts an
event with a wrongly-normalised hash and returns {"events_received": 1} — the
same response as a perfect one. The only symptom is match quality quietly
dropping, which nobody notices for weeks. There is no error to catch and no
log line to read, so a test is the only thing that can tell us.

Each hash assertion computes the expected digest INDEPENDENTLY from the rule
in the function's own docstring, rather than pasting in whatever the function
currently returns. A test written the other way round passes just as happily
against a bug.
"""

import hashlib
import logging

import pytest

import meta_capi


def sha(value: str) -> str:
    """The expected digest, derived from Meta's stated rule rather than from
    our implementation of it."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ── email ────────────────────────────────────────────────────────────────
class TestHashEmail:
    def test_lowercases_and_trims(self):
        assert meta_capi.hash_email("  Test.User@Example.COM  ") == sha(
            "test.user@example.com"
        )

    def test_does_not_strip_plus_tags_or_dots(self):
        # Meta's spec does not ask for it, so inventing that normalisation
        # would hash to something Meta never matches against.
        assert meta_capi.hash_email("a.b+tag@x.com") == sha("a.b+tag@x.com")

    @pytest.mark.parametrize("empty", [None, ""])
    def test_absent_input_is_omitted_not_hashed(self, empty):
        # "" must not become sha("") — a hash of nothing still looks like a
        # real identifier to Meta and can only ever fail to match.
        assert meta_capi.hash_email(empty) is None

    def test_whitespace_only_is_not_hashed_as_an_empty_string(self):
        """Regression. "   " is truthy, so guarding on the raw value alone
        let it through to be hashed as sha256("") and sent to Meta as though
        it were an address — the same meaningless digest for every such
        lead. Found by this suite; hash_email was the only hasher here
        missing the post-normalisation re-check."""
        assert meta_capi.hash_email("   ") is None

    def test_every_hasher_rejects_whitespace_only_input(self):
        """The rule this file exists to hold: a field that normalises to
        nothing must be OMITTED, never sent as the hash of an empty
        string."""
        empty_digest = sha("")
        for name, fn in [
            ("hash_email", meta_capi.hash_email),
            ("hash_phone", meta_capi.hash_phone),
            ("hash_city", meta_capi.hash_city),
            ("hash_postal_code", meta_capi.hash_postal_code),
            ("hash_name_part", meta_capi.hash_name_part),
        ]:
            assert fn("   ") != empty_digest, f"{name} hashed whitespace"


# ── phone ────────────────────────────────────────────────────────────────
class TestHashPhone:
    def test_strips_formatting_and_prepends_country_code(self):
        assert meta_capi.hash_phone("(416) 555-1234") == sha("14165551234")

    def test_number_already_carrying_country_code_is_not_doubled(self):
        assert meta_capi.hash_phone("1-416-555-1234") == sha("14165551234")

    def test_formatting_variants_agree(self):
        """The whole point of the normalisation: however a broker typed it,
        the same human must produce the same hash."""
        variants = ["4165551234", "416-555-1234", "(416) 555 1234", "+1 416 555 1234"]
        hashes = {meta_capi.hash_phone(v) for v in variants}
        assert len(hashes) == 1, f"variants disagreed: {hashes}"

    @pytest.mark.parametrize("junk", [None, "", "abc", "---"])
    def test_no_digits_yields_nothing(self, junk):
        assert meta_capi.hash_phone(junk) is None


# ── name ─────────────────────────────────────────────────────────────────
class TestHashNamePart:
    def test_strips_punctuation_so_obrien_matches(self):
        assert meta_capi.hash_name_part("O'Brien") == sha("obrien")
        assert meta_capi.hash_name_part("O'Brien") == meta_capi.hash_name_part("OBrien")

    def test_hyphenated_name(self):
        assert meta_capi.hash_name_part("Okonkwo-Fitzgerald") == sha(
            "okonkwofitzgerald"
        )

    def test_name_with_no_letters_yields_nothing(self):
        assert meta_capi.hash_name_part("123") is None
        assert meta_capi.hash_name_part("---") is None

    def test_accented_letters_are_kept(self):
        """Regression. The old [^a-zA-Z] filter deleted accents outright —
        "Núñez" hashed as "nez", "Müller" as "mller" — guaranteeing a
        mismatch against Meta's own copy of the name, and only for customers
        whose names carry accents. Meta asks for lowercase UTF-8 with
        punctuation stripped; accented letters are letters."""
        assert meta_capi.hash_name_part("Núñez") == sha("núñez")
        assert meta_capi.hash_name_part("Müller") == sha("müller")

    def test_digits_are_stripped(self):
        assert meta_capi.hash_name_part("John2") == sha("john")


class TestSplitName:
    def test_splits_on_last_space(self):
        assert meta_capi.split_name("Jean Paul Martin") == ("Jean Paul", "Martin")

    def test_single_word_is_first_name_only(self):
        assert meta_capi.split_name("Cher") == ("Cher", None)

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_empty_yields_no_parts(self, empty):
        assert meta_capi.split_name(empty) == (None, None)


# ── date of birth ────────────────────────────────────────────────────────
class TestHashDateOfBirth:
    def test_iso_string(self):
        assert meta_capi.hash_date_of_birth("1988-04-12") == sha("19880412")

    def test_date_object(self):
        import datetime

        assert meta_capi.hash_date_of_birth(datetime.date(1988, 4, 12)) == sha(
            "19880412"
        )

    def test_string_and_date_object_agree(self):
        import datetime

        assert meta_capi.hash_date_of_birth("1988-04-12") == meta_capi.hash_date_of_birth(
            datetime.date(1988, 4, 12)
        )

    def test_timestamp_string_is_truncated_to_the_date(self):
        assert meta_capi.hash_date_of_birth("1988-04-12T00:00:00+00:00") == sha(
            "19880412"
        )

    @pytest.mark.parametrize("bad", [None, "not-a-date", "1988-4-2"])
    def test_unparseable_yields_nothing(self, bad):
        assert meta_capi.hash_date_of_birth(bad) is None


# ── gender ───────────────────────────────────────────────────────────────
class TestHashGender:
    def test_maps_to_meta_single_letter(self):
        assert meta_capi.hash_gender("Male") == sha("m")
        assert meta_capi.hash_gender("Female") == sha("f")

    def test_other_is_omitted_rather_than_guessed(self):
        # driver.gender's check constraint allows 'Other'; Meta has no
        # equivalent, and a guess would actively mismatch a real person.
        assert meta_capi.hash_gender("Other") is None

    def test_matches_the_database_check_constraint_exactly(self):
        """migrations/0035_driver.sql constrains gender to Male/Female/Other,
        so those three are the only values that can reach this function."""
        assert meta_capi.hash_gender("Male") is not None
        assert meta_capi.hash_gender("Female") is not None
        assert meta_capi.hash_gender("Other") is None


# ── city / province / postal ─────────────────────────────────────────────
class TestHashCity:
    def test_removes_all_spaces_not_just_trims(self):
        # Meta's spec: "New York" -> "newyork". Trimming alone would hash
        # "new york" and never match.
        assert meta_capi.hash_city("New York") == sha("newyork")
        assert meta_capi.hash_city("  Richmond Hill  ") == sha("richmondhill")

    def test_removes_punctuation_too(self):
        """Meta, verbatim: "Lowercase only with no punctuation, no special
        characters, and no spaces." Spaces alone were stripped until
        2026-08-07, so every St./Ste. and hyphenated Ontario city hashed to
        something Meta could not match."""
        assert meta_capi.hash_city("St. Catharines") == sha("stcatharines")
        assert meta_capi.hash_city("Whitchurch-Stouffville") == sha("whitchurchstouffville")
        assert meta_capi.hash_city("Ste. Anne's") == sha("steannes")

    def test_accented_letters_are_kept(self):
        """No punctuation is not the same as ASCII-only — dropping the accent
        would change the name into a different string from the one Meta holds."""
        assert meta_capi.hash_city("Montréal") == sha("montréal")

    def test_a_city_of_only_punctuation_is_omitted(self):
        """Nothing left to match on. Absent beats present-but-empty, which
        Meta scores worse."""
        assert meta_capi.hash_city("...") is None


class TestHashProvince:
    def test_real_lookup_not_a_string_slice(self):
        # The bug this guards: "Quebec"[:2] is "qu", not "qc".
        assert meta_capi.hash_province("Quebec") == sha("qc")
        assert meta_capi.hash_province("Ontario") == sha("on")

    def test_case_and_whitespace_insensitive(self):
        assert meta_capi.hash_province("  ONTARIO ") == meta_capi.hash_province("Ontario")

    def test_every_mapped_province_is_two_letters(self):
        for name, code in meta_capi._PROVINCE_CODES.items():
            assert len(code) == 2, f"{name} -> {code!r} is not a 2-letter code"
            assert code.islower(), f"{name} -> {code!r} must be lowercase"

    def test_unknown_province_is_omitted(self):
        assert meta_capi.hash_province("Atlantis") is None


class TestHashPostalCode:
    def test_canadian_code_with_space(self):
        assert meta_capi.hash_postal_code("M5V 2T6") == sha("m5v2t6")

    def test_spaced_and_unspaced_agree(self):
        assert meta_capi.hash_postal_code("M5V 2T6") == meta_capi.hash_postal_code(
            "M5V2T6"
        )


# ── payload assembly ─────────────────────────────────────────────────────
class TestBuildUserData:
    LEAD = {
        "id": "11111111-2222-3333-4444-555555555555",
        "email": "test@example.com",
        "phone": "4165551234",
        "platform_leadgen_id": "1234567890123456",
        # CARRIES A NAME DELIBERATELY. Without it the driver=None tests below
        # would pass whether or not the lead.name fallback exists, which is
        # exactly how the missing fallback survived unnoticed.
        "name": "Jordan Fielding",
    }
    DRIVER = {
        "name": "Priya Raghunathan",
        "date_of_birth": "1988-04-12",
        "gender": "Female",
        "city": "Toronto",
        "province": "Ontario",
        "postal_code": "M5V 2T6",
    }

    def test_lead_id_is_the_one_field_sent_unhashed(self):
        """Meta's own 15-17 digit id is an EXACT match where every other
        field is probabilistic — hashing it would destroy that."""
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=None)
        assert ud["lead_id"] == "1234567890123456"

    def test_email_and_phone_are_arrays(self):
        # Meta's CRM payload example sends em/ph as lists.
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=None)
        assert isinstance(ud["em"], list)
        assert isinstance(ud["ph"], list)

    def test_absent_fields_are_omitted_entirely_not_sent_as_null(self):
        """Meta treats a present-but-empty field as worse than an absent
        one, so None must not survive into the payload."""
        ud = meta_capi.build_user_data(lead={"id": "x"}, driver=None)
        assert None not in ud.values()
        assert "em" not in ud
        assert "ph" not in ud

    def test_driver_none_is_not_an_error(self):
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=None)
        assert "db" not in ud and "ge" not in ud
        assert ud["em"]  # lead-sourced fields still present

    def test_full_payload_carries_every_matching_field(self):
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=self.DRIVER)
        for field in ("lead_id", "em", "ph", "fn", "ln", "db", "ge", "ct", "st", "zp"):
            assert field in ud, f"{field} missing — costs Event Match Quality"

    def test_country_is_constant_and_HASHED(self):
        """This test asserted the UNHASHED "ca" until 2026-08-07 — so the one
        thing that should have caught the mistake was holding it in place.
        Meta's customer information parameters page lists country among the
        hashing-required fields."""
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=None)
        assert ud["country"] == sha("ca")
        assert ud["country"] != "ca", "country must never travel in the clear"


class TestNameFallback:
    """fn/ln come from the driver when there is one, and from the LEAD when
    there is not.

    THE BUG THIS LOCKS DOWN: fn/ln used to be read only from the driver. A
    Lead signal is queued when the lead row is inserted (migration 0079) and
    sent by the drain up to 15 minutes later, so there is usually no driver
    yet — and only 4 of 35 sent Lead events carried a name, against 35 of 35
    carrying email and phone. Meta lists name among the four parameters that
    most improve matching, so every one of those events went out weaker than
    the data we already held.
    """

    LEAD = {"id": "abc", "email": "a@b.com", "phone": "4165551234", "name": "Jane Doe"}
    DRIVER = {"name": "Priya Raghunathan"}

    def test_lead_name_is_used_when_there_is_no_driver(self):
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=None)
        assert ud["fn"] == sha("jane")
        assert ud["ln"] == sha("doe")

    def test_driver_name_wins_over_lead_name(self):
        """The applicant the broker confirmed on the call, who may not even be
        the person who enquired."""
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=self.DRIVER)
        assert ud["fn"] == sha("priya")

    def test_driver_without_a_name_falls_back_to_the_lead(self):
        """A driver row exists but is half-filled — the lead's name is still
        better than nothing."""
        ud = meta_capi.build_user_data(lead=self.LEAD, driver={"date_of_birth": "1988-04-12"})
        assert ud["fn"] == sha("jane")

    def test_single_word_name_sends_first_only(self):
        """9 of 38 real leads carry one word. fn alone is correct; an empty ln
        would be worse than an absent one."""
        ud = meta_capi.build_user_data(lead={**self.LEAD, "name": "Syed"}, driver=None)
        assert ud["fn"] == sha("syed")
        assert "ln" not in ud

    def test_no_name_anywhere_omits_both(self):
        ud = meta_capi.build_user_data(lead={"id": "abc"}, driver=None)
        assert "fn" not in ud and "ln" not in ud

    def test_empty_string_name_omits_both(self):
        ud = meta_capi.build_user_data(lead={**self.LEAD, "name": ""}, driver=None)
        assert "fn" not in ud and "ln" not in ud

    def test_a_whitespace_only_driver_name_falls_back_to_the_lead(self):
        """A whitespace string is TRUTHY, so " " would win the `or` against a
        perfectly good lead name and then hash to nothing — losing the name
        entirely rather than falling back to the one we hold."""
        ud = meta_capi.build_user_data(lead=self.LEAD, driver={"name": "   "})
        assert ud["fn"] == sha("jane")
        assert ud["ln"] == sha("doe")

    def test_a_whitespace_only_lead_name_omits_both(self):
        ud = meta_capi.build_user_data(lead={**self.LEAD, "name": "  	 "}, driver=None)
        assert "fn" not in ud and "ln" not in ud


class TestEventContracts:
    """What each of the three events must actually carry.

    ONE TEST PER BUSINESS RULE, so a future change cannot quietly thin a
    payload. Asserted as a MINIMUM rather than an exact set: a Lead sent after
    a broker has opened the lead legitimately carries the driver's fields too,
    and an equality assertion would fail on a correct payload.
    """

    LEAD = {
        "id": "11111111-2222-3333-4444-555555555555",
        "email": "test@example.com",
        "phone": "4165551234",
        "platform_leadgen_id": "1234567890123456",
        "name": "Jane Doe",
    }
    FULL_DRIVER = {
        "name": "Jane Doe",
        "date_of_birth": "1988-04-12",
        "gender": "Female",
        "city": "Toronto",
        "province": "Ontario",
        "postal_code": "M5V 2T6",
    }
    BASIC = ("lead_id", "em", "ph", "fn", "ln", "external_id", "country")
    RICH = BASIC + ("db", "ge", "ct", "st", "zp")

    def test_lead_carries_at_least_the_basics_with_no_driver(self):
        """The event fires at lead creation, so the driver is usually absent —
        but the basics must always be there."""
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=None)
        for field in self.BASIC:
            assert field in ud, f"Lead is missing {field}"

    def test_complete_registration_carries_every_matching_field(self):
        """By Quote Done the applicant has been filled in, so this event has no
        excuse for a thin payload."""
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=self.FULL_DRIVER)
        for field in self.RICH:
            assert field in ud, f"CompleteRegistration is missing {field}"

    def test_purchase_carries_the_same_fields_plus_value_and_currency(self):
        """value/currency are REQUIRED by Meta on a valued event — without them the
        sale teaches Meta's optimisation nothing about revenue."""
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=self.FULL_DRIVER)
        for field in self.RICH:
            assert field in ud, f"the event is missing {field}"
        cd = meta_capi.build_custom_data(total_new_premium=1200.0)
        assert cd["value"] == 1200.0
        assert cd["currency"] == "CAD"


class TestSendQuality:
    """The send-quality report: what our sent events actually carried.

    Reads ONLY the stored payload. An earlier design compared each payload
    against the CRM's CURRENT lead and driver rows to report "held but not
    sent" — dropped after measuring that 32 of 35 driver rows were updated
    AFTER their event was sent, which would have reported later edits as
    sending gaps.
    """

    def _rows(self, monkeypatch, rows):
        class _Result:
            data = rows

        monkeypatch.setattr(
            _meta_signal_service.repo, "list_sent_payloads_between",
            lambda start, end: _Result(),
        )
        return _meta_signal_service.send_quality(days=14)

    def test_a_field_absent_from_every_event_still_reports_zero(self, monkeypatch):
        """The zero IS the finding. A field that vanishes from the table
        instead of reading 0/N is the gap you cannot see."""
        out = self._rows(monkeypatch, [
            {"id": "1", "event_name": "Lead", "sent_at": "2026-08-20T00:00:00Z",
             "payload": {"user_data": {"em": "h"}}},
        ])
        coverage = out["events"][0]["coverage"]
        assert coverage["db"] == {"sent": 0, "total": 1}
        assert coverage["fn"] == {"sent": 0, "total": 1}

    def test_separate_denominators_per_event(self, monkeypatch):
        out = self._rows(monkeypatch, [
            {"id": "1", "event_name": "Lead", "sent_at": "2026-08-20T00:00:00Z",
             "payload": {"user_data": {"em": "h"}}},
            {"id": "2", "event_name": "Lead", "sent_at": "2026-08-20T00:00:00Z",
             "payload": {"user_data": {"em": "h"}}},
            {"id": "3", "event_name": "CompleteRegistration",
             "sent_at": "2026-08-20T00:00:00Z",
             "payload": {"user_data": {"em": "h", "fn": "h"}}},
        ])
        by_name = {e["event_name"]: e for e in out["events"]}
        assert by_name["Lead"]["total"] == 2
        assert by_name["CompleteRegistration"]["total"] == 1
        assert by_name["CompleteRegistration"]["coverage"]["fn"] == {"sent": 1, "total": 1}

    def test_an_empty_value_is_not_coverage(self, monkeypatch):
        """Meta scores a present-but-empty field worse than an absent one, so
        `in` would over-report. Counted with .get()."""
        out = self._rows(monkeypatch, [
            {"id": "1", "event_name": "Lead", "sent_at": "2026-08-20T00:00:00Z",
             "payload": {"user_data": {"em": "h", "fn": "", "ln": None}}},
        ])
        coverage = out["events"][0]["coverage"]
        assert coverage["fn"]["sent"] == 0
        assert coverage["ln"]["sent"] == 0

    def test_a_payload_with_no_dataset_reads_as_not_recorded(self, monkeypatch):
        """Rows sent before the dataset was stored. A real state, and it must
        not read as a mismatch."""
        out = self._rows(monkeypatch, [
            {"id": "1", "event_name": "Lead", "sent_at": "2026-08-20T00:00:00Z",
             "payload": {"user_data": {"em": "h"}}},
        ])
        assert out["datasets"] == {"not recorded": 1}

    def test_the_dataset_is_reported_when_present(self, monkeypatch):
        out = self._rows(monkeypatch, [
            {"id": "1", "event_name": "Lead", "sent_at": "2026-08-20T00:00:00Z",
             "payload": {"dataset_id": "ds-9", "user_data": {"em": "h"}}},
        ])
        assert out["datasets"] == {"ds-9": 1}

    def test_a_malformed_payload_does_not_break_the_report(self, monkeypatch):
        """One bad row must not blank the whole panel."""
        out = self._rows(monkeypatch, [
            {"id": "1", "event_name": "Lead", "sent_at": "2026-08-20T00:00:00Z",
             "payload": None},
            {"id": "2", "event_name": "Lead", "sent_at": "2026-08-20T00:00:00Z",
             "payload": {"user_data": "not-a-dict"}},
            {"id": "3", "event_name": "Lead", "sent_at": "2026-08-20T00:00:00Z",
             "payload": {"user_data": {"em": "h"}}},
        ])
        assert out["events"][0]["total"] == 3
        assert out["events"][0]["coverage"]["em"]["sent"] == 1

    def test_an_empty_window_returns_cleanly(self, monkeypatch):
        """No rows must not divide by zero."""
        out = self._rows(monkeypatch, [])
        assert out["events"] == []
        assert out["datasets"] == {}
        assert out["warnings"] == []
        # The field vocabulary is a constant, not derived from the rows, so it
        # is present even with nothing to report — the panel needs it to lay
        # out an empty table rather than render nothing.
        assert [f["field"] for f in out["fields"]] == list(
            _meta_signal_service._REPORTED_FIELDS
        )

    def test_identical_warnings_are_grouped(self, monkeypatch):
        """The same warning on forty events is one problem, not forty."""
        message = "Invalid parameter: em could not be matched"
        out = self._rows(monkeypatch, [
            {"id": "1", "event_name": "Lead", "sent_at": "2026-08-19T00:00:00Z",
             "payload": {"user_data": {}, "response": {"messages": [message]}}},
            {"id": "2", "event_name": "Lead", "sent_at": "2026-08-20T00:00:00Z",
             "payload": {"user_data": {}, "response": {"messages": [message]}}},
        ])
        assert out["warnings"] == [
            {"message": message, "count": 2, "last_seen": "2026-08-20T00:00:00Z"}
        ]

    def test_a_non_string_warning_is_coerced_not_crashed(self, monkeypatch):
        """Meta has returned both strings and objects here."""
        out = self._rows(monkeypatch, [
            {"id": "1", "event_name": "Lead", "sent_at": "2026-08-20T00:00:00Z",
             "payload": {"user_data": {}, "response": {"messages": [{"code": 2804003}]}}},
        ])
        assert out["warnings"][0]["count"] == 1
        assert "2804003" in out["warnings"][0]["message"]

    def test_no_hashed_value_is_ever_returned(self, monkeypatch):
        """The payload holds hashes; this endpoint reports field NAMES and
        counts, so nothing identifying can leak through it."""
        import json as _json

        secret = "a" * 64
        out = self._rows(monkeypatch, [
            {"id": "1", "event_name": "Lead", "sent_at": "2026-08-20T00:00:00Z",
             "payload": {"user_data": {"em": secret, "fn": secret}}},
        ])
        assert secret not in _json.dumps(out)


class TestBuildCustomData:
    def test_crm_fields_present_on_every_event_even_without_a_value(self):
        """The bug this locks down: custom_data used to return {} when there
        was no premium, and send_event drops an empty dict — so Lead and
        CompleteRegistration reached Meta with neither required CRM field."""
        cd = meta_capi.build_custom_data(total_new_premium=None)
        assert cd["event_source"] == "crm"
        assert cd["lead_event_source"] == "MyDay CRM"

    def test_purchase_adds_value_and_currency(self):
        cd = meta_capi.build_custom_data(total_new_premium=2450.0)
        assert cd["value"] == 2450.0
        assert cd["currency"] == "CAD"
        assert cd["event_source"] == "crm"

    def test_is_always_truthy_so_callers_must_not_test_it_for_emptiness(self):
        """A REGRESSION GUARD FOR A CALLER, not for this function.

        services/meta_signal.py used `if not custom_data:` to decide whether a
        the event had a premium. That worked while this function returned {}
        for a missing premium — and stopped working the moment it began
        seeding event_source and lead_event_source unconditionally, which
        Meta's CRM integration requires on every event. A non-empty dict is
        truthy, so the branch became unreachable and every premium-less
        the event went to Meta with no value: accepted, and silently useless
        for optimisation.

        The caller now tests the premium itself. This asserts the property
        that made the old check wrong, so anyone reintroducing an emptiness
        test here fails rather than ships the same silent bug."""
        assert meta_capi.build_custom_data(total_new_premium=None)
        assert meta_capi.build_custom_data(total_new_premium=0)
        assert meta_capi.build_custom_data(total_new_premium=2450.0)

    def test_non_purchase_omits_value(self):
        cd = meta_capi.build_custom_data(total_new_premium=None)
        assert "value" not in cd and "currency" not in cd


class TestActionSource:
    def test_defaults_to_the_value_metas_crm_guide_requires(self):
        """Meta's setup guide: "Action_source field should always be
        'system_generated'." An env-var default that is wrong ships a subtly
        broken integration to any environment that sets nothing."""
        assert meta_capi.ACTION_SOURCE == "system_generated"


class TestGraphApiVersion:
    """The version is read from META_GRAPH_API_VERSION and validated at
    import, so every case here reloads the module under a patched env — the
    check cannot be exercised any other way.

    WHY THESE EXIST: the version used to be written out in meta_graph.py AND
    meta_capi.py, the second carrying a comment asking whoever edited one to
    edit the other. Nothing enforced that. The first test below is the
    enforcement that comment was asking a human for.
    """

    def _reload(self, monkeypatch, value, *, keep_overrides=False):
        """Import both modules fresh with META_GRAPH_API_VERSION set.

        Both BASE_OVERRIDEs are cleared unless a test asks to keep them. A
        developer's own .env sets GRAPH_API_BASE_OVERRIDE to point at the
        meta-lead-simulator, and an override replaces the whole base — so
        without this these tests measure whatever that machine happens to
        have configured rather than the version. Found exactly that way:
        they passed alone and failed in the suite.
        """
        import importlib

        import meta_graph

        if not keep_overrides:
            # All FOUR names — the current pair and the previous pair. Missing
            # one here means the test measures whatever the developer's own
            # .env happens to set rather than the thing under test.
            monkeypatch.delenv("META_GRAPH_API_BASE", raising=False)
            monkeypatch.delenv("META_CAPI_BASE", raising=False)
            monkeypatch.delenv("GRAPH_API_BASE_OVERRIDE", raising=False)
            monkeypatch.delenv("META_CAPI_BASE_OVERRIDE", raising=False)

        if value is None:
            monkeypatch.delenv("META_GRAPH_API_VERSION", raising=False)
        else:
            monkeypatch.setenv("META_GRAPH_API_VERSION", value)
        # meta_capi imports the constant FROM meta_graph, so meta_graph has to
        # be reloaded first or the second reload re-imports the stale value.
        graph = importlib.reload(meta_graph)
        capi = importlib.reload(meta_capi)
        return graph, capi

    def test_both_modules_report_the_same_version(self, monkeypatch):
        """The drift the old "keep the two in step" comment asked a human to
        prevent. Meta versions both directions of its API together, so a build
        fetching leads on one version and reporting conversions on another is
        a mistake, not a configuration."""
        graph, capi = self._reload(monkeypatch, "v23.0")
        assert graph.GRAPH_API_VERSION == capi.GRAPH_API_VERSION == "v23.0"

    def test_both_bases_move_together(self, monkeypatch):
        graph, capi = self._reload(monkeypatch, "v23.0")
        assert graph.GRAPH_API_BASE == "https://graph.facebook.com/v23.0"
        assert capi.CAPI_API_BASE == "https://graph.facebook.com/v23.0"

    def test_unset_is_byte_for_byte_what_it_always_was(self, monkeypatch):
        """The change must be invisible in every environment that sets
        nothing — which is all of them today, including production."""
        graph, capi = self._reload(monkeypatch, None)
        assert graph.GRAPH_API_VERSION == "v21.0"
        assert graph.GRAPH_API_BASE == "https://graph.facebook.com/v21.0"
        assert capi.CAPI_API_BASE == "https://graph.facebook.com/v21.0"

    @pytest.mark.parametrize("bad", ["21.0", "v21", "", "latest", "v21.0.1"])
    def test_a_malformed_version_stops_the_app(self, monkeypatch, bad):
        """Fails at IMPORT, not on the next lead. Unvalidated, "21.0" builds
        https://graph.facebook.com/21.0/... — which Meta answers with a 404
        that surfaces hours later as a lead that never arrived, in someone
        else's error message."""
        with pytest.raises(ValueError, match="META_GRAPH_API_VERSION"):
            self._reload(monkeypatch, bad)

    def test_the_simulator_override_still_wins(self, monkeypatch):
        """GRAPH_API_BASE_OVERRIDE replaces the whole base, version included —
        it points at the meta-lead-simulator stub. Unrelated to the version,
        and this change must not have coupled them."""
        monkeypatch.setenv("GRAPH_API_BASE_OVERRIDE", "http://127.0.0.1:9999")
        monkeypatch.delenv("META_CAPI_BASE_OVERRIDE", raising=False)
        graph, capi = self._reload(monkeypatch, "v23.0", keep_overrides=True)
        assert graph.GRAPH_API_BASE == "http://127.0.0.1:9999"
        # CAPI has its own override and must NOT follow that one.
        assert capi.CAPI_API_BASE == "https://graph.facebook.com/v23.0"


class TestApiBaseFromEnvironment:
    """The full URL comes from the environment.

    META_GRAPH_API_BASE / META_CAPI_BASE are the current names;
    GRAPH_API_BASE_OVERRIDE / META_CAPI_BASE_OVERRIDE are the previous ones,
    still read so a rename cannot break a developer's gitignored .env or the
    meta-lead-simulator setup it drives.
    """

    def _reload(self, monkeypatch, **env):
        import importlib

        import meta_graph

        for name in (
            "META_GRAPH_API_BASE",
            "META_CAPI_BASE",
            "GRAPH_API_BASE_OVERRIDE",
            "META_CAPI_BASE_OVERRIDE",
            "META_GRAPH_API_VERSION",
        ):
            monkeypatch.delenv(name, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        graph = importlib.reload(meta_graph)
        return graph, importlib.reload(meta_capi)

    def test_nothing_set_is_what_it_always_was(self, monkeypatch):
        graph, capi = self._reload(monkeypatch)
        assert graph.GRAPH_API_BASE == "https://graph.facebook.com/v21.0"
        assert capi.CAPI_API_BASE == "https://graph.facebook.com/v21.0"

    def test_full_url_is_used_verbatim(self, monkeypatch):
        """Used as given — the version is part of the URL, not appended."""
        graph, _ = self._reload(
            monkeypatch, META_GRAPH_API_BASE="https://graph.facebook.com/v23.0"
        )
        assert graph.GRAPH_API_BASE == "https://graph.facebook.com/v23.0"

    def test_a_full_url_beats_the_version(self, monkeypatch):
        """Setting a base makes META_GRAPH_API_VERSION irrelevant for it."""
        graph, _ = self._reload(
            monkeypatch,
            META_GRAPH_API_BASE="https://example.test/v23.0",
            META_GRAPH_API_VERSION="v21.0",
        )
        assert graph.GRAPH_API_BASE == "https://example.test/v23.0"

    def test_the_old_name_still_works(self, monkeypatch):
        """THE MIGRATION GUARANTEE. Developers' .env files are gitignored, so
        no rename can reach them — dropping this would stop the simulator
        working with no error to say why."""
        graph, _ = self._reload(
            monkeypatch, GRAPH_API_BASE_OVERRIDE="http://127.0.0.1:9090/v21.0"
        )
        assert graph.GRAPH_API_BASE == "http://127.0.0.1:9090/v21.0"

        _, capi = self._reload(
            monkeypatch, META_CAPI_BASE_OVERRIDE="http://127.0.0.1:9091/v21.0"
        )
        assert capi.CAPI_API_BASE == "http://127.0.0.1:9091/v21.0"

    def test_the_new_name_wins_over_the_old(self, monkeypatch):
        graph, _ = self._reload(
            monkeypatch,
            META_GRAPH_API_BASE="https://new.test/v23.0",
            GRAPH_API_BASE_OVERRIDE="https://old.test/v21.0",
        )
        assert graph.GRAPH_API_BASE == "https://new.test/v23.0"

    def test_the_two_directions_stay_independent(self, monkeypatch):
        """Sharing one variable once redirected live conversion events to a
        dev stub, silently. Setting the graph base must never move CAPI."""
        graph, capi = self._reload(
            monkeypatch, META_GRAPH_API_BASE="http://127.0.0.1:9090/v21.0"
        )
        assert graph.GRAPH_API_BASE == "http://127.0.0.1:9090/v21.0"
        assert capi.CAPI_API_BASE == "https://graph.facebook.com/v21.0"

    def test_mismatched_versions_warn_but_still_start(self, monkeypatch, caplog):
        """A warning, not an error: pointing graph at a simulator while CAPI
        stays on real Meta is the normal dev setup."""
        with caplog.at_level("WARNING", logger="myday.meta"):
            self._reload(
                monkeypatch,
                META_GRAPH_API_BASE="https://graph.facebook.com/v21.0",
                META_CAPI_BASE="https://graph.facebook.com/v23.0",
            )
        assert "versions differ" in caplog.text

    def test_matching_versions_do_not_warn(self, monkeypatch, caplog):
        """The real dev setup: different HOSTS, same version."""
        with caplog.at_level("WARNING", logger="myday.meta"):
            self._reload(
                monkeypatch, META_GRAPH_API_BASE="http://127.0.0.1:9090/v21.0"
            )
        assert "versions differ" not in caplog.text

    def test_a_base_with_no_version_is_silent(self, monkeypatch, caplog):
        """A bare simulator host has no version to compare, so comparing is
        not possible — and guessing would produce a warning on every run."""
        with caplog.at_level("WARNING", logger="myday.meta"):
            self._reload(monkeypatch, META_GRAPH_API_BASE="http://localhost:9090")
        assert "versions differ" not in caplog.text


# ── the credential must never reach a log ────────────────────────────────
class TestTokenIsNotLogged:
    """THE ONE TEST HERE THAT IS ABOUT SECURITY, not match quality.

    send_event used to pass the token as ?access_token=, and httpx logs every
    request URL at INFO — which main.py turns on globally. Every conversion
    event therefore wrote the Meta system user token, in cleartext, into
    Railway's retained log store. Nothing failed and nothing looked wrong; the
    events sent perfectly.

    That is exactly the shape of bug this file exists for, so the assertion
    lives here: the token must not appear in the URL, and must not appear in
    anything logged while sending.
    """

    TOKEN = "EAAG_system_user_token_value_that_must_never_be_logged"

    def _send(self, monkeypatch, capture):
        """Call the real send_event against a captured request, so this tests
        the transport as written rather than a re-description of it."""
        import httpx

        def fake_post(url, **kwargs):
            capture["url"] = url
            capture["kwargs"] = kwargs
            # Log the way httpx itself does, at the level main.py enables —
            # asserting on our own recorded URL alone would not prove what
            # reaches the log.
            logging.getLogger("httpx").info(
                'HTTP Request: POST %s "HTTP/1.1 200 OK"',
                httpx.URL(url, params=kwargs.get("params")),
            )
            return httpx.Response(
                200, json={"events_received": 1}, request=httpx.Request("POST", url)
            )

        monkeypatch.setattr(meta_capi.httpx, "post", fake_post)
        return meta_capi.send_event(
            dataset_id="1234567890",
            access_token=self.TOKEN,
            event_name="Lead",
            event_time=1,
            event_id="signal-1",
            user_data={"country": "ca"},
        )

    def test_token_is_not_in_the_url(self, monkeypatch):
        capture: dict = {}
        self._send(monkeypatch, capture)
        assert self.TOKEN not in capture["url"]
        # params carried nothing but the token, so it should be gone entirely
        # rather than emptied — a leftover params={} invites re-adding to it.
        assert not capture["kwargs"].get("params")

    def test_token_travels_in_the_authorization_header(self, monkeypatch):
        capture: dict = {}
        self._send(monkeypatch, capture)
        assert capture["kwargs"]["headers"]["Authorization"] == f"Bearer {self.TOKEN}"

    def test_nothing_logged_while_sending_contains_the_token(
        self, monkeypatch, caplog
    ):
        """The assertion that would have caught this. Fails against the old
        query-param version."""
        with caplog.at_level(logging.INFO):
            self._send(monkeypatch, {})
        assert caplog.text, "nothing was logged — the test would pass vacuously"
        assert self.TOKEN not in caplog.text

    def test_the_event_still_sends(self, monkeypatch):
        assert self._send(monkeypatch, {}) == {"events_received": 1}


# ── what Meta said back ──────────────────────────────────────────────────
# These reach into services/meta_signal, which imports repositories -> db.py.
# db.py calls load_dotenv() and then reads SUPABASE_* at import, so it works on
# a developer machine with backend/.env and raises without one. Guarded the
# same way tests/test_lead_assignment.py guards itself, so a credential-less
# checkout SKIPS these rather than failing collection.
_HAS_DB_ENV = True
try:  # noqa: SIM105
    from services import meta_signal as _meta_signal_service
except Exception:  # pragma: no cover - only on a checkout with no .env
    _HAS_DB_ENV = False


@pytest.mark.skipif(not _HAS_DB_ENV, reason="services.meta_signal needs SUPABASE_* at import")
class TestMetaResponseIsKept:
    """Meta's reply to a SUCCESSFUL send is worth keeping.

    migrations/0056 says why, in the meta_signal table's own header: a hashing
    or normalisation mistake is invisible, because Meta accepts the event,
    matches nobody, and still reports success. The reply is where that shows
    up — `messages` carries warnings on an ACCEPTED event, and `fbtrace_id` is
    the id Meta support asks for.

    Until 2026-08-07 send_event's return value was discarded at the call site,
    so a warned-but-accepted event was recorded as a clean success with
    nothing to show for it.
    """

    RESPONSE = {
        "events_received": 1,
        "messages": [],
        "fbtrace_id": "AbC123trace",
    }

    def _run(self, monkeypatch, response, event_name="CompleteRegistration"):
        """Drive the real _send_one against stubbed edges, and hand back what
        was written to meta_signal. Only the boundaries are faked — the
        function under test runs as written."""
        marked: dict = {}

        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(_meta_signal_service, "_get_principal_driver", lambda _id: None)
        monkeypatch.setattr(_meta_signal_service.meta_capi, "send_event",
                            lambda **kwargs: response)
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_sent",
            lambda signal_id, payload: marked.update(id=signal_id, payload=payload),
        )
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")

        row = {
            "id": "signal-1",
            "lead_id": "lead-1",
            "event_name": event_name,
            "attempt_count": 0,
            "created_at": "2026-08-07T12:00:00+00:00",
            "payload": {"total_new_premium_at_registration": 1200},
        }
        outcome = _meta_signal_service._send_one(row)
        return outcome, marked

    def test_the_trace_id_is_stored(self, monkeypatch):
        """fbtrace_id is the handle Meta support needs for a disputed event."""
        outcome, marked = self._run(monkeypatch, self.RESPONSE)
        assert outcome == "sent"
        assert marked["payload"]["response"]["fbtrace_id"] == "AbC123trace"

    def test_a_warning_on_an_accepted_event_survives(self, monkeypatch):
        """THE CASE THIS EXISTS FOR. Meta returns events_received: 1 AND a
        warning that the user data was unusable. Accepted, matched to nobody,
        and previously recorded as an unqualified success."""
        warned = {
            **self.RESPONSE,
            "messages": ["Invalid parameter: em could not be matched"],
        }
        _, marked = self._run(monkeypatch, warned)
        assert marked["payload"]["response"]["messages"] == [
            "Invalid parameter: em could not be matched"
        ]

    def test_what_we_sent_is_still_stored(self, monkeypatch):
        """Guard rail: adding the reply must not displace the request. Both
        halves of the conversation are needed to debug a match failure."""
        _, marked = self._run(monkeypatch, self.RESPONSE)
        assert "user_data" in marked["payload"]
        assert marked["payload"]["user_data"], "user_data must not be empty"

    def test_the_queued_payload_survives_the_send(self, monkeypatch):
        """THE QUEUE-TIME SNAPSHOT MUST NOT BE ERASED BY SUCCESS. A Purchase is
        queued with total_new_premium_at_purchase (0133); mark_sent used to
        REPLACE the payload with the send record, wiping it — so a Sent
        Purchase could never show its amount on the Meta Quality screen.
        Found live on staging 2026-10-25; this pins the merge."""
        _, marked = self._run(monkeypatch, self.RESPONSE)
        # The _run row is queued with a payload key; it must still be there
        # BESIDE the send record, not displaced by it.
        assert marked["payload"]["total_new_premium_at_registration"] == 1200
        assert "response" in marked["payload"]
        assert "user_data" in marked["payload"]

    def test_a_purchase_snapshot_survives_a_successful_send(self, monkeypatch):
        """THE SPECIFIC BUG d859b35 FIXED, pinned on a Purchase (the merge test
        above uses CompleteRegistration). The $1,800 queued at the sale must
        still be on the row after it is marked Sent, beside the send record,
        and an old send record on the row is replaced, not kept."""
        marked: dict = {}
        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(_meta_signal_service, "_get_principal_driver", lambda _id: None)
        monkeypatch.setattr(_meta_signal_service.meta_capi, "send_event",
                            lambda **kwargs: self.RESPONSE)
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_sent",
            lambda signal_id, payload: marked.update(id=signal_id, payload=payload),
        )
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")

        outcome = _meta_signal_service._send_one({
            "id": "signal-p", "lead_id": "lead-1", "event_name": "Purchase",
            "attempt_count": 0, "created_at": "2026-10-25T12:00:00+00:00",
            "payload": {"total_new_premium_at_purchase": 1800,
                        "response": {"stale": True}},
        })
        assert outcome == "sent"
        assert marked["id"] == "signal-p"
        assert marked["payload"]["total_new_premium_at_purchase"] == 1800
        assert marked["payload"]["custom_data"]["value"] == 1800.0
        assert marked["payload"]["dataset_id"] == "ds-1"
        assert marked["payload"]["response"] == self.RESPONSE, "the old send record must be replaced"

    def test_a_non_dict_payload_on_a_purchase_fails_cleanly(self, monkeypatch):
        """A hand-edited row whose payload is a list must fail as 'no premium',
        never raise out of the sender."""
        failed: list = []
        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(_meta_signal_service, "_get_principal_driver", lambda _id: None)
        def _must_not_send(**kwargs):
            raise AssertionError("must not POST a Purchase with no premium")
        monkeypatch.setattr(_meta_signal_service.meta_capi, "send_event", _must_not_send)
        monkeypatch.setattr(_meta_signal_service.repo, "mark_attempt_failed",
                            lambda *a, **k: failed.append((a, k)))
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")
        outcome = _meta_signal_service._send_one({
            "id": "signal-x", "lead_id": "lead-1", "event_name": "Purchase",
            "attempt_count": 0, "created_at": "2026-10-25T12:00:00+00:00",
            "payload": ["not", "a", "dict"],
        })
        assert outcome == "failed" and failed and failed[0][1]["terminal"] is True

    def test_the_dataset_it_went_to_is_stored(self, monkeypatch):
        """WHICH DATASET RECEIVED IT — the first question anyone asks when
        Events Manager disagrees with us, and previously unanswerable: the id
        is read from the environment at send time, so a stored row proved only
        that SOMETHING accepted the event. Diagnosing a reporting gap meant
        checking Railway's variables by hand and trusting they had not changed
        since."""
        _, marked = self._run(monkeypatch, self.RESPONSE)
        assert marked["payload"]["dataset_id"] == "ds-1"

    def test_a_registration_with_no_premium_is_sent(self, monkeypatch):
        """REVERSED TWICE, and the history is the argument for where it landed.

        0100 flipped it from 'skipped' to 'failed'. Skipping left the row Pending
        with attempt_count untouched, so the drain retried it every 15 minutes
        forever, drifting past Meta's 7-day event_time window — harmless only
        because it lived on the Purchase branch and Purchase never fired once.
        Terminal was the right correction at the time: the event fired at Quote
        Done, where services/lead.py's premium guard runs first, so arriving here
        without a figure meant something had bypassed it and deserved a visible
        Failed row.

        0115/0116 flips it again, to 'sent'. CompleteRegistration now fires at IN
        PROGRESS, where there is usually no quote — that is the whole reason for
        firing earlier. A premium-less event stopped being an anomaly and became
        the common case, so failing it would terminally fail most new signals.

        The constant through all three is that the row must never sit Pending
        forever. Skipped was wrong, failed was right, sent is right now — and
        _STAGES_NEEDING_PREMIUM still refuses the SALE stages without a figure,
        at the stage move where a broker can actually be told. Quote Done came
        out of that set when CompleteRegistration stopped carrying money — the
        premium now rides on Purchase, which only the sale stages fire.
        """
        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(_meta_signal_service, "_get_principal_driver", lambda _id: None)

        sent: dict = {}
        marked: dict = {}
        monkeypatch.setattr(
            _meta_signal_service.meta_capi, "send_event",
            lambda **kwargs: sent.update(kwargs) or {"events_received": 1},
        )
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_sent", lambda signal_id, payload: None
        )
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_attempt_failed",
            lambda _id, msg, attempts, terminal=False, **k: marked.update(
                message=msg, attempts=attempts, terminal=terminal),
        )
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")

        outcome = _meta_signal_service._send_one({
            "id": "signal-1", "lead_id": "lead-1",
            "event_name": "CompleteRegistration",
            "attempt_count": 0, "created_at": "2026-08-07T12:00:00+00:00",
            "payload": None,
        })
        assert outcome == "sent"
        assert not marked, f"a premium-less event must not be failed: {marked}"
        assert "value" not in sent["custom_data"]
        assert "currency" not in sent["custom_data"]

    def test_a_throttle_never_expires_however_many_attempts(self, monkeypatch):
        """A THROTTLE IS NOT A VERDICT ON THE EVENT.

        Both handlers in _send_one used to run the same
        `attempts >= MAX_ATTEMPTS` check over every failure, so three throttled
        passes turned a perfectly valid conversion terminal — and code 4/17/32
        was worse than that: it arrives as MetaCapiError, which was terminal on
        the FIRST attempt, no retry at all.

        attempt_count is 99 here, far past MAX_ATTEMPTS, precisely so passing
        this cannot be an accident of the ceiling not having been reached.
        """
        for code in (4, 17, 32):
            failed: dict = {}

            def reject(**kwargs):
                raise _meta_signal_service.meta_capi.MetaCapiError(
                    "Application request limit reached", code=code
                )

            monkeypatch.setattr(
                _meta_signal_service, "_get_lead",
                lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                             "platform_leadgen_id": "lg-1"},
            )
            monkeypatch.setattr(
                _meta_signal_service, "_get_principal_driver", lambda _id: None
            )
            monkeypatch.setattr(_meta_signal_service.meta_capi, "send_event", reject)
            monkeypatch.setattr(
                _meta_signal_service.repo, "mark_attempt_failed",
                lambda sid, msg, attempts, *, terminal, **kw: failed.update(
                    terminal=terminal, attempts=attempts, code=kw.get("error_code")
                ),
            )
            monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
            monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")

            _meta_signal_service._send_one({
                "id": "signal-1", "lead_id": "lead-1",
                "event_name": "CompleteRegistration",
                "attempt_count": 99, "created_at": "2026-08-07T12:00:00+00:00",
                "payload": {"total_new_premium_at_registration": 1200},
            })

            assert failed["terminal"] is False, f"code {code} must stay retryable"
            assert failed["code"] == code, "the code still reaches the admin screen"
            assert failed["attempts"] == 100, "attempts are still counted"

    def test_an_http_429_never_expires_either(self, monkeypatch):
        """THE SAME RULE ARRIVING BY THE OTHER ROUTE.

        429 is not 400/401, so meta_capi.py never raises MetaCapiError for it —
        it falls through to raise_for_status() and surfaces as
        httpx.HTTPStatusError. Classified by status here, by error code above,
        and both must reach the same branch or one throttle expires while the
        other does not.
        """
        class _FakeResponse:
            status_code = 429

        class _FakeStatusError(Exception):
            response = _FakeResponse()

        failed: dict = {}
        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(
            _meta_signal_service, "_get_principal_driver", lambda _id: None
        )

        def _throttled(**kwargs):
            raise _FakeStatusError("Too Many Requests")

        monkeypatch.setattr(_meta_signal_service.meta_capi, "send_event", _throttled)
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_attempt_failed",
            lambda sid, msg, attempts, *, terminal, **kw: failed.update(
                terminal=terminal, attempts=attempts
            ),
        )
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")

        _meta_signal_service._send_one({
            "id": "signal-1", "lead_id": "lead-1", "event_name": "Lead",
            "attempt_count": 99, "created_at": "2026-08-07T12:00:00+00:00",
            "payload": None,
        })

        assert failed["terminal"] is False
        assert failed["attempts"] == 100

    def test_a_network_failure_still_expires(self, monkeypatch):
        """THE CONTRAST THAT MAKES THE ABOVE MEAN SOMETHING.

        If every failure became non-terminal the throttle tests would pass and
        the system would retry a genuinely unreachable endpoint forever with
        nobody told — the invisible-failure pattern the whole change is meant to
        remove. A plain network error keeps the MAX_ATTEMPTS ceiling.
        """
        failed: dict = {}
        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(
            _meta_signal_service, "_get_principal_driver", lambda _id: None
        )

        def _unreachable(**kwargs):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(_meta_signal_service.meta_capi, "send_event", _unreachable)
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_attempt_failed",
            lambda sid, msg, attempts, *, terminal, **kw: failed.update(
                terminal=terminal
            ),
        )
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")

        _meta_signal_service._send_one({
            "id": "signal-1", "lead_id": "lead-1", "event_name": "Lead",
            "attempt_count": _meta_signal_service.MAX_ATTEMPTS - 1,
            "created_at": "2026-08-07T12:00:00+00:00", "payload": None,
        })

        assert failed["terminal"] is True

    def test_a_dead_token_still_expires_immediately(self, monkeypatch):
        """THE OTHER CONTRAST. Code 190 is a dead token: it does not heal by
        retrying, so it stays terminal on the first attempt. Asserted alongside
        the throttle codes because the classifier has to DISCRIMINATE — a change
        that softened every MetaCapiError would pass the throttle tests and
        silently make a credential failure retry forever.
        """
        failed: dict = {}

        def reject(**kwargs):
            raise _meta_signal_service.meta_capi.MetaCapiError(
                "Error validating access token", code=190
            )

        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(
            _meta_signal_service, "_get_principal_driver", lambda _id: None
        )
        monkeypatch.setattr(_meta_signal_service.meta_capi, "send_event", reject)
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_attempt_failed",
            lambda sid, msg, attempts, *, terminal, **kw: failed.update(
                terminal=terminal
            ),
        )
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")

        _meta_signal_service._send_one({
            "id": "signal-1", "lead_id": "lead-1", "event_name": "Lead",
            "attempt_count": 0, "created_at": "2026-08-07T12:00:00+00:00",
            "payload": None,
        })

        assert failed["terminal"] is True

    def test_every_row_goes_to_the_one_dataset(self, monkeypatch):
        """Since 0133 there is one dataset, Auto Insurance, in
        META_CAPI_DATASET_ID. Asserted on what reached send_event, since the row
        would be marked Sent whichever id it went to."""
        sent: dict = {}
        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(
            _meta_signal_service, "_get_principal_driver", lambda _id: None
        )
        monkeypatch.setattr(
            _meta_signal_service.meta_capi, "send_event",
            lambda **kwargs: sent.update(kwargs) or {"events_received": 1},
        )
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_sent", lambda signal_id, payload: None
        )
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-auto-insurance")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")

        _meta_signal_service._send_one({
            "id": "signal-1", "lead_id": "lead-1", "event_name": "Lead",
            "attempt_count": 0, "created_at": "2026-08-07T12:00:00+00:00",
            "payload": None, "dataset_target": "legacy",
        })
        assert sent["dataset_id"] == "ds-auto-insurance"

    def test_no_dataset_means_the_row_waits(self, monkeypatch):
        """SKIPPED, NOT FAILED. With META_CAPI_DATASET_ID unset the event is
        fine and the configuration is not, so the row stays Pending and sends
        once the dataset is set — nothing is POSTed and nothing is failed."""
        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(
            _meta_signal_service, "_get_principal_driver", lambda _id: None
        )

        def _must_not_send(**kwargs):
            raise AssertionError("nothing should be POSTed with no dataset")

        def _must_not_fail(*a, **k):
            raise AssertionError("a missing dataset must not fail the row")

        monkeypatch.setattr(_meta_signal_service.meta_capi, "send_event", _must_not_send)
        monkeypatch.setattr(_meta_signal_service.repo, "mark_attempt_failed", _must_not_fail)
        monkeypatch.delenv("META_CAPI_DATASET_ID", raising=False)
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")

        outcome = _meta_signal_service._send_one({
            "id": "signal-1", "lead_id": "lead-1", "event_name": "Lead",
            "attempt_count": 0, "created_at": "2026-08-07T12:00:00+00:00",
            "payload": None, "dataset_target": "legacy",
        })
        assert outcome == "skipped"

    def test_the_drain_reads_nothing_with_no_dataset(self, monkeypatch):
        """Every row would come back 'skipped', so the pass does not even read
        the queue."""
        def _must_not_read(**kwargs):
            raise AssertionError("the queue should not be read with no dataset")

        monkeypatch.setattr(_meta_signal_service.repo, "list_pending", _must_not_read)
        monkeypatch.delenv("META_CAPI_DATASET_ID", raising=False)

        result = _meta_signal_service.send_pending_signals()
        assert result["checked"] == 0

    def test_an_overlapping_send_pass_does_nothing(self, monkeypatch):
        """TWO PASSES MUST NOT READ THE SAME PENDING ROWS.

        POST /meta-signal/send-pending can land while the 15-minute drain is
        mid-pass, and list_pending has no locking. Meta deduplicates on
        event_id so the conversion was never at risk — the database record was:
        the slower pass's failure write could land on a row the faster one had
        already marked Sent, leaving a DELIVERED event recorded as Failed, which
        is the state 0112 identifies as the door to double-counting.

        Proven by calling send_pending_signals re-entrantly from INSIDE a send,
        which is exactly the overlap, and asserting the inner call read nothing.
        """
        inner: dict = {}
        reads = {"count": 0}

        class _Result:
            data = [{
                "id": "signal-1", "lead_id": "lead-1", "event_name": "Lead",
                "attempt_count": 0, "created_at": "2026-08-07T12:00:00+00:00",
                "payload": None, "dataset_target": "legacy",
            }]

        def _list_pending(limit=50):
            reads["count"] += 1
            return _Result()

        monkeypatch.setattr(_meta_signal_service.repo, "list_pending", _list_pending)
        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(
            _meta_signal_service, "_get_principal_driver", lambda _id: None
        )
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_sent", lambda signal_id, payload: None
        )

        def _send_event(**kwargs):
            # The overlap, from inside the critical section.
            inner.update(_meta_signal_service.send_pending_signals())
            return {"events_received": 1}

        monkeypatch.setattr(
            _meta_signal_service.meta_capi, "send_event", _send_event
        )
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")

        outer = _meta_signal_service.send_pending_signals()

        assert outer["sent"] == 1, "the first pass still does its work"
        assert inner["skipped_locked"] is True, "the overlapping pass backs off"
        assert inner["checked"] == 0
        assert reads["count"] == 1, "the second pass never even read the queue"

    def test_the_lock_is_released_after_a_raise(self, monkeypatch):
        """A pass that dies must not wedge the drain forever. list_pending
        raising is the realistic case — Supabase unreachable — and if the lock
        leaked there, CAPI would stop sending for the life of the process with
        the app still looking healthy."""
        def _boom(limit=50):
            raise RuntimeError("supabase unreachable")

        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
        monkeypatch.setattr(_meta_signal_service.repo, "list_pending", _boom)
        with pytest.raises(RuntimeError):
            _meta_signal_service.send_pending_signals()

        # And the next pass gets in.
        monkeypatch.setattr(
            _meta_signal_service.repo, "list_pending",
            lambda limit=50: type("R", (), {"data": []})(),
        )
        assert _meta_signal_service.send_pending_signals()["checked"] == 0

    def test_the_bulk_retry_audits_with_a_non_null_entity(self, monkeypatch):
        """audit_log.entity_id is `text not null` (0012:33), and this call is
        wrapped in a handler that only LOGS — so passing None would have let the
        retry succeed while the audit row silently never appeared. Exactly the
        invisible failure this change set exists to remove.
        """
        audited: dict = {}
        monkeypatch.setattr(
            _meta_signal_service.repo, "reset_code_190_to_pending",
            lambda: type("R", (), {"data": [{"id": "a"}, {"id": "b"}]})(),
        )
        monkeypatch.setattr(
            _meta_signal_service.audit_repo, "insert",
            lambda **kw: audited.update(kw),
        )

        out = _meta_signal_service.retry_dead_token_failures("admin-1")

        assert out["requeued"] == 2
        assert audited["entity_id"] is not None
        assert audited["entity_id"], "must be a non-empty string, not None"
        assert audited["actor_broker_id"] == "admin-1"
        assert audited["detail"]["error_code"] == 190

    def test_the_bulk_retry_survives_a_failed_audit(self, monkeypatch):
        """The re-queue already happened. Losing the audit row is a log line,
        not a failed request that would leave an admin unsure whether the retry
        went through."""
        monkeypatch.setattr(
            _meta_signal_service.repo, "reset_code_190_to_pending",
            lambda: type("R", (), {"data": [{"id": "a"}]})(),
        )

        def _audit_boom(**kw):
            raise RuntimeError("audit_log unreachable")

        monkeypatch.setattr(
            _meta_signal_service.audit_repo, "insert", _audit_boom
        )

        assert _meta_signal_service.retry_dead_token_failures("admin-1") == {
            "requeued": 1
        }

    def test_a_rejected_event_is_not_marked_sent(self, monkeypatch):
        """Guard rail: failures already keep Meta's reason in error_message,
        and must never reach mark_sent."""
        def reject(**kwargs):
            # code=190 is Meta's expired-token error. Raised here so the
            # assertion below can prove the CODE reaches the database, which is
            # what the admin screen groups failures by — "the token expired"
            # rather than twenty rows of identical error text.
            raise _meta_signal_service.meta_capi.MetaCapiError(
                "Error validating access token", code=190
            )

        marked: dict = {}
        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(_meta_signal_service, "_get_principal_driver", lambda _id: None)
        monkeypatch.setattr(_meta_signal_service.meta_capi, "send_event", reject)
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_sent",
            lambda signal_id, payload: marked.update(id=signal_id),
        )
        failed: dict = {}
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_attempt_failed",
            # **kw absorbs error_code, added 2026-08-07 — a stub that pins the
            # exact signature breaks on every new keyword the real function
            # gains, which says nothing about the behaviour under test.
            lambda sid, msg, attempts, *, terminal, **kw: failed.update(
                msg=msg, terminal=terminal, code=kw.get("error_code")
            ),
        )
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")

        # A PREMIUM IS REQUIRED to reach send_event at all since 0100 — without
        # one the premium guard fails the row first, and this test would pass
        # for entirely the wrong reason, never exercising the token rejection
        # it exists to check.
        row = {"id": "signal-1", "lead_id": "lead-1", "event_name": "CompleteRegistration",
               "attempt_count": 0, "created_at": "2026-08-07T12:00:00+00:00",
               "payload": {"total_new_premium_at_registration": 1200}}

        assert _meta_signal_service._send_one(row) == "failed"
        assert not marked, "a rejected event must never be marked Sent"
        assert failed["terminal"] is True
        assert failed["code"] == 190, "Meta's error code must be stored, not just its text"


# ── custom_data reaches Meta on EVERY event ──────────────────────────────
@pytest.mark.skipif(not _HAS_DB_ENV, reason="services.meta_signal needs SUPABASE_* at import")
class TestEveryEventCarriesCrmFields:
    """event_source and lead_event_source are required on EVERY Conversion
    Leads event, not only the ones carrying a value.

    THE TESTING GAP THIS CLOSES. build_custom_data was already correct and
    already tested — but every one of those tests called it DIRECTLY. None
    checked that the sender passes it. _send_one built custom_data only inside
    `if row["event_name"] == "Purchase"`, so Lead and CompleteRegistration went

    to Meta with custom_data=None and neither required field.

    CompleteRegistration is the event the optimisation is aimed at, and it was
    one of the two broken ones. Since 0100 it is also the ONLY event carrying
    a value, which makes both halves of this class load-bearing.
    """

    def _capture(self, monkeypatch, event_name, payload=None, after_setup=None):
        """Run the real _send_one against a stubbed transport and hand back the
        kwargs send_event actually received — the thing no previous test
        looked at."""
        seen: dict = {}

        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": "4165550000",
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(_meta_signal_service, "_get_principal_driver", lambda _id: None)
        monkeypatch.setattr(_meta_signal_service.meta_capi, "send_event",
                            lambda **kw: seen.update(kw) or {"events_received": 1})
        monkeypatch.setattr(_meta_signal_service.repo, "mark_sent", lambda *a, **k: None)
        # Stubbed too, since 0100: a premium-less event now takes the terminal
        # branch, and an unstubbed repo call would reach the real database.
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_attempt_failed", lambda *a, **k: None
        )
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")

        if after_setup is not None:
            after_setup(monkeypatch)

        row = {
            "id": "signal-1", "lead_id": "lead-1", "event_name": event_name,
            "attempt_count": 0, "created_at": "2026-08-07T12:00:00+00:00",
            "payload": payload,
        }
        outcome = _meta_signal_service._send_one(row)
        return outcome, seen

    def test_the_crm_fields_reach_meta_on_a_lead(self, monkeypatch):
        """THE BUG. Lead sent custom_data=None."""
        outcome, seen = self._capture(monkeypatch, "Lead")
        assert outcome == "sent"
        assert seen["custom_data"] is not None, "Lead sent no custom_data at all"
        assert seen["custom_data"]["event_source"] == "crm"
        assert seen["custom_data"]["lead_event_source"]

    def test_a_purchase_carries_its_premium_as_value_and_currency(self, monkeypatch):
        """THE BUG FOUND ON 2026-10-05. ensure_conversion_signals snapshots the
        sale premium into payload.total_new_premium_at_purchase, but the sender
        only read a CompleteRegistration key, so every Purchase would have gone
        to Meta with no value. The money rides on Purchase and nowhere else."""
        outcome, seen = self._capture(
            monkeypatch,
            "Purchase",
            payload={"total_new_premium_at_purchase": 2400},
        )
        assert outcome == "sent"
        cd = seen["custom_data"]
        assert cd["event_source"] == "crm"
        assert cd["lead_event_source"]
        assert cd["value"] == 2400.0
        assert cd["currency"] == "CAD"

    def test_a_complete_registration_carries_no_value(self, monkeypatch):
        """Since 0123 a CompleteRegistration means "a real prospect", which has
        no price. Even a row carrying the pre-0123 key sends no value."""
        outcome, seen = self._capture(
            monkeypatch,
            "CompleteRegistration",
            payload={"total_new_premium_at_registration": 2450},
        )
        assert outcome == "sent"
        assert "value" not in seen["custom_data"]
        assert "currency" not in seen["custom_data"]

    def test_the_old_payload_key_is_not_read(self, monkeypatch):
        """0100 renamed total_new_premium_at_sale ->
        total_new_premium_at_registration. There is no compatibility branch,
        deliberately: zero Purchase rows ever existed, so there was nothing to
        stay compatible WITH.

        THE ASSERTION CHANGED WITH 0115/0116, THE BEHAVIOUR DID NOT. This used
        to assert the event FAILED, using the premium hard-fail as its detector:
        the old key was unreadable, so no premium was found, so the row was
        marked terminally Failed. That hard-fail is gone — In Progress fires
        before a quote exists, so a premium-less event is now ordinary and is
        sent without a value.

        What this protects is unchanged and is what is asserted now: the old key
        is not read, so it contributes NO value. A regression that started
        reading it would put 2450 into custom_data and fail here.
        """
        outcome, seen = self._capture(
            monkeypatch,
            "CompleteRegistration",
            payload={"total_new_premium_at_sale": 2450},
        )
        assert outcome == "sent"
        assert "value" not in seen["custom_data"], "the old key must not be read"
        assert "currency" not in seen["custom_data"]

    def test_a_lead_carries_no_value(self, monkeypatch):
        """A Lead has no amount. Sending value: 0 would tell Meta the
        conversion was worth nothing, which is a different claim from making
        no claim."""
        _, seen = self._capture(monkeypatch, "Lead")
        assert "value" not in seen["custom_data"]
        assert "currency" not in seen["custom_data"]

    def test_a_premiumless_registration_is_sent_without_a_value(self, monkeypatch):
        """REVERSED BY 0115/0116. This asserted the opposite until the signal
        moved earlier in the funnel.

        It was terminally Failed from 0100, and that was right while the event
        fired at Quote Done — services/lead.py's premium guard runs before that
        stage write, so arriving here with no figure meant something had
        bypassed it (direct SQL, a hand-inserted row) and deserved a visible
        failure.

        CompleteRegistration now fires at In Progress, where there is usually no
        quote at all — that is the entire reason for firing earlier. Keeping the
        hard-fail would have terminally failed MOST new signals, and the feature
        would have looked wired while sending nothing.

        Meta accepts a valued event without a value: build_custom_data omits
        value and currency cleanly, the Conversion Leads spec never requires
        them, and 8 of the 23 CompleteRegistrations already sent carried none.

        NOTHING IS UNGUARDED. _STAGES_NEEDING_PREMIUM still refuses Pending and
        the sale stages without a figure — at the stage move, which is the one
        place that can actually tell a broker about it.
        """
        must_not_fail: dict = {}

        # Patched INSIDE the send, not before it: _capture installs its own
        # no-op stub, so patching first would simply be overwritten.
        def _catch_any_failure(monkeypatch_):
            monkeypatch_.setattr(
                _meta_signal_service.repo,
                "mark_attempt_failed",
                lambda _id, msg, attempts, terminal=False, **k: must_not_fail.update(
                    message=msg, terminal=terminal
                ),
            )

        outcome, seen = self._capture(
            monkeypatch, "CompleteRegistration", payload={}, after_setup=_catch_any_failure
        )
        assert outcome == "sent"
        assert not must_not_fail, f"must not be marked failed: {must_not_fail}"
        assert "value" not in seen["custom_data"]
        assert "currency" not in seen["custom_data"]
        # The two CRM fields Meta requires on EVERY Conversion Leads event are
        # still there — a premium-less event is incomplete in value only.
        assert seen["custom_data"]["event_source"] == "crm"
        assert seen["custom_data"]["lead_event_source"] == "MyDay CRM"


class TestContactArraysCarryBoth:
    """em and ph carry the lead's contact AND the applicant's.

    Only lead["email"]/["phone"] were sent until 2026-08-07, so a broker
    correcting a typo on the applicant screen changed nothing about what Meta
    received. The two are different facts: the lead holds what the customer
    typed into Meta's form, the applicant what the broker confirmed on the
    call — and the applicant may not even be the same person, since someone
    can enquire on behalf of a spouse.
    """

    LEAD = {"id": "l1", "email": "a@b.com", "phone": "4165550000",
            "platform_leadgen_id": "lg-1"}

    def test_a_corrected_applicant_contact_is_also_sent(self):
        """THE BUG. Both go, because Meta tries every value in the array."""
        ud = meta_capi.build_user_data(
            lead=self.LEAD, driver={"email": "fixed@b.com", "phone": "6475551111"},
        )
        assert sha("fixed@b.com") in ud["em"]
        assert sha("a@b.com") in ud["em"], "the original must not be dropped"
        assert len(ud["ph"]) == 2

    def test_an_identical_contact_is_not_sent_twice(self):
        """The applicant is seeded FROM the lead, so the two are usually the
        same. Deduplicated after hashing, which also collapses case and
        whitespace differences."""
        ud = meta_capi.build_user_data(
            lead=self.LEAD, driver={"email": "  A@B.com ", "phone": "4165550000"},
        )
        assert ud["em"] == [sha("a@b.com")]
        assert len(ud["ph"]) == 1

    def test_no_driver_still_sends_the_lead_contact(self):
        """A signal can fire before the applicant row is opened."""
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=None)
        assert ud["em"] == [sha("a@b.com")]

    def test_a_contactless_lead_omits_the_field(self):
        """Absent beats present-but-empty, which Meta scores worse."""
        ud = meta_capi.build_user_data(
            lead={"id": "l1", "platform_leadgen_id": "lg-1"}, driver=None,
        )
        assert "em" not in ud and "ph" not in ud


class TestTokenHealthCheck:
    """Ask Meta whether the token is still good, rather than waiting for a
    conversion to fail.

    PROACTIVE, unlike every other signal in this system: by the time a failure
    appears, a lead has already gone unreported.

    NOT REALLY ABOUT EXPIRY. The token in use is a SYSTEM_USER token, which
    Meta issues with expires_at = 0 — it does not expire. What actually
    happens is revocation, app restriction, or the wrong scopes, and is_valid
    covers all three.
    """

    def _stub(self, monkeypatch, *, status=200, body=None, raises=None):
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")

        def fake_get(url, **kwargs):
            if raises:
                raise raises
            import httpx as _h
            return _h.Response(status, json=body or {}, request=_h.Request("GET", url))

        monkeypatch.setattr(meta_capi.httpx, "get", fake_get)

    def test_a_valid_token_reports_ok(self, monkeypatch):
        self._stub(monkeypatch, body={"data": {
            "is_valid": True, "type": "SYSTEM_USER", "expires_at": 0,
            "scopes": ["ads_management"], "app_id": "1",
        }})
        r = meta_capi.check_token()
        assert r["ok"] is True
        assert r["reason"] is None

    def test_a_never_expiring_token_reports_no_expiry(self, monkeypatch):
        """Meta sends 0, not null. Passed through as None so the screen does
        not have to know that, and cannot render 'expires 1 Jan 1970'."""
        self._stub(monkeypatch, body={"data": {"is_valid": True, "expires_at": 0}})
        assert meta_capi.check_token()["expires_at"] is None

    def test_a_revoked_token_reports_not_ok(self, monkeypatch):
        """THE CASE THIS EXISTS FOR — and it is reachable even though the
        token never expires."""
        self._stub(monkeypatch, body={"data": {"is_valid": False}})
        r = meta_capi.check_token()
        assert r["ok"] is False
        assert r["reason"]

    def test_an_unreachable_meta_is_not_reported_as_a_bad_token(self, monkeypatch):
        """A network failure says nothing about the credential. Calling it
        invalid would send an admin to replace a token that was fine."""
        self._stub(monkeypatch, raises=RuntimeError("connection refused"))
        r = meta_capi.check_token()
        assert r["ok"] is False
        assert "Could not reach Meta" in r["reason"]

    def test_missing_configuration_is_named(self, monkeypatch):
        monkeypatch.delenv("META_CAPI_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("META_CAPI_DATASET_ID", raising=False)
        r = meta_capi.check_token()
        assert r["ok"] is False
        assert "not configured" in r["reason"]

    def test_it_never_raises(self, monkeypatch):
        """It feeds a status strip. A health check that takes the page down
        with it is worse than one that says it could not tell."""
        self._stub(monkeypatch, raises=Exception("anything at all"))
        assert meta_capi.check_token()["ok"] is False


class TestMetaRejectionStatusCodes:
    """A bad token comes back as 401, not 400.

    FOUND BY SENDING ONE. Reading only 400 meant Meta's OAuthException fell
    through to raise_for_status, surfaced as a plain Exception, and was treated
    as a TRANSIENT network failure — retried three times, given up on, and
    recorded with no error_code. So the admin screen could not identify the one
    failure it exists to name.
    """

    def _send(self, monkeypatch, status, body):
        import httpx

        def fake_post(url, **kwargs):
            return httpx.Response(status, json=body, request=httpx.Request("POST", url))

        monkeypatch.setattr(meta_capi.httpx, "post", fake_post)
        return meta_capi.send_event(
            dataset_id="ds-1", access_token="tok", event_name="Lead",
            event_time=1, event_id="e1", user_data={"country": "ca"},
        )

    def test_a_401_carries_metas_error_code(self):
        """THE BUG. Meta's real reply to a broken token."""
        import pytest as _pytest

        monkeypatch = _pytest.MonkeyPatch()
        try:
            with _pytest.raises(meta_capi.MetaCapiError) as err:
                self._send(monkeypatch, 401, {"error": {
                    "code": 190, "type": "OAuthException",
                    "message": "Invalid OAuth access token",
                }})
            assert err.value.code == 190, "an admin cannot be told which failure this is"
        finally:
            monkeypatch.undo()

    def test_a_400_still_carries_its_code(self):
        """Guard rail: the original path must keep working."""
        import pytest as _pytest

        monkeypatch = _pytest.MonkeyPatch()
        try:
            with _pytest.raises(meta_capi.MetaCapiError) as err:
                self._send(monkeypatch, 400, {"error": {"code": 100, "message": "Invalid parameter"}})
            assert err.value.code == 100
        finally:
            monkeypatch.undo()

    def test_a_500_is_still_transient(self):
        """Guard rail the other way: a Meta outage must NOT become terminal.
        MetaCapiError means "do not retry", and a 5xx is exactly the case that
        clears on its own."""
        import pytest as _pytest

        monkeypatch = _pytest.MonkeyPatch()
        try:
            with _pytest.raises(Exception) as err:
                self._send(monkeypatch, 500, {"error": {"message": "server error"}})
            assert not isinstance(err.value, meta_capi.MetaCapiError)
        finally:
            monkeypatch.undo()


class TestTheTokenNeverReachesStorage:
    """Meta quotes the access token back in its own error message.

    "Malformed access token EAA…" is Meta's literal reply to a bad credential,
    and that message is stored in meta_signal.error_message and rendered on the
    admin screen. Without redaction a LIVE system user token would be written
    to the database in the clear — the same class of leak as the query-param
    token this file already carries a comment about, arriving from the other
    direction. Found by deliberately sending a bad token on 2026-08-07.
    """

    TOKEN = "EAAG_a_real_looking_system_user_token_value"

    def _send_with_bad_token(self, monkeypatch):
        import httpx

        def fake_post(url, **kwargs):
            return httpx.Response(
                401,
                json={"error": {"code": 190, "message": f"Malformed access token {self.TOKEN}"}},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(meta_capi.httpx, "post", fake_post)
        return meta_capi.send_event(
            dataset_id="ds-1", access_token=self.TOKEN, event_name="Lead",
            event_time=1, event_id="e1", user_data={"country": "ca"},
        )

    def test_the_stored_message_does_not_contain_the_token(self, monkeypatch):
        with pytest.raises(meta_capi.MetaCapiError) as err:
            self._send_with_bad_token(monkeypatch)
        assert self.TOKEN not in str(err.value.reason)
        assert "[token redacted]" in str(err.value.reason)

    def test_the_message_is_still_useful(self, monkeypatch):
        """Redacted, not discarded. An admin still needs to know Meta refused
        the credential rather than the payload."""
        with pytest.raises(meta_capi.MetaCapiError) as err:
            self._send_with_bad_token(monkeypatch)
        assert "access token" in str(err.value.reason).lower()
        assert err.value.code == 190

    def test_a_short_value_is_left_alone(self):
        """Redacting a three-character token would blank ordinary words out of
        a message that has to stay readable."""
        assert meta_capi._redact_token("Invalid parameter", "abc") == "Invalid parameter"


class TestOnlyAutoInsuranceRowsAreHandled:
    """Since 0133 only 'legacy' rows are live. Rows from the retired
    five-dataset setup are history: never sent, never retried."""

    def test_a_retired_dataset_row_cannot_be_retried(self, monkeypatch):
        monkeypatch.setattr(
            _meta_signal_service.repo, "get_by_id",
            lambda _id: type("R", (), {"data": [{
                "id": "sig-1", "status": "Failed", "event_name": "Lead",
                "lead_id": "lead-1", "dataset_target": "preferred",
            }]})(),
        )

        def _must_not_reset(_id):
            raise AssertionError("a retired-dataset row must not be re-queued")

        monkeypatch.setattr(_meta_signal_service.repo, "reset_to_pending", _must_not_reset)

        with pytest.raises(_meta_signal_service.HTTPException) as err:
            _meta_signal_service.retry("sig-1", "admin-1")
        assert err.value.status_code == 409

    def test_an_auto_insurance_row_is_still_retried(self, monkeypatch):
        monkeypatch.setattr(
            _meta_signal_service.repo, "get_by_id",
            lambda _id: type("R", (), {"data": [{
                "id": "sig-1", "status": "Failed", "event_name": "Lead",
                "lead_id": "lead-1", "dataset_target": "legacy",
            }]})(),
        )
        monkeypatch.setattr(
            _meta_signal_service.repo, "reset_to_pending",
            lambda _id: type("R", (), {"data": [{"id": "sig-1", "status": "Pending"}]})(),
        )
        monkeypatch.setattr(_meta_signal_service.audit_repo, "insert", lambda **k: None)

        assert _meta_signal_service.retry("sig-1", "admin-1")["status"] == "Pending"


class TestAPurchaseWorthNothingIsNeverSent:
    """A row queued by the older SQL could carry a $0 or missing premium.
    The sender fails it outright rather than telling Meta the sale was
    worthless."""

    def _send(self, monkeypatch, premium):
        posted, failed = [], []
        monkeypatch.setattr(
            _meta_signal_service, "_get_lead",
            lambda _id: {"id": "lead-1", "email": "a@b.com", "phone": None,
                         "platform_leadgen_id": "lg-1"},
        )
        monkeypatch.setattr(_meta_signal_service, "_get_principal_driver", lambda _id: None)
        monkeypatch.setattr(
            _meta_signal_service.meta_capi, "send_event",
            lambda **kw: posted.append(kw) or {"events_received": 1},
        )
        monkeypatch.setattr(_meta_signal_service.repo, "mark_sent", lambda *a, **k: None)
        monkeypatch.setattr(
            _meta_signal_service.repo, "mark_attempt_failed",
            lambda *a, **k: failed.append((a, k)),
        )
        monkeypatch.setenv("META_CAPI_DATASET_ID", "ds-1")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "tok-1")
        payload = None if premium is ... else {"total_new_premium_at_purchase": premium}
        outcome = _meta_signal_service._send_one({
            "id": "sig-1", "lead_id": "lead-1", "event_name": "Purchase",
            "attempt_count": 0, "created_at": "2026-10-24T12:00:00+00:00",
            "payload": payload, "dataset_target": "legacy",
        })
        return outcome, posted, failed

    @pytest.mark.parametrize("premium", [0, 0.0, "0", -5, None, "", ...])
    def test_no_premium_above_zero_fails_without_posting(self, monkeypatch, premium):
        outcome, posted, failed = self._send(monkeypatch, premium)
        assert outcome == "failed"
        assert posted == []
        [(args, kwargs)] = failed
        assert kwargs["terminal"] is True
        assert "premium" in args[1]

    @pytest.mark.parametrize("premium", [2400, "2400.00"])
    def test_a_real_premium_is_sent_as_value(self, monkeypatch, premium):
        outcome, posted, failed = self._send(monkeypatch, premium)
        assert outcome == "sent" and failed == []
        assert posted[0]["custom_data"]["value"] == 2400.0
        assert posted[0]["custom_data"]["currency"] == "CAD"
