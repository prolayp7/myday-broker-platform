"""Tests for the vCard 3.0 builder.

WHY THIS FILE CARRIES THE WEIGHT. A malformed card does not raise anything. It
is accepted by Radicale, stored, synced, and then either silently dropped by the
phone or rendered as garbage in a broker's Contacts — and nobody finds out until
a customer rings and the screen still shows a bare number. There is no runtime
signal, so the format rules are only ever as correct as this file is.

Four of them are invisible to casual testing and each has its own class below:
escaping ORDER, CRLF line endings, folding by OCTETS without splitting a UTF-8
sequence, and the placeholder contact strings that lead.phone/lead.email carry
because both columns are NOT NULL.

Nothing here touches the database — vcard.py imports neither db.py nor any
service — so these run without SUPABASE_URL and without the dbtest marker.
"""

import pytest

import vcard


def _has_email(text: str) -> bool:
    """True if the card carries an EMAIL property of any parameterisation.

    NOT `"EMAIL" in _props(text)`. The property name includes its parameters —
    the key is "EMAIL;TYPE=INTERNET" — so a membership test on the bare word
    is always False and passes against a card that does contain an email.
    Verified: the placeholder tests below passed against a builder that wrote
    "(not provided)" straight through.
    """
    return any(k.split(";")[0] == "EMAIL" for k in _props(text))


def _props(text: str) -> dict[str, str]:
    """Unfold a card and split it into property -> value.

    Written to parse rather than to assert on substrings: `"FN:Teena" in text`
    passes just as happily against a card where FN was folded across two lines
    or duplicated, which is exactly the class of bug this file exists to catch.
    """
    unfolded = text.replace("\r\n ", "")
    out: dict[str, str] = {}
    for line in unfolded.split("\r\n"):
        if not line:
            continue
        name, _, value = line.partition(":")
        out[name] = value
    return out


def _card(**kw) -> str:
    """A valid card, with any field overridden by the caller."""
    args = {
        "uid": "myday-abc",
        "name": "Teena",
        "phone_e164": "+16475313736",
        "email": None,
    }
    args.update(kw)
    return vcard.build_vcard(**args)


# ── the required shape ───────────────────────────────────────────────────
class TestRequiredProperties:
    def test_every_property_rfc_2426_demands_is_present(self):
        """BEGIN, VERSION, FN, N and END are all MUST in vCard 3.0. A card
        missing any one of them is not a vCard, whatever a lenient parser makes
        of it."""
        props = _props(_card())
        for required in ("BEGIN", "VERSION", "FN", "N", "END"):
            assert required in props, f"{required} missing"

    def test_the_version_is_three_point_zero(self):
        """Not 4.0. RFC 6352: absent a server-advertised supported-address-data,
        a CardDAV server must only accept 3.0."""
        assert _props(_card())["VERSION"] == "3.0"

    def test_the_uid_is_written(self):
        """Optional per the spec, included anyway — clients dedupe on it, and it
        ties the card back to its carddav_contact row."""
        assert _props(_card(uid="myday-xyz"))["UID"] == "myday-xyz"


# ── the display name ─────────────────────────────────────────────────────
class TestDisplayName:
    def test_fn_carries_the_ps_suffix(self):
        """The whole point of the marker: a broker seeing a name ring knows it
        is a work lead rather than someone from their own contacts."""
        assert _props(_card(name="Teena"))["FN"] == "Teena (PS)"

    def test_the_name_is_not_split_into_given_and_family(self):
        """N's components are family;given;additional;prefix;suffix. This schema
        has one `name` column, and guessing the split from whitespace is wrong
        often enough — compound surnames, single-word names, "Last, First" — to
        produce visibly wrong contacts. Whole name in `given`, marker in
        `suffix`, family left empty."""
        assert _props(_card(name="Mitali Chawla"))["N"] == ";Mitali Chawla;;;(PS)"


# ── escaping (RFC 2426 section 5) ────────────────────────────────────────
class TestEscaping:
    def test_a_comma_is_escaped(self):
        """Unescaped, "Smith, John" makes FN a two-component value and the
        contact renders as "Smith" on the phone."""
        assert _props(_card(name="Smith, John"))["FN"] == r"Smith\, John (PS)"

    def test_a_semicolon_is_escaped(self):
        assert _props(_card(name="Smith; John"))["FN"] == r"Smith\; John (PS)"

    def test_a_backslash_is_escaped_before_anything_else(self):
        r"""THE ORDERING BUG, tested explicitly.

        Input `a\;b` must become `a\\\;b`: the literal backslash doubled, and
        the semicolon escaped separately.

        Escape the semicolon first and you get `a\\;b`, then the backslash pass
        doubles BOTH backslashes into `a\\\\;b` — a literal backslash followed
        by a bare semicolon, which splits the value into two components. The
        card stays syntactically valid, so nothing complains; the contact is
        just wrong.
        """
        assert _props(_card(name="a\\;b"))["FN"] == "a\\\\\\;b (PS)"

    def test_escaping_applies_inside_n_components_not_to_their_separators(self):
        """N is structured: the five separating semicolons must stay raw while
        semicolons inside a component are escaped. Escaping everything would
        collapse the structure; escaping nothing would let a name create extra
        components."""
        n = _props(_card(name="Smith; John"))["N"]
        assert n == r";Smith\; John;;;(PS)"
        # Four raw separators, exactly — the escaped one does not count.
        assert n.replace(r"\;", "").count(";") == 4


# ── line endings ─────────────────────────────────────────────────────────
class TestLineEndings:
    def test_every_line_ends_with_crlf(self):
        """RFC 2426 delimits lines with CRLF. A bare LF is rejected by strict
        parsers and silently mangled by lenient ones."""
        text = _card()
        assert "\n" in text
        for i, ch in enumerate(text):
            if ch == "\n":
                assert text[i - 1] == "\r", f"bare LF at offset {i}"

    def test_the_final_line_is_terminated_too(self):
        assert _card().endswith("END:VCARD\r\n")


# ── folding (RFC 2426 section 2.6) ───────────────────────────────────────
class TestFolding:
    def test_a_short_card_is_not_folded(self):
        """Folding a line that fits would add continuation spaces for nothing,
        and every real lead name is short."""
        assert "\r\n " not in _card(name="Teena")

    def test_a_long_line_is_folded_at_the_octet_limit(self):
        text = _card(name="Q" * 200)
        for line in text.split("\r\n"):
            assert len(line.encode("utf-8")) <= 75, f"line too long: {line!r}"

    def test_folding_never_splits_a_multibyte_character(self):
        """THE BUG A NAIVE FOLDER HIDES.

        A folder that slices by characters, or one that slices bytes and
        decodes with errors="replace", passes every ASCII test above and then
        cuts an accented name mid-codepoint the first time one appears. The
        phone receives a broken sequence and drops or mojibakes the property.

        ASSERTED ON THE ROUND-TRIP, not on "decoding raised nothing". A byte
        slicer using errors="replace" substitutes U+FFFD rather than raising,
        so a test that only decodes each line passes against the very bug it
        was written for — verified by breaking the folder that way.
        """
        # CONSTRUCTED so a two-byte character straddles the 75-octet boundary
        # rather than hoping one lands there. "FN:" is 3 octets, so 71 ASCII
        # characters fill the line to octet 74 and the next character — two
        # octets wide — is cut in half by a slicer that does not check.
        #
        # A string of repeating accents does NOT reproduce this: verified, it
        # round-trips cleanly through the broken folder because no boundary
        # happens to fall mid-character. The bug is real but narrow, which is
        # exactly why the test has to aim at it.
        name = "x" * 71 + "ö" * 20
        text = _card(name=name)

        # THE PRIMARY ASSERTION: unfolding gives back exactly what went in.
        # Every other check here is diagnosis; this one is the requirement.
        assert _props(text)["FN"] == f"{name} (PS)"

        # No replacement characters: their presence IS the corruption a byte
        # slicer with errors="replace" produces.
        assert "�" not in text

        # Each physical line is independently valid UTF-8 (strict).
        for line in text.split("\r\n"):
            line.encode("utf-8").decode("utf-8")

        assert "\r\n " in text  # the folding is real, not skipped

    def test_an_unfolded_card_round_trips_to_the_original_value(self):
        """Folding must be reversible: unfolding (strip CRLF + one space) has to
        give back exactly what went in, or the phone stores a name with stray
        spaces in it."""
        name = "Ángela " + "x" * 120
        text = _card(name=name)
        assert _props(text)["FN"] == f"{name} (PS)"


# ── the phone number ─────────────────────────────────────────────────────
class TestPhone:
    def test_the_number_is_written_in_e164_as_a_cell(self):
        """E.164 with "+" so Android's right-to-left matcher (minimum 7 digits)
        resolves a call presented as 4165551234. CELL so it maps to
        TYPE_MOBILE."""
        props = _props(_card(phone_e164="+14165551234"))
        assert props["TEL;TYPE=CELL,VOICE"] == "+14165551234"

    def test_a_null_phone_raises(self):
        """Not "writes a card without a TEL". A contact with no number can never
        match an incoming call, so it is noise in the address book. The service
        layer skips these before reaching here; this raise is what makes a bug
        past that guard fail loudly.

        Null arrives for two ordinary reasons: the "(not provided)" placeholder,
        and any non-NANP number, since normalize_phone_e164 is NANP-only.
        """
        with pytest.raises(ValueError):
            _card(phone_e164=None)

    def test_a_blank_phone_raises_too(self):
        with pytest.raises(ValueError):
            _card(phone_e164="   ")


# ── the email ────────────────────────────────────────────────────────────
class TestEmail:
    def test_a_real_address_is_written_once(self):
        props = _props(_card(email="teena@example.com"))
        assert props["EMAIL;TYPE=INTERNET"] == "teena@example.com"

    def test_the_not_provided_placeholder_produces_no_email_line(self):
        """lead.email is NOT NULL, so a lead captured without one carries the
        literal string "(not provided)". Written through, every such broker gets
        a contact whose email address reads "not provided"."""
        assert not _has_email(_card(email="(not provided)"))

    def test_other_placeholder_spellings_are_caught_as_well(self):
        """The frontend's isPlaceholderContact matches more than one stand-in,
        and a hand-entered lead can carry any of them. Matching only the
        parenthesised form would let "N/A" reach a phone."""
        for stand_in in ("N/A", "n/a", "unknown", "None", "not available", "  "):
            assert not _has_email(_card(email=stand_in)), stand_in

    def test_a_missing_email_still_yields_a_valid_card(self):
        """The phone is what makes a card worth having; the email is a bonus.
        A lead with no address must still reach the broker's contacts."""
        assert not _has_email(_card(email=None))
        assert _props(_card(email=None))["FN"] == "Teena (PS)"

    def test_an_email_is_escaped_like_any_other_text_value(self):
        assert _props(_card(email="a,b@example.com"))["EMAIL;TYPE=INTERNET"] == (
            r"a\,b@example.com"
        )


# ── identity and change detection ────────────────────────────────────────
class TestUid:
    def test_the_uid_is_stable_for_a_lead(self):
        """Deterministic on purpose: if carddav_contact is ever lost, a rebuild
        produces the same UID and therefore the same URL, so the card is updated
        rather than duplicated beside itself."""
        lead_id = "a3f8c2d1-4b7e-4c1a-9f2e-5d6b7c8a9e0f"
        assert vcard.vcard_uid_for_lead(lead_id) == vcard.vcard_uid_for_lead(lead_id)

    def test_different_leads_get_different_uids(self):
        assert vcard.vcard_uid_for_lead("a") != vcard.vcard_uid_for_lead("b")


class TestContentHash:
    def test_it_changes_when_the_name_changes(self):
        """This is what makes an edit detectable without a round-trip."""
        assert vcard.content_hash(_card(name="Teena")) != vcard.content_hash(
            _card(name="Teena Mathew")
        )

    def test_it_changes_when_the_phone_changes(self):
        assert vcard.content_hash(_card(phone_e164="+14165551234")) != (
            vcard.content_hash(_card(phone_e164="+16475313736"))
        )

    def test_it_changes_when_the_email_changes(self):
        assert vcard.content_hash(_card(email="a@example.com")) != (
            vcard.content_hash(_card(email="b@example.com"))
        )

    def test_it_does_not_change_when_nothing_does(self):
        """The common case, and the one that matters for load: an unchanged hash
        means the sync loop makes no HTTP call at all. If this were unstable,
        every cycle would re-PUT every card and every phone would redownload the
        whole address book on a schedule."""
        assert vcard.content_hash(_card()) == vcard.content_hash(_card())
