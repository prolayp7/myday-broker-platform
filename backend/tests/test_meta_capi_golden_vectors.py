"""The exact digests that go on the wire to Meta.

WHAT THIS ADDS OVER test_meta_capi.py. That file already pins the
normalisation RULES, thoroughly — email whitespace, accent preservation, city
punctuation, country, the unhashed lead_id. What it cannot catch is the
implementation and its expectations drifting TOGETHER, because every expected
value there is computed at test time from the same assumption the code makes:

    assert hash_email("  Test.User@Example.COM  ") == sha("test.user@example.com")

Change hash_email's normalisation, update the sha() argument to match, and the
suite stays green while every event sent afterwards matches nobody. Nothing
computes here. The 64-character strings below are literal, so a rule change
moves the digest and fails immediately — before a single unmatched event goes
out.

WHY THAT FAILURE IS WORTH A FILE. Meta accepts an event whose hashes match
nobody, returns success, and reports nothing wrong. There is no error, no
retry, no red row on the dashboard — the conversion is simply lost. This is
the one class of bug in the CAPI path that is invisible from our side.

WHERE THE EXPECTED VALUES COME FROM. The normalised forms are what Meta's
Customer Information Parameters specification requires:

  https://developers.facebook.com/docs/marketing-api/conversions-api/parameters/customer-information-parameters

The digests were generated from those normalised strings independently of
meta_capi.py, then checked against it. That order matters: a vector produced by
running our own code and pasting the result would freeze a current bug as the
permanent "correct" answer, and assert only that the code still does what it
did — the exact weakness this file exists to cover.

DO NOT "TIDY" A LITERAL INTO sha(). It would read as a simplification and
would delete the guard.
"""

import pytest

import meta_capi

# (function, input, normalised form per Meta's spec, literal sha256).
#
# The inputs deliberately mirror the cases test_meta_capi.py exercises, so the
# two files pin the same behaviour from two directions: that file says "this
# rule holds", this one says "and it produces exactly this digest". An input
# with nothing to normalise would pin a digest no rule change could move.
GOLDEN = [
    # Surrounding whitespace AND uppercase, so the digest moves if either
    # step is dropped.
    (
        meta_capi.hash_email,
        "  Test.User@Example.COM  ",
        "test.user@example.com",
        "a97d7a4513204a9cc7cb2f11d72d41a59b18d1ba633d22e58d53c625518f5203",
    ),
    # Punctuation stripped, then the country code prefixed — Meta requires the
    # number to carry it.
    (
        meta_capi.hash_phone,
        "(416) 555-1234",
        "14165551234",
        "a1af4d4ab21826f89f0ec12f1d708bf68b438e5a62a3f797d6a97c9b296589cb",
    ),
    (
        meta_capi.hash_name_part,
        "O'Brien",
        "obrien",
        "b4cb6cb33fe4b865868de825023a1e2790dc12ac01ecc8d7c5afe8254071c8ba",
    ),
    # ACCENTS ARE PRESERVED, not folded to "nunez", and the digest is over
    # UTF-8 bytes. A well-meant "normalise to ASCII" would silently unmatch
    # every multicultural name we send.
    (
        meta_capi.hash_name_part,
        "Núñez",
        "núñez",
        "1aafcb92547a6d1d82dbb3e72eb5bf4d83f628aa06041547a1c0faff83d908e8",
    ),
    # A two-word surname from the live data — the case the 0094 name split was
    # built around. The internal space is REMOVED, not kept.
    (
        meta_capi.hash_name_part,
        "RAJI MOL",
        "rajimol",
        "0dd8bf025abbd608f700bdfa909d80dc5c5e0c66c6cb21399634fa8befb21c40",
    ),
    (
        meta_capi.hash_date_of_birth,
        "1990-04-17",
        "19900417",
        "2b76e296573f6543dd729c05838c1b49274cdbf64466e539e3eee1d7332cccc3",
    ),
    (
        meta_capi.hash_gender,
        "Female",
        "f",
        "252f10c83610ebca1a059c0bae8255eba2f95be4d1d7bcfa89d7248a82d9f111",
    ),
    # A real Ontario city carrying both a full stop and a space. "Toronto"
    # exercises neither.
    (
        meta_capi.hash_city,
        "St. Catharines",
        "stcatharines",
        "285e9e9c2c838fcd2b3f76d4d380f3b5a157806848734c97b46ea01e126ccd51",
    ),
    (
        meta_capi.hash_province,
        "Ontario",
        "on",
        "b8d31e852725afb1e26d53bab6095b2bff1749c9275be13ed1c05a56ed31ec09",
    ),
    (
        meta_capi.hash_postal_code,
        "M5H 1A1",
        "m5h1a1",
        "52033ce4057243da4a2903ea707a3184c06edbde746e41a16b17f2d6cc40a15b",
    ),
]


@pytest.mark.parametrize(
    "fn,raw,normalised,digest",
    GOLDEN,
    ids=[f"{fn.__name__}-{raw.strip()}" for fn, raw, _, _ in GOLDEN],
)
def test_the_wire_digest_never_drifts(fn, raw, normalised, digest):
    assert fn(raw) == digest, (
        f"{fn.__name__}({raw!r}) no longer hashes to sha256({normalised!r}). "
        "If the normalisation changed deliberately, every conversion sent "
        "since that change matched nobody at Meta."
    )


def test_an_absent_province_still_hashes_to_ontario():
    """Empty defaults to "on" rather than being omitted.

    That default is why province is deliberately absent from
    _REQUIRED_APPLICANT_FIELDS — an Ontario-only brokerage has one possible
    answer, so the broker is not asked for it and Meta still receives it.
    """
    assert meta_capi.hash_province(None) == (
        "b8d31e852725afb1e26d53bab6095b2bff1749c9275be13ed1c05a56ed31ec09"
    )


class TestTheAssembledPayload:
    """The two hashed fields no hash_* function owns.

    country and external_id are built inside build_user_data, so the
    per-function vectors above cannot reach them.
    """

    # Fixed, so the digest below can be literal. Any uuid would do; this one
    # is memorable and obviously not real data.
    LEAD_ID = "11111111-2222-3333-4444-555555555555"
    LEAD = {
        "id": LEAD_ID,
        "platform_leadgen_id": "99887766",
        "email": "a@b.com",
        "phone": None,
    }

    def test_external_id_is_the_hashed_lead_uuid(self):
        """THE ONE ASSERTION THE SUITE DID NOT HAVE. test_meta_capi.py checks
        that external_id is PRESENT (in its BASIC key tuple) but never what it
        contains — so a change that sent the raw uuid, or hashed the wrong
        column, would pass every existing test.
        """
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=None)

        assert ud["external_id"] == (
            "666ff6ccaa5b3c07feaa3a95d3a4bd2c46ac9e9abdb09ca9133528d3dc1e8952"
        )
        assert ud["external_id"] != self.LEAD_ID, "the uuid must never travel raw"

    def test_country_is_hashed_ca(self):
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=None)

        assert ud["country"] == (
            "6959097001d10501ac7d54c0bdb8db61420f658f2922cc26e46d536119a31126"
        )

    def test_lead_id_is_the_one_field_sent_in_the_clear(self):
        """platform_leadgen_id is Meta's OWN identifier for the lead, so it is
        matched by comparison, not by hash. Hashing it would break the
        strongest match key we send.
        """
        ud = meta_capi.build_user_data(lead=self.LEAD, driver=None)

        assert ud["lead_id"] == "99887766"
