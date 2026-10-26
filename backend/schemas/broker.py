from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

# The 4 product lines a broker can be licensed for. Matches the check
# constraint on broker.lines_of_authority in migration 0003.
LineOfAuthority = Literal["Auto", "Travel", "Business", "Life"]
# Two tiers, not three. `manager` was dropped in migrations/0066 — it granted
# exactly what `admin` granted (require_admin accepted both), so it was a
# permission tier that could only ever mislead. Matches the check constraint
# on broker.role.
Role = Literal["broker", "admin"]

# An E&O (errors & omissions) policy term runs at most one year. A broker can
# join mid-term (so a shorter term is valid), but a term longer than a year is
# a data-entry mistake, not a real policy.
MAX_EO_TERM = timedelta(days=366)  # 366 so a full leap-year term isn't wrongly rejected

# Leads are distributed against a broker's daily_cap; a cap above this isn't a
# real workload, it defeats the point of capping.
MAX_DAILY_CAP = 25


class _BrokerProfileFields(BaseModel):
    """Fields (and their cross-field rules) shared by create and update, so the
    E&O-term checks live in one place instead of being duplicated and drifting
    apart.

    daily_cap is deliberately NOT here — see docs/round-robin-allocation-design.md
    §6. Once round-robin allocation is live, daily_cap is system-managed: only
    the daily reset job writes it (promoting pending_daily_cap). Editing an
    EXISTING broker's cap goes through SetDailyCapRequest below (deferred to
    next reset), never through this shared profile-edit shape. CreateBrokerRequest
    is the one exception — a brand-new broker isn't live until eligible_from
    (tomorrow), so setting daily_cap directly at onboarding is safe."""

    date_of_birth: date | None = None
    address: str | None = None

    ribo_license_number: str | None = None
    lines_of_authority: list[LineOfAuthority] = []
    resident_province: str = "Ontario"
    license_expiry_date: date | None = None
    eo_start_date: date | None = None
    eo_expiry_date: date | None = None

    @model_validator(mode="after")
    def _check_eo_term(self):
        # Both E&O dates must be present together — an admin entering one but
        # not the other is a half-filled record, not a valid partial state.
        if (self.eo_start_date is None) != (self.eo_expiry_date is None):
            raise ValueError("Enter both E&O start and end dates, or neither")

        if self.eo_start_date is not None and self.eo_expiry_date is not None:
            if self.eo_expiry_date <= self.eo_start_date:
                raise ValueError("E&O end date must be after the start date")
            if self.eo_expiry_date - self.eo_start_date > MAX_EO_TERM:
                raise ValueError("E&O term can't be longer than one year")

        return self



class CreateBrokerRequest(_BrokerProfileFields):
    # Identity (required). No password field: the system generates a one-time
    # temporary password at onboarding (returned once in the response) — the
    # admin never chooses it, and the broker is forced to replace it on first
    # login (must_change_password). See services/broker.py::create_broker.
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: str = Field(min_length=1, max_length=50)
    role: Role = "broker"

    # Safe to set directly here (unlike SetDailyCapRequest's deferred
    # write) — a new broker isn't eligible for leads until tomorrow anyway
    # (see services/broker.py setting eligible_from), so there's no "mid-day"
    # to protect against.
    daily_cap: int = Field(default=10, ge=0, le=MAX_DAILY_CAP)
    # Group-2 overtime ceiling — see design §8. Defaults to the same value as
    # daily_cap's default: no overtime beyond the normal cap until an admin
    # raises it.
    max_cap: int = Field(default=10, ge=0, le=MAX_DAILY_CAP)

    @model_validator(mode="after")
    def _check_cap_ordering(self):
        # max_cap is the TOTAL delivery ceiling (guaranteed daily + overtime),
        # so it can never sit below daily_cap — a broker with max < daily
        # would silently never qualify for Group-2 overtime, which reads like
        # a bug to an admin. Enforced here at creation; the deferred edit
        # endpoints deliberately don't cross-check (the UI schedules both caps
        # in parallel calls, so a server-side cross-check would race against
        # itself and false-reject a legitimate simultaneous raise — the
        # ScheduleCapDialog validates the pair client-side instead, and the
        # worst case of a raw-API mismatch is benign: no overtime, never a
        # crash or over-delivery).
        if self.max_cap < self.daily_cap:
            raise ValueError("Max cap can't be lower than the daily cap")
        return self


class MoveBrokerRequest(BaseModel):
    group_id: UUID


class BlockBrokerRequest(BaseModel):
    # Required — a block without a recorded reason isn't useful to anyone
    # reviewing it later.
    reason: str = Field(min_length=1, max_length=500)


class UpdateBrokerRequest(_BrokerProfileFields):
    # Editing a broker's profile — everything CreateBrokerRequest has except
    # password (that's a separate, deliberate flow, not a profile edit),
    # group_id (moving groups is its own endpoint, with cap-checking), and
    # daily_cap/max_cap (their own deferred endpoints below, per §6).
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: str = Field(min_length=1, max_length=50)
    role: Role = "broker"
class SetDailyCapRequest(BaseModel):
    # Writes pending_daily_cap + pending_daily_cap_effective_at — NEVER
    # daily_cap directly (§6). Promoted by the daily reset job once
    # effective_at arrives. daily_cap itself is system-managed from this
    # point on for an existing broker.
    daily_cap: int = Field(ge=0, le=MAX_DAILY_CAP)
    effective_at: datetime

    @model_validator(mode="after")
    def _check_future(self):
        if self.effective_at <= datetime.now(self.effective_at.tzinfo):
            raise ValueError("Effective date must be in the future")
        return self


class SetMaxCapRequest(BaseModel):
    # Group-2 overtime ceiling. Same deferred pattern as daily_cap — a
    # same-day lowering could otherwise drop below overtime leads already
    # delivered today, exactly the mid-day-cut problem §6 exists to prevent.
    max_cap: int = Field(ge=0, le=MAX_DAILY_CAP)
    effective_at: datetime

    @model_validator(mode="after")
    def _check_future(self):
        if self.effective_at <= datetime.now(self.effective_at.tzinfo):
            raise ValueError("Effective date must be in the future")
        return self


class ScheduleUnpauseRequest(BaseModel):
    # Deferred unblock (§5). Pausing (block_broker) stays INSTANT — see
    # routes/broker.py's existing /block endpoint, unchanged. But unblocking
    # is no longer instant: this writes scheduled_unpause_at only; the daily
    # reset job promotes it (clears is_blocked_by_admin) once effective_at
    # arrives, so leads resume the day AFTER, same regime as a brand-new
    # broker's first day.
    effective_at: datetime

    @model_validator(mode="after")
    def _check_future(self):
        if self.effective_at <= datetime.now(self.effective_at.tzinfo):
            raise ValueError("Effective date must be in the future")
        return self


class SetReadyForMoreRequest(BaseModel):
    # Group-2 daily opt-in (§8) — "I'm ready for overtime leads today."
    # Takes effect immediately (it's the broker/admin's live choice for
    # TODAY, not a scheduled change) and is cleared every morning by the
    # reset job, so it never silently carries over to a day nobody chose.
    #
    # "Immediately" includes the BACKLOG: switching it on offers the leads
    # already waiting in the group, not only the ones that arrive afterwards.
    ready_for_more: bool


class SetSelfPausedRequest(BaseModel):
    """A broker stopping or resuming their own leads.

    ONE FIELD, AND NO BROKER ID. The id comes from the session token, so there
    is no way to shape a body that pauses somebody else — the same reasoning as
    UpdateMyProfileRequest below. An admin stopping another broker's leads is a
    different act with a different route (PATCH /brokers/{id}/block), which
    records who did it and why.

    extra="forbid" so a body carrying broker_id is REFUSED rather than quietly
    ignored. Only `paused` reaches the service either way, but someone trying
    to pause a colleague should be told no, not handed a 200 that did something
    else.
    """

    model_config = ConfigDict(extra="forbid")

    paused: bool


class AvailabilityResponse(BaseModel):
    """What changed, and whether the leads already waiting were handed out.

    NARROW ON PURPOSE — not the broker row. This endpoint answers one question
    and returning a whole profile from it would put licence numbers and caps
    behind a toggle that has no business carrying them.

    backlog_drained is FALSE ONLY as information, never as an error. The pause
    itself is already saved by the time the drain runs, so reporting a drain
    failure with a 4xx/5xx would make the frontend roll the switch back over a
    change the database kept — see services/broker.py::set_my_self_paused.
    """

    paused: bool
    backlog_drained: bool


class MyProfileGroup(BaseModel):
    """The broker's group, named rather than identified by uuid."""

    name: str
    group_number: int


class MyProfileResponse(BaseModel):
    """A broker's own profile — what the system knows about THEM.

    A DENY-BY-DEFAULT LIST. This model is spelled out field by field rather
    than built from SAFE_COLUMNS, so a column added to the broker table never
    starts appearing on a broker's screen by itself. Everything absent below
    is absent on purpose:

      * password_hash, totp_secret        never leave the server at all
      * failed_login_count, locked_until  security internals; showing a broker
                                          their own lockout counter tells an
                                          attacker who reached the session how
                                          close they are
      * is_blocked_by_admin, blocked_*    why an admin blocked someone is a
                                          conversation with that admin, not a
                                          panel the blocked person opens
      * is_archived, archived_at          an archived broker cannot log in
      * id, group_id                      internal keys

    The route reads the id from the SESSION, so this is always the caller's
    own row — there is no path here to another broker's profile.
    """

    name: str
    email: str
    role: str
    phone: str | None = None
    date_of_birth: date | None = None
    address: str | None = None
    group: MyProfileGroup | None = None

    # A signed, short-lived link to the broker's photo — the header avatar,
    # which otherwise falls back to their initials. Null when no photo has been
    # attached, and also when storage cannot be reached: a decorative image is
    # never worth failing this request over.
    #
    # NOT a stored URL. The bucket blocks public access, so the link is signed
    # per request and expires; there is nothing durable to keep on the row.
    # Only an admin can upload one (routes/broker_document.py) — a broker does
    # not choose their own compliance photo.
    photo_url: str | None = None

    # Licensing — the block with deadlines attached. A lapsed RIBO licence or
    # E&O policy is a regulatory problem, and nothing else in the app surfaces
    # either date.
    ribo_license_number: str | None = None
    lines_of_authority: list[str] = []
    resident_province: str | None = None
    license_expiry_date: date | None = None
    eo_start_date: date | None = None
    eo_expiry_date: date | None = None

    # Lead capacity, including the scheduled changes — a raise agreed with an
    # admin is otherwise invisible until the morning it takes effect.
    daily_cap: int | None = None
    max_cap: int | None = None
    pending_daily_cap: int | None = None
    pending_daily_cap_effective_at: datetime | None = None
    pending_max_cap: int | None = None
    pending_max_cap_effective_at: datetime | None = None
    availability_status: str | None = None
    ready_for_more: bool = False

    # Whether the broker has stopped their own leads. REQUIRED, not defaulted
    # — and that is the point of it being here at all.
    #
    # This model is deny-by-default, so a field missing from it is stripped
    # from the response silently. With `= False` the failure would be a quiet
    # lie: the database holds self_paused = true, /my-profile drops it, and the
    # header renders the switch OFF while leads really are stopped. The broker
    # sits waiting for work that is not coming.
    #
    # The column is NOT NULL, so requiring it means forgetting it in
    # SAFE_COLUMNS fails loudly in a test instead.
    self_paused: bool


class UpdateMyProfileRequest(BaseModel):
    """The only thing a broker may change about themselves.

    PHONE ONLY. Name, licence numbers, caps and the work line are all ADMIN
    decisions — a broker editing their own RIBO number or daily cap would
    route around the person whose job it is to set them. Their own contact
    number is the one field here that is genuinely theirs to correct.

    extra="forbid" so a request carrying daily_cap or role is REFUSED rather
    than quietly ignored. Pydantic's default drops unknown fields, which is
    safe here — only phone reaches the repository — but a caller trying to
    raise their own cap should be told no, not handed a 200 that silently did
    something else.
    """

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=1, max_length=50)
