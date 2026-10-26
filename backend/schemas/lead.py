from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

Stage = Literal[
    # 'In Progress' became a real stage in 0115 — it was lead.sub_stage until
    # then. Pipeline order, matching lead_stage_check and the rail.
    "New", "In Progress", "Quote Done", "Pending",
    "Sold", "Submitted", "Issued", "Lost",
]
# Mirrors the check constraint on lead.combined_policy_type in
# migrations/0068_policy_type_naming.sql, kept in sync BY HAND.
#
# The names state whether an AUTO policy is bundled in, which the previous
# vocabulary ("Homeowners", "Tenants") left the reader to know. Auto Only is
# what used to be called "Standalone".
CombinedPolicyType = Literal[
    "Auto Only",
    "Homeowner Only",
    "Tenant Only",
    "Auto and Homeowner",
    "Auto and Tenant",
    "Auto and Condo",
]

# WHICH PRODUCTS A POLICY TYPE COVERS — and therefore which premium fields it
# may hold, and whether it insures cars at all.
#
# Lives here beside CombinedPolicyType because it is the same vocabulary said
# a second way. It used to live in schemas/insurer.py, DERIVED from the
# commission rate columns each policy type needed ("auto_rate_pct" ->
# "auto"). That file is gone (0083), and the derivation went with it — the
# products a policy covers were never really a fact about commission rates,
# they only shared a shape.
#
# Written out literally now. Six lines, no cleverness: the keys mirror the
# check constraint on lead.combined_policy_type in
# migrations/0068_policy_type_naming.sql, kept in sync BY HAND, the same
# convention as schemas/property.py.
#
# A PRODUCT MISSING HERE FAILS SILENTLY, in the direction that loses a
# broker's work: its premium is REJECTED on save
# (services/premium_history.py::_refuse_products_outside_the_policy) and
# CLEARED on a policy-type switch (services/lead.py::_stale_premium_products).
# The figure would vanish with no error anywhere — so adding a policy type
# means adding it here in the same commit.
#
# Condo carries no product of its own: a condo is owner-occupied property, so
# it rides on the homeowner premium columns.
PRODUCTS_FOR: dict[str, frozenset[str]] = {
    "Auto Only": frozenset({"auto"}),
    "Homeowner Only": frozenset({"homeowner"}),
    "Tenant Only": frozenset({"tenant"}),
    "Auto and Homeowner": frozenset({"auto", "homeowner"}),
    "Auto and Condo": frozenset({"auto", "homeowner"}),
    "Auto and Tenant": frozenset({"auto", "tenant"}),
}

# What a broker CALLS each product. 'homeowner' is the column name; nobody
# says it out loud, and these words go into a warning read mid-call.
PRODUCT_LABEL: dict[str, str] = {
    "auto": "auto",
    "homeowner": "home",
    "tenant": "tenant",
}
# Which intake channel this lead came in on — see migrations/0017_lead_source.sql
# for why this is meta/website/tiktok and not split by Facebook/Instagram.
# 'manual' (0053) is the odd one out: the first three are channels the system
# ingests from, while a manual lead is a broker typing what a caller told them.
Source = Literal["meta", "website", "tiktok", "manual"]


class CreateLeadRequest(BaseModel):
    # group_id is required — every lead must have a routing target at
    # creation time (see house rule: resolved via lead_form lookup or
    # website_lead_rotation BEFORE this is called, never left to figure out
    # later). Not resolved here — the caller (future webhook ingestion, or
    # this manual test endpoint) already knows it.
    group_id: UUID
    ad_id: UUID | None = None
    # Required, no default: every caller already knows which channel it's
    # ingesting for (lead_ingestion.py sets "meta"; a future website/tiktok
    # ingestion path would set its own) — never left to guess.
    source: Source
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=1, max_length=50)
    email: EmailStr
    extra_data: dict | None = None
    # The Meta form's Ontario auto qualifier, in the form's own vocabulary
    # ("2020_or_before" / "2021_or_later"). Optional: website and phone leads
    # never answer it. Validated by a check constraint on the column, not here,
    # so a form that adds a third option fails loudly at the database rather
    # than being silently coerced by a Literal that nobody remembered to widen.
    licensed_in_canada: str | None = None
    combined_policy_type: CombinedPolicyType | None = None
    # When the CUSTOMER actually requested the lead (Meta's created_time from
    # the webhook) — distinct from created_at (when WE created the row, which
    # can lag ingestion retries). Optional: a manual/test creation may not
    # have a real request moment to report.
    requested_at: datetime | None = None
    # Meta's own id for this ONE form submission (see migrations/0054).
    # lead_ingestion.py already reads this to call the Graph API for the
    # submitted answers — this is what lets it also be kept, rather than
    # discarded the moment that call returns. Optional: a manual/test lead
    # has no Meta submission behind it.
    platform_leadgen_id: str | None = None


class ReassignLeadRequest(BaseModel):
    # Admin-only move of an already-assigned lead to a different broker (see
    # docs/round-robin-allocation-design.md §9) — never a broker self-claim.
    broker_id: UUID


class CreateManualLeadRequest(BaseModel):
    """A lead the broker types in themselves — a referral, a walk-in, someone
    who called the office.

    Three fields, because three is what creating a lead actually requires.
    Everything else follows from WHO is asking: group_id is the creator's own
    group, read from their token and never accepted from the caller, so a
    broker cannot file a customer into someone else's book.

    NO source field either. Everything created here is manual by definition,
    and a question with one answer is not worth asking.
    """

    name: str = Field(min_length=1, max_length=200)

    # PHONE OR EMAIL, not both. A broker mid-conversation has whichever the
    # customer gave them: a referral usually comes with a number, a web
    # enquiry with an address. Demanding both would mean inventing one.
    # The service fills whichever is missing with the "(not provided)"
    # placeholder the rest of the system already recognises — the columns are
    # NOT NULL and the list hides that value rather than showing it as data.
    phone: str | None = Field(None, max_length=50)
    email: EmailStr | None = None

    @model_validator(mode="after")
    def require_a_way_to_reach_them(self):
        if not self.phone and not self.email:
            raise ValueError("Enter a phone number or an email address")
        return self


class SetPolicyTypeRequest(BaseModel):
    """Whether this quote bundles a property, and which kind.

    'Auto Only' means no property. Anything else creates the applicant's own
    home and opens the property side of the quote.

    NOT nullable. Null means "the broker has not asked yet" and is the state a
    lead arrives in; it is not something they can choose. Answering the
    question is what this endpoint is for, so it always writes a real answer.
    """

    policy_type: CombinedPolicyType

    # Switching down — to Auto Only, or to a Tenant type while rentals exist —
    # destroys properties the broker may have spent a call collecting. The
    # request is refused with a 409 naming how many would be lost unless this
    # says the warning has been seen and accepted.
    confirm: bool = False


class SetQualificationRequest(BaseModel):
    """What the qualification dialog records — the broker's judgement, and the
    facts collected alongside it.

    A PARTIAL UPDATE, because the dialog SAVES ON EVERY TAP rather than once at
    the end. A mis-click closing it must not cost the answers already given, and
    reopening should show the broker their own work. So each step sends only its
    own field.

    THERE IS STILL NO 'SKIP' VALUE. Skipping is the broker closing the dialog,
    which calls nothing and leaves lead.is_qualified null — that null is what
    makes the question return on the next stage move. Storing "I did not answer"
    would be the same fact written twice.

    OMITTED AND EXPLICIT NULL ARE DIFFERENT THINGS HERE, which is why the
    validator below exists rather than the field simply being optional:

        {"is_qualified": false}         -> the judgement
        {"is_qualified": null}          -> 422. Clearing a judgement is not
                                           something this endpoint does, and a
                                           client that sent null by accident
                                           would otherwise erase it silently
        {}                              -> 422

    ONE FIELD, THOUGH THE MODEL IS SHAPED FOR SEVERAL. It carried
    vehicle_count_declared until the dialog's vehicles question was removed —
    nothing read the count, and the multi-car dataset it was collected for was
    considered and rejected. The optional-with-validator shape is kept because
    the dialog still saves answer by answer and the next fact collected here will
    need exactly this treatment; the column itself remains in the database,
    simply never written.

    The other two dialog answers do NOT come through here: convictions and the
    licence question are facts about the PERSON, so they patch the driver
    (PATCH /drivers/{id}).

    Only asked of META leads: an unqualified one sends nothing to Meta at all,
    while a manual lead has no conversion to attach an answer to (see
    services/lead.py::_needs_qualifying).
    """

    is_qualified: bool | None = None

    @model_validator(mode="after")
    def _reject_empty_and_explicit_null(self):
        """Nothing sent is a mistake; null sent on purpose is a worse one.

        model_fields_set is the only way to tell `{"is_qualified": null}` from a
        request that simply omitted it — both leave the attribute None, and one
        of them must not be allowed to clear a stored judgement.
        """
        if not self.model_fields_set:
            raise ValueError(
                "Send at least one answer — an empty request writes nothing"
            )
        if "is_qualified" in self.model_fields_set and self.is_qualified is None:
            raise ValueError(
                "is_qualified cannot be cleared — send true or false, or omit it"
            )
        return self


class UpdateStageRequest(BaseModel):
    # The broker moving their own lead through the pipeline (New -> Quote Done
    # -> ... -> Issued/Lost). Deliberately free-form: any stage to any other
    # stage, with no terminal-state lock — a customer who goes quiet after a
    # quote and comes back is an ordinary week, not an exception.
    #
    # ONE RULE: Quote Done and beyond require a premium, because Quote Done
    # fires Meta's CompleteRegistration and value is required on it.
    #
    # HISTORY IS KEPT. Every move appends to lead_stage_history (0029) with
    # from-stage, to-stage, broker and timestamp — this comment claimed
    # otherwise until 2026-08-07, having outlived the migration that changed
    # it. The stages listed were stale too ("Quote Ready" was removed in 0051).
    #
    # Reuses the same Stage literal as CreateLeadRequest, so this can never
    # accept a value the lead table's own CHECK constraint would reject.
    stage: Stage

    # THE DAY THE CARRIER WROTE THE POLICY — required when and only when the
    # stage is Issued, which is why it is optional here: the six other moves
    # have no date to give, and a required field would break every one of
    # them. services/lead.py::update_stage enforces the pairing.
    #
    # NOT DEFAULTED TO TODAY. The default belongs in the dialog, where the
    # broker can see it and change it; defaulting server-side would silently
    # invent a date for a client that forgot to send one — which is exactly
    # the behaviour this replaces. issued_at used to be set automatically to
    # today, so a policy written on the 30th and recorded on the 2nd landed in
    # the following month with nothing able to correct it.
    issued_at: date | None = None

    # THE BROKER HAS SEEN THE LOW-PREMIUM WARNING AND MEANT IT.
    #
    # Moving to Quote Done with a proposed AUTO premium under $1,000 a year is
    # refused with a 409 naming the figure, because it is far more likely to be
    # a monthly number entered as annual than a real policy. Sending this says
    # the question was asked and answered.
    #
    # Same shape as SetPolicyTypeRequest.confirm, which guards a destructive
    # policy-type switch — one pattern for "are you sure", not two.
    #
    # AUTO ONLY, and proposed only. Small home and tenant premiums are
    # ordinary — five of the fourteen live rows hold one — so warning on those
    # would teach brokers to click through the warning that matters.
    confirm: bool = False

    # A finer label INSIDE the stage (0092), not a stage of its own: 'Junk'
    # distinguishes a spam or duplicate lead from a genuine opportunity that
    # did not convert, and 'In Progress' marks a New lead someone has started.
    #
    # Optional for the same reason issued_at is — six of the seven moves have
    # no sub-stage to give, and the pairing (Junk only with Lost, In Progress
    # only with New) is enforced in services/lead.py::update_stage beside the
    # issued_at rule it is modelled on.
    #
    # A LITERAL, so a value the 0092 CHECK constraint would reject is refused
    # by Pydantic first — the same reasoning as the Stage literal above.
    # Sending null is meaningful: it clears the label back to the plain stage.
    # 'Junk' alone since 0115. 'In Progress' was the other value until it became
    # a stage of its own; it is refused here AND by lead_sub_stage_check, so an
    # old client cannot write the label back.
    sub_stage: Literal["Junk"] | None = None


class AdjustCallCountRequest(BaseModel):
    """One press of a call counter's + or -.

    A DELTA, NOT A TOTAL. The client sends "one more" rather than "make it 3",
    so two brokers pressing at the same moment both count — where a total
    would have the second overwrite the first. The arithmetic itself happens
    in SQL (adjust_lead_call_count, migration 0121), never read-then-write in
    Python.
    """

    # Which counter. These two literals are the ONLY values that reach the SQL
    # function's column name, mapped in services/lead.py — client text is never
    # interpolated into a column position.
    outcome: Literal["answered", "no_answer"]

    # +1 or -1, nothing else. Not an arbitrary int: a stepper can only ever
    # mean "one more" or "take one back", and accepting 500 would let a typo
    # or a replayed request put a nonsense figure in front of a broker.
    # Minus at zero is a no-op, clamped by greatest(0, ...) in 0121.
    delta: Literal[1, -1]


class CallCountResponse(BaseModel):
    """Both counters after the change — the authoritative figures.

    BOTH, not just the one adjusted, so the client can replace its optimistic
    guess with the truth in one assignment and never hold a half-updated pair.

    There is no LeadResponse in this codebase; lead reads return raw rows. This
    is deliberately the narrow thing the stepper needs, not a whole lead.
    """

    answered_count: int
    no_answer_count: int
