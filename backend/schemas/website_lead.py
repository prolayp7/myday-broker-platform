"""What the website sends, and what the Website leads admin screen reads and
writes.

THE PAYLOAD IS A CONTRACT with the website's server (its Cloudflare Worker),
agreed field by field before either side was built. Every key is always
present; a question the customer was not asked arrives as null, never missing.
The website validates the same rules before it sends, so a 422 here means the
two sides have drifted — which is exactly what should be loud.

Validated HERE, before the submission is stored. The drain that turns it into a
lead (migration 0127, assign_waiting_website_leads) trusts what was stored, and
anything it still cannot use is marked failed rather than retried.
"""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

# The website's own vocabulary. Deliberately NOT lead.combined_policy_type's —
# the CRM owns that, and turning one into the other happens on the CRM's side
# (the broker picks the policy type; see 0127 for why it is not set at intake).
Coverage = Literal["auto", "home", "tenant", "auto-home", "auto-tenant"]

# Tickets, at-fault claims and home claims all use the same four answers.
History = Literal["none", "1", "2", "3-plus"]

# Which coverages ask which questions. The website's form asks the driving
# questions for any coverage with a car in it, and home claims only for an
# owned home — a tenant is never asked either.
_ASKS_DRIVING: frozenset[str] = frozenset({"auto", "auto-home", "auto-tenant"})
_ASKS_HOME_CLAIMS: frozenset[str] = frozenset({"home", "auto-home"})

# submitted_at, exactly as JavaScript's toISOString() writes it.
_UTC_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z")


class WebsiteLeadPayload(BaseModel):
    """One quote-form submission, exactly as the website's server forwards it.

    extra='ignore', not 'forbid'. The raw body is stored whole regardless, so
    nothing is lost; forbidding would turn a harmless new field on the website
    into a customer whose details never arrive.

    No defaults anywhere: `X | None` without a default is required-but-nullable
    in Pydantic, which is precisely "every key always present".

    STRICT ENOUGH THAT THE RAW BODY IS SAFE TO READ. The drain (0127) builds
    the lead from the STORED body, not from this model — the body is kept
    verbatim as the record of what the customer sent. So anything this model
    would quietly normalise has to be refused instead: a timestamp sent as a
    number, an email written "Name <address>", a digit from another script.
    Otherwise it would pass here, be answered "received", and then either fail
    in the drain or reach the broker in a shape nobody checked. `[0-9]` rather
    than `\\d` for the same reason: Pydantic's regex `\\d` matches fullwidth
    and Arabic-Indic digits, which no dialler does.
    """

    model_config = ConfigDict(extra="ignore")

    version: Literal[1]
    # The idempotency key. Created once when the customer presses Send and
    # reused on every retry, so a double-click or a network retry cannot make
    # two leads — webhook_event.dedup_key is built from it.
    submission_id: UUID
    submitted_at: datetime
    coverage: Coverage

    name: str = Field(min_length=1, max_length=200)
    # The website normalises to +1 and ten digits before sending. Required in
    # that exact form so a number arrives dialable, not as whatever was typed.
    phone: str = Field(pattern=r"^\+1[0-9]{10}$")
    email: EmailStr

    address: str = Field(min_length=1, max_length=500)
    # "lat,lng" from Google's suggestion, or null when the address was typed.
    address_location: str | None = Field(
        pattern=r"^-?[0-9]{1,3}(\.[0-9]+)?,-?[0-9]{1,3}(\.[0-9]+)?$", max_length=64
    )
    # 'asap', or a one-hour Toronto slot written YYYY-MM-DDTHH. The shape here;
    # that it is a real day is checked below.
    call_time: str = Field(pattern=r"^(asap|[0-9]{4}-[0-9]{2}-[0-9]{2}T([01][0-9]|2[0-3]))$")

    convictions: History | None
    auto_claims: History | None
    home_claims: History | None

    # The notice shown under the Send button. It is a notice, not a checkbox,
    # so this records what the customer was shown — never that they ticked
    # anything. Stored with submitted_at in the raw payload, kept forever.
    consent_notice: str = Field(min_length=1, max_length=2000)
    page: str = Field(min_length=1, max_length=500)

    @field_validator("submitted_at", mode="before")
    @classmethod
    def _submitted_at_is_utc_iso(cls, value):
        """Exactly what the website sends — JavaScript's toISOString(),
        2026-09-29T14:32:05.123Z — and nothing else.

        The drain casts the stored raw value with ::timestamptz, so anything
        Pydantic or fromisoformat would accept but Postgres would not has to be
        refused here, or the customer is told "received" and the lead then
        fails. Both are wider than Postgres: a Unix number, a comma fraction,
        an offset of +20:00 (Postgres stops at ±15:59). One exact shape closes
        all of them; the parse after it catches a month 13."""
        if not isinstance(value, str) or not _UTC_ISO.fullmatch(value):
            raise ValueError("submitted_at must be a UTC ISO 8601 string ending in Z")
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @field_validator("email", mode="before")
    @classmethod
    def _email_is_just_an_address(cls, value):
        """EmailStr accepts "Priya Sharma <priya@example.com>" and returns the
        bare address — but the drain reads the STORED value, so the lead would
        get the whole string. Refused, so what is validated is what is used."""
        if isinstance(value, str) and any(c in value for c in "<> \t\r\n"):
            raise ValueError("email must be a bare address")
        return value

    @field_validator("call_time")
    @classmethod
    def _call_time_is_a_real_day(cls, value: str) -> str:
        """2026-02-30 has the right shape and is not a day. The drain parses it
        with to_timestamp, which refuses it — after the customer has already
        been told "received"."""
        if value != "asap":
            try:
                datetime.strptime(value, "%Y-%m-%dT%H")
            except ValueError:
                raise ValueError("call_time is not a real day and hour") from None
        return value

    @model_validator(mode="after")
    def _answers_match_the_coverage(self):
        """A question is answered exactly when the coverage asks it.

        Both directions matter. A missing answer the form should have collected
        is a website bug; an answer to a question the form never asks means the
        two sides disagree about what the form is — either way the lead would
        reach a broker describing something the customer did not say.
        """
        driving = self.coverage in _ASKS_DRIVING
        for field in ("convictions", "auto_claims"):
            answered = getattr(self, field) is not None
            if answered != driving:
                raise ValueError(
                    f"{field} must be {'answered' if driving else 'null'} "
                    f"for coverage '{self.coverage}'"
                )

        home = self.coverage in _ASKS_HOME_CLAIMS
        if (self.home_claims is not None) != home:
            raise ValueError(
                f"home_claims must be {'answered' if home else 'null'} "
                f"for coverage '{self.coverage}'"
            )
        return self


# ── The admin screen ─────────────────────────────────────────────────────


class WebsiteBrokerRow(BaseModel):
    broker_id: str
    name: str
    group_id: str
    group_name: str | None
    is_on: bool
    daily_cap: int
    # Website leads received since the last daily reset — the number the
    # website limit is compared against.
    today: int
    self_paused: bool
    is_blocked_by_admin: bool
    is_active: bool
    # Added by migration 0129. OPTIONAL, deliberately: a board built by 0127's
    # function does not carry them, and a required field would turn "0129 not
    # applied yet" into a 500 on the whole screen.
    community_id: str | None = None
    community_name: str | None = None
    # When the broker starts receiving leads. The drain skips them until then,
    # so the screen needs it to say why a switched-on broker gets nothing.
    eligible_from: datetime | None = None


class WebsiteLeadBoardResponse(BaseModel):
    # Submissions stored but not yet given to anyone: every switched-on broker
    # is paused or at their limit, or nobody is switched on.
    waiting: int
    # Submissions that can never become a lead as sent. Needs a person.
    failed: int
    brokers: list[WebsiteBrokerRow]


class UpdateWebsiteBrokerRequest(BaseModel):
    """Either field or both. The screen saves the switch on click and the
    limit on blur, so each sends only what changed."""

    model_config = ConfigDict(extra="forbid")

    is_on: bool | None = None
    # A sanity ceiling, not a business rule: a typo of 500 for 5 should be
    # refused rather than open the floodgates to one broker.
    daily_cap: int | None = Field(default=None, ge=0, le=200)

    @model_validator(mode="after")
    def _something_to_change(self):
        if self.is_on is None and self.daily_cap is None:
            raise ValueError("Send is_on, daily_cap, or both")
        return self


class UpdateWebsiteBrokersRequest(BaseModel):
    """The same change for many brokers at once — "switch on everyone shown".

    One request so it is one transaction (0129, set_website_broker_settings):
    every broker listed changes, or none does.
    """

    model_config = ConfigDict(extra="forbid")

    # A ceiling far above any real office, there only so one request cannot
    # carry an unbounded list.
    broker_ids: list[UUID] = Field(min_length=1, max_length=500)
    is_on: bool | None = None
    daily_cap: int | None = Field(default=None, ge=0, le=200)

    @field_validator("broker_ids")
    @classmethod
    def _each_broker_once(cls, value: list[UUID]) -> list[UUID]:
        """A repeated id means the caller built its list wrongly. Refused
        rather than quietly de-duplicated, so the count it sent is the count
        that changed."""
        if len(set(value)) != len(value):
            raise ValueError("broker_ids must not repeat a broker")
        return value

    @model_validator(mode="after")
    def _something_to_change(self):
        if self.is_on is None and self.daily_cap is None:
            raise ValueError("Send is_on, daily_cap, or both")
        return self


class WebsiteBrokerSettingResponse(BaseModel):
    broker_id: str
    is_on: bool
    daily_cap: int
