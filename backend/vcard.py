"""vCard 3.0 text, built to RFC 2426.

Sits at the backend root beside meta_capi.py — same reason:
this is a wire format, not a layer of our own domain. It touches no database and
raises no HTTPException, which is what makes it exhaustively unit-testable, and
test_vcard.py exercises every rule below.

WHY 3.0 AND NOT 4.0. RFC 6352 section 6.3.2.1: absent a server-advertised
supported-address-data property, a CardDAV server "MUST only accept data with
the media type text/vcard and vCard version 3.0". iOS Contacts is 3.0-native and
Radicale advertises nothing, so 3.0 is both the compatible choice and the
required one.

THE FOUR RULES THAT BREAK SILENTLY. Every one of these produces a card that
looks fine in a text editor and is wrong on a phone:

  1. Escaping order. A backslash must be escaped BEFORE the characters whose
     escapes introduce backslashes, or you escape your own escape sequences.
  2. CRLF. RFC 2426 delimits lines with CRLF, not LF. A card with bare newlines
     is rejected by strict parsers and silently mangled by lenient ones.
  3. Folding counts OCTETS, not characters, and must never split a UTF-8
     sequence in half — which is invisible until a name has an accent in it.
  4. N is a structured field of five components. Escaping has to apply inside
     each component, never to the semicolons separating them.
"""

import hashlib
import re

# Appended to the display name so a ringing name is recognisably a work lead
# rather than someone from the broker's own contacts. Kept as a constant because
# the sync loop's content hash changes the moment this does — every card is
# rewritten and every device redownloads, so this is not a value to edit
# casually.
DISPLAY_SUFFIX = " (PS)"

# Lines SHOULD be folded at 75 octets (RFC 2426 section 2.6, citing RFC 2425).
_FOLD_OCTETS = 75

# The Python twin of the frontend's isPlaceholderContact (domain/leadStages.ts).
# lead.phone and lead.email are NOT NULL, so a lead captured without one carries
# a stand-in string rather than a null — and writing that stand-in onto a card
# gives a broker a contact whose email address reads "not provided".
#
# Kept deliberately in step with the frontend regex, including the variants
# beyond "(not provided)": a lead entered by hand can carry "N/A" or "unknown"
# just as easily, and those must not reach a phone either.
_PLACEHOLDER = re.compile(
    r"^\(?\s*(not provided|not available|n/?a|unknown|none)\s*\)?$",
    re.IGNORECASE,
)


def is_placeholder(value: str | None) -> bool:
    """True when a contact field holds a stand-in rather than a real value."""
    if not value or not value.strip():
        return True
    return bool(_PLACEHOLDER.match(value.strip()))


def _escape(value: str) -> str:
    """Escape one TEXT value per RFC 2426 section 5.

    THE BACKSLASH GOES FIRST and the order is load-bearing. Escaping ";" to
    "\\;" introduces backslashes; if the backslash rule ran afterwards it would
    escape those too, turning "\\;" into "\\\\;" — a literal backslash followed
    by an unescaped semicolon, which splits the value into two components.
    """
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def _fold(line: str) -> list[str]:
    """Fold one logical line into physical lines of at most 75 octets.

    Continuation lines begin with a single space, which the parser strips.

    COUNTED IN OCTETS, NOT CHARACTERS, and never splitting a multi-byte UTF-8
    sequence — RFC 2426: "multi-octet characters MUST remain contiguous". A
    naive line[:75] passes every ASCII test and corrupts the first accented
    name it meets, because the phone receives half a codepoint and drops or
    mojibakes the whole property.

    The continuation space costs one octet, so the payload budget after the
    first line is 74.
    """
    raw = line.encode("utf-8")
    if len(raw) <= _FOLD_OCTETS:
        return [line]

    out: list[str] = []
    start = 0
    limit = _FOLD_OCTETS
    while start < len(raw):
        end = min(start + limit, len(raw))
        # Back off to a codepoint boundary. Continuation bytes are 10xxxxxx;
        # walking back off them lands on the byte that starts the character.
        while end > start and end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        chunk = raw[start:end].decode("utf-8")
        out.append(chunk if not out else " " + chunk)
        start = end
        limit = _FOLD_OCTETS - 1  # the leading space eats one octet
    return out


def vcard_uid_for_lead(lead_id: str) -> str:
    """The card's identity, derived from the lead rather than generated.

    Deterministic on purpose: the same lead always produces the same UID, so a
    card re-downloaded after an edit REPLACES the one already in the broker's
    address book rather than landing beside it as a duplicate.

    Per-lead, not per-(lead, broker) — a reassigned lead keeps its identity as
    it moves.
    """
    return f"myday-{lead_id}"


def content_hash(vcard_text: str) -> str:
    """Fingerprint of an exact card body.

    This is how the sync loop detects "nothing changed" without a round-trip:
    rebuild the card, hash it, compare to what was last written. Without it
    every cycle re-PUTs every card, and every PUT bumps Radicale's sync token,
    so every broker's phone redownloads their whole address book on a schedule.
    """
    return hashlib.sha256(vcard_text.encode("utf-8")).hexdigest()


def build_vcard(
    *,
    uid: str,
    name: str,
    phone_e164: str | None,
    email: str | None = None,
) -> str:
    """Render one contact as vCard 3.0 text.

    Takes primitives rather than a lead row so a test can pass
    name="O'Brien; Jr." without constructing a database record.

    A NULL PHONE RAISES rather than producing a card. A contact with no number
    can never match an incoming call, so it is pure noise in a broker's address
    book — the service layer skips those before reaching here, and this raise is
    what makes a bug past that guard fail loudly instead of quietly writing
    useless cards. Note phone_e164 is null for two ordinary reasons: the
    placeholder "(not provided)", and any non-NANP number, because
    normalize_phone_e164 is NANP-only by design (migration 0058).

    A NULL EMAIL IS ORDINARY — the line is simply left out. So is a placeholder
    email, which must never be written through as though it were an address.
    """
    if not phone_e164 or not phone_e164.strip():
        raise ValueError("a vCard needs a phone number: it exists to match a call")

    display = f"{name}{DISPLAY_SUFFIX}"

    # N's five components are family;given;additional;prefix;suffix.
    #
    # THE NAME IS NOT SPLIT into given and family. This schema has a single
    # `name` column, and guessing the split from whitespace is wrong often
    # enough — compound surnames, single-word names, "Last, First" entries — to
    # produce visibly wrong contacts. The whole name goes in `given` and the
    # marker in `suffix`. The call screen renders FN, which carries both.
    n_field = ";".join(["", _escape(name), "", "", _escape(DISPLAY_SUFFIX.strip())])

    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"UID:{_escape(uid)}",
        f"FN:{_escape(display)}",
        f"N:{n_field}",
        # E.164 with the leading "+". Android's caller-ID matcher compares
        # right-to-left over the last 7 digits (PhoneNumberUtils.compareLoosely,
        # config_phonenumber_compare_min_match), so +14165551234 matches a call
        # presented as 4165551234. iOS's matcher is undocumented but observed to
        # behave the same. CELL so Android maps it to TYPE_MOBILE.
        f"TEL;TYPE=CELL,VOICE:{_escape(phone_e164.strip())}",
    ]

    if not is_placeholder(email):
        # email is checked, not asserted: a placeholder reaching this point
        # would put "not provided" in the broker's Contacts as an address.
        lines.append(f"EMAIL;TYPE=INTERNET:{_escape(email.strip())}")  # type: ignore[union-attr]

    lines.append("END:VCARD")

    physical: list[str] = []
    for line in lines:
        physical.extend(_fold(line))

    # Joined AND terminated with CRLF, including the final line.
    #
    # Never "\n".join(...) followed by .replace("\n", "\r\n") — that would also
    # rewrite the escaped literal "\n" inside a value, corrupting any name or
    # email that legitimately contained a newline.
    return "\r\n".join(physical) + "\r\n"
