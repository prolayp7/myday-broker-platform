"""Data access for the lead table. Only talks to Supabase."""

from db import supabase


# Columns the ADMIN Leads table renders — kept tight (not select *) so the
# payload stays small at scale. The two embeds resolve the FK ids to NAMES the
# screen shows: group_id -> broker_group(name, community(name)),
# assigned_broker_id -> broker(name). Both are null-safe: an unassigned lead's
# broker embed comes back null (rendered as "Unassigned"). The embeds are the
# EXPENSIVE part (measured ~124ms of a ~208ms query) — worth it for admin,
# which genuinely shows many DIFFERENT brokers/groups per page.
_LEAD_LIST_SELECT = (
    "id, group_id, assigned_broker_id, source, name, phone, email, stage, "
    "combined_policy_type, requested_at, assigned_at, assigned_by, created_at, "
    "broker_group(id, name, group_number, community(name)), "
    "broker(id, name)"
)

# Columns for a broker's OWN leads (/leads/mine) — public: services/lead.py
# passes this into list_leads for the broker path. No broker/broker_group
# embeds: on their own list every row's holder is themselves and the group is
# their own — fetching those on every row is pure dead weight (~9ms/request
# measured). extra_data IS included (unlike the admin list): the broker's
# detail panel shows Meta's question answers for the selected lead.
# is_qualified is here because the SCREEN needs it: a Meta lead with no answer
# yet cannot be moved past Quote Done (services/lead.py::update_stage), so the
# broker's own list is where the dialog decides whether to ask. Without it the
# frontend would only discover the requirement by being refused with a 422.
MY_LEAD_SELECT = (
    # phone_e164 as well as phone: wa.me needs a full international number, and
    # stripping punctuation off "4168274934" gives a number WhatsApp cannot
    # route. The trigger in 0058 already maintains the E.164 form; the mobile
    # screen was building its own from digits and getting it wrong.
    "id, group_id, source, name, phone, phone_e164, email, stage, sub_stage, "
    "combined_policy_type, requested_at, assigned_at, created_at, extra_data, "
    # vehicle_count_declared (0114) was here until the dialog's vehicles question
    # was removed. The column still exists and is never written; selecting it
    # would send a permanently null field to every screen.
    # The two call counters (0121). On the LIST select because the coloured
    # tally they drive is painted per row — fetching them per lead afterwards
    # would be one request per row for two integers.
    "licensed_in_canada, is_qualified, answered_count, no_answer_count, "
    # THE APPLICANT'S OWN DETAILS, embedded so the list can tell a lead that is
    # READY to be judged from one that is merely old (frontend domain/unjudged).
    #
    # The red border asks the broker for the qualified answer, so it must only
    # appear where they could actually give one — which means the details they
    # collect on the call are already in. Without this the flag fires on any
    # unjudged lead over a day old: measured at 145 of 200 on production, versus
    # 4 once completeness is required.
    #
    # AN EMBED, NOT NINE MORE COLUMNS ON lead. These live on driver because they
    # describe a PERSON, and a lead can carry several. Same shape the renewal
    # sweep already uses for driver.renewal_date (services/reminder.py).
    #
    # The frontend picks the principal itself: PostgREST cannot filter an embed
    # down to one row without dropping leads that have no driver at all, and a
    # lead with no applicant is exactly one the flag must not fire on.
    "driver(is_principal, first_name, last_name, phone, email, date_of_birth, "
    "gender, street_address, city, postal_code)"
)

# Columns for a BROKER's OWN leads (GET /leads/mine). Deliberately drops the
# broker/broker_group embeds and assigned_broker_id/assigned_by: on a broker's
# own screen those are CONSTANT (always themselves, always their group), so
# fetching them per row is pure waste — and measured, the embeds are ~60% of
# the query time (208ms -> 84ms without them). Keeps only what the broker
# Leads screen actually renders.
_MY_LEAD_LIST_SELECT = (
    "id, source, name, phone, email, stage, "
    "combined_policy_type, requested_at, assigned_at, created_at, licensed_in_canada"
)


def group_ids_for_community(community_name: str):
    """Resolve a community NAME to the ids of its groups. Used to filter leads
    by community: PostgREST can't cleanly restrict parent rows through a
    two-level embed, so we resolve group ids first and filter group_id in (...).
    One small extra query, only when the community filter is active."""
    return (
        supabase.table("broker_group")
        .select("id, community(name)")
        .eq("community.name", community_name)
        .execute()
    )


def list_leads(
    *,
    group_ids: list[str] | None = None,
    broker_id: str | None = None,
    stage: str | None = None,
    sub_stage: str | None = None,
    sub_stage_is_null: bool = False,
    search: str | None = None,
    unassigned: bool = False,
    order_by: str = "created_at",
    select_columns: str = _LEAD_LIST_SELECT,
    offset: int = 0,
    limit: int = 50,
):
    """One page of leads matching the given filters, newest first, with an
    EXACT total of all matches (for the pager). Filters are applied server-side
    on indexed columns (group_id, assigned_broker_id, stage) so the browser
    only ever receives one page regardless of total lead volume.

    order_by: which timestamp to sort by, DESC. The ADMIN all-leads screen
    wants 'created_at' (when the customer submitted). A BROKER's own-leads
    screen wants 'assigned_at' (when it landed in THEIR inbox) — a lead
    reassigned to them today should show as newest to them, even if the
    customer submitted it weeks ago.

    select_columns: the admin list needs the broker/broker_group name embeds;
    a broker's own list uses the leaner _MY_LEAD_SELECT (see both constants).

    group_ids: pre-resolved from a community filter (see group_ids_for_community)
    and/or a single group filter — an empty list means "a filter was applied but
    matched no groups", so the query is short-circuited to zero rows by the
    caller. unassigned=True restricts to leads still in the pool."""
    q = (
        supabase.table("lead")
        .select(select_columns, count="exact")
        .order(order_by, desc=True)
    )
    if group_ids is not None:
        q = q.in_("group_id", group_ids)
    if broker_id is not None:
        q = q.eq("assigned_broker_id", broker_id)
    if stage is not None:
        q = q.eq("stage", stage)
    # THE LABEL INSIDE THE STAGE (0092), used to split New into untouched vs
    # In Progress and Lost into lost vs Junk. Two separate arguments because
    # "has this label" and "has no label" are different questions and a single
    # nullable string cannot ask both — passing None already means "do not
    # filter". The rail's counts split the same way (0093), so a chip's number
    # and the rows it opens always agree.
    if sub_stage is not None:
        q = q.eq("sub_stage", sub_stage)
    elif sub_stage_is_null:
        q = q.is_("sub_stage", "null")
    if unassigned:
        q = q.is_("assigned_broker_id", "null")
    if search:
        # name OR phone OR email, case-insensitive. Kept in sync with the two
        # counts RPCs (broker_stage_counts/0033, lead_list_counts/0028) — all
        # three must search the SAME columns or rows and counts would disagree.
        #
        # The pattern is double-quoted (PostgREST value quoting): commas and
        # parens are or()-syntax characters, so an unquoted search like
        # "Doe, John" is a parse error (a 500 to the user). Quotes/backslashes
        # are stripped from the term since they'd escape the quoting itself.
        safe = search.replace("\\", "").replace('"', "")
        pat = f'"%{safe}%"'
        q = q.or_(f"name.ilike.{pat},phone.ilike.{pat},email.ilike.{pat}")
    return q.range(offset, offset + limit - 1).execute()


def lead_list_counts(
    *,
    community: str | None = None,
    group_id: str | None = None,
    broker_id: str | None = None,
    stage: str | None = None,
    search: str | None = None,
):
    """The 4 KPI counts (all / unassigned / assigned / today) in one pass via
    the lead_list_counts RPC (migrations/0028). Honors the shared filters but
    NOT the assigned/unassigned facet, so the chips don't make totals jump."""
    return supabase.rpc(
        "lead_list_counts",
        {
            "p_community": community,
            "p_group_id": group_id,
            "p_broker_id": broker_id,
            "p_stage": stage,
            "p_search": search,
        },
    ).execute()


def insert(values: dict):
    return supabase.table("lead").insert(values).execute()


def get_by_id(lead_id: str):
    return supabase.table("lead").select("*").eq("id", lead_id).execute()


def adjust_call_count(lead_id: str, column: str, delta: int):
    """Move one call counter by one, atomically (0121).

    AN RPC, NOT A READ-THEN-WRITE. Two presses landing together would both
    read the same number and both write the same successor, losing a count.
    adjust_lead_call_count does the arithmetic inside a single UPDATE, so
    Postgres serialises them, and clamps at zero with greatest(0, ...) so a
    minus on an empty counter is a no-op rather than a constraint violation.

    `column` is one of exactly two names, mapped from a validated literal by
    services/lead.py — never client text. The function re-checks it anyway and
    raises on anything else.

    Returns both counters; an empty .data means no lead with that id.
    """
    return supabase.rpc(
        "adjust_lead_call_count",
        {"p_lead_id": lead_id, "p_column": column, "p_delta": delta},
    ).execute()


def set_combined_policy_type(lead_id: str, policy_type: str | None):
    """Write lead.combined_policy_type — whether this quote bundles a property.

    Narrow rather than a general update(), matching update_stage above: the
    columns a broker may change are few and each has its own rules, so a
    generic setter would be a way to write any column from anywhere.
    """
    return (
        supabase.table("lead")
        .update({"combined_policy_type": policy_type})
        .eq("id", lead_id)
        .execute()
    )


def insert_manual_custody(lead_id: str, broker_id: str):
    """The custody and arrival rows for a lead that skipped the round-robin.

    Every other lead gets these from assign_lead (0030), which a manual lead
    never calls — there is nothing to allocate, the broker who typed it is the
    broker on the call. Written here instead so a manual lead has the same
    history as any other rather than appearing from nowhere.

    'Manual Entry' rather than 'Initial Claim': the latter means the engine
    chose this broker, and recording a hand-typed lead that way would corrupt
    any later reporting on how leads actually arrive.
    """
    supabase.table("lead_assignment").insert({
        "lead_id": lead_id,
        "broker_id": broker_id,
        "reason": "Manual Entry",
    }).execute()
    return (
        supabase.table("lead_stage_history")
        .insert({
            "lead_id": lead_id,
            "broker_id": broker_id,
            # null from_stage marks an ARRIVAL rather than a move — the same
            # shape assign_lead writes for a lead landing on its first broker.
            "from_stage": None,
            "to_stage": "New",
        })
        .execute()
    )


def call_assign_lead(lead_id: str):
    """Invokes assign_lead(p_lead_id) (migrations/0019_assignment_function.sql)
    via Postgres RPC. This is the ONLY way a lead ever gets assigned — see
    migrations/0019 and docs/round-robin-allocation-design.md §9: brokers no
    longer claim leads, the system assigns every one via round-robin. Returns
    the assigned broker_id, or null if no eligible broker exists right now
    (the lead is left unassigned, not an error)."""
    return supabase.rpc("assign_lead", {"p_lead_id": lead_id}).execute()


def get_by_platform_leadgen_id(leadgen_id: str):
    """The lead created from one Meta submission, if there is one.

    Used by ingestion to tell a genuine failure from a retry that has already
    succeeded: create_lead and mark_processed are separate transactions, so a
    lead can exist while its webhook_event still says failed. Asking the
    database is more reliable than reading a driver-specific duplicate-key
    error message.

    Reads one column — this is an existence check, not a fetch."""
    return (
        supabase.table("lead")
        .select("id")
        .eq("platform_leadgen_id", leadgen_id)
        .limit(1)
        .execute()
    )


def drain_pending_for_group(group_id: str):
    """Invokes assign_pending_leads_for_group(p_group_id) (migrations/0019) via
    RPC — offers every currently-unassigned lead in the group back around the
    rotation, oldest request first.

    The daily reset (0020) was its only caller until now. It is also what makes
    a mid-day capacity change meaningful: a broker opting into overtime at 3pm
    needs the leads ALREADY waiting to reach them, not just the next one to
    arrive.

    Returns nothing useful — the SQL function is `returns void`, and each lead
    it places is recorded by assign_lead itself."""
    return supabase.rpc(
        "assign_pending_leads_for_group", {"p_group_id": group_id}
    ).execute()


def group_capacity(group_id: str):
    """Invokes group_capacity(p_group_id) (migrations/0021) via RPC — the
    read-model behind the reassign dialog's load bars and the group capacity
    view. Returns a single JSON blob: leads_today, unassigned, and a
    per-broker delivered-today array, all anchored to the same reset clock
    the assignment engine uses."""
    return supabase.rpc("group_capacity", {"p_group_id": group_id}).execute()


def broker_lead_history(broker_id: str):
    """Invokes broker_lead_history(p_broker_id) (migrations/0026) via RPC —
    EVERY lead this broker was ever assigned, read from the lead_assignment
    custody log (system AND admin assignments). A lead later reassigned away
    still shows here, since the broker really worked it. Returns a list of rows,
    newest first (lead_id, name, phone, email, source, stage, received_at,
    delivered_by=the assignment reason)."""
    return supabase.rpc("broker_lead_history", {"p_broker_id": broker_id}).execute()


def admin_assign(lead_id: str, broker_id: str, admin_id: str, batch_id: str | None = None):
    """Admin assignment of a lead to a broker — handles BOTH handing out a
    pooled lead (Admin Claim) and moving an already-held one (Admin Reassign);
    the DB function decides which from the lead's current state. One RPC, so the
    lead move + custody row + audit log all commit atomically (migrations/0025).
    Deliberately does NOT touch lead_delivery, so it stays invisible to daily
    caps (design §3). Raises P0001 with a human message on an invalid request
    (lead/broker missing, archived, or same-broker no-op) — mapped to a 400 by
    the service layer."""
    return supabase.rpc(
        "admin_assign_lead",
        {
            "p_lead_id": lead_id,
            "p_broker_id": broker_id,
            "p_admin_id": admin_id,
            "p_batch_id": batch_id,
        },
    ).execute()


def update_stage(
    lead_id: str, broker_id: str, new_stage: str, issued_at: str | None = None
):
    """The broker moving their OWN lead's stage — one RPC, atomic (updates
    lead.stage, appends a lead_stage_history row, and writes or clears
    premium_history.issued_at; migrations/0032, rebuilt by 0085). The DB
    function itself enforces the ownership guard (only the CURRENT holder may
    change it) and raises a plain message on any invalid request — mapped to
    a 400/403 by the service layer.

    issued_at travels WITH the stage rather than being written after it. The
    two used to be separate calls, and the second was best-effort: a failure
    between them left a lead reading 'Issued' whose premium counted toward no
    month at all. See 0085.

    ALWAYS SENT, including as null. PostgREST matches an RPC on the exact set
    of keys it is given, and omitting this one would look for a
    three-argument update_lead_stage that 0085 dropped.
    """
    return supabase.rpc(
        "update_lead_stage",
        {
            "p_lead_id": lead_id,
            "p_broker_id": broker_id,
            "p_new_stage": new_stage,
            "p_issued_at": issued_at,
        },
    ).execute()


def broker_stage_counts(broker_id: str, search: str | None = None):
    """Per-stage counts for one broker's Leads-screen rail (migrations/0033,
    rebuilt by 0051) — a single jsonb object keyed by stage name, every one of
    the 7 stages always present, plus 'all'. Honors search so the rail matches
    the searched slice; no stage param (the rail always shows the full
    breakdown)."""
    return supabase.rpc(
        "broker_stage_counts", {"p_broker_id": broker_id, "p_search": search}
    ).execute()


def count_assigned_since(broker_id: str, since: str):
    """How many leads landed with this broker at or after `since` (an ISO
    timestamp) — the "new today" figure on My Day.

    Counted from lead_assignment, NOT from lead.assigned_at, because
    lead_assignment is the append-only custody log: it records every arrival
    including a lead that came in this morning and was reassigned away this
    afternoon. That lead WAS new work for this broker today, and reading the
    lead table (which now points at someone else) would silently drop it.

    count="exact" with head=True asks PostgREST for the count only — no rows
    cross the wire, which matters because this is polled.
    """
    return (
        supabase.table("lead_assignment")
        .select("id", count="exact")
        .eq("broker_id", broker_id)
        .gte("assigned_at", since)
        .execute()
    )


def stage_activity_for_broker(broker_id: str):
    """Every stage-change timestamp on this broker's CURRENTLY-HELD leads,
    newest first — the raw material for the "stale" figure.

    Returns rows, not a count: "stale" means a lead whose LATEST activity is
    older than the cutoff, and PostgREST cannot express a per-group max with a
    having-clause. The service reduces these to one newest timestamp per lead.

    Filtered through an inner join on lead.assigned_broker_id rather than
    lead_stage_history.broker_id: history rows are stamped with whoever held
    the lead at the time, so a lead reassigned TO this broker carries the
    previous holder's rows. Asking "which of MY leads have gone quiet" has to
    follow current custody, and the !inner makes the embed a filter rather
    than a nullable attachment.
    """
    return (
        supabase.table("lead_stage_history")
        .select("lead_id, changed_at, lead!inner(assigned_broker_id)")
        .eq("lead.assigned_broker_id", broker_id)
        .order("changed_at", desc=True)
        .execute()
    )


def set_qualification(lead_id: str, values: dict):
    """Write the qualification answers AND queue the conversion rows, in one
    transaction (0124).

    AN RPC, NOT A PLAIN UPDATE — and the reason is the whole of 0124. This used
    to be a column update, with the comment "there is nothing here to keep atomic
    with anything else". That stopped being true when the broker's answer became
    what fires CompleteRegistration (0123): the answer and its conversion rows
    are now two halves of one fact, and PostgREST gives no way to put two REST
    calls in one transaction. A failure between them would leave a lead reading
    Qualified that Meta is never told about, unrecoverably — there is no repair
    sweep, and Meta's 7-day limit closes the window within a week.

    STILL TAKES A DICT, so the service's call site does not change shape, but the
    only key it can carry is `is_qualified` — SetQualificationRequest has no
    others. The dialog's convictions and licence answers are facts about the
    PERSON and patch the driver instead (PATCH /drivers/{id}); the driver trigger
    reconciles those.

    Ownership and source guards stay in the service, which is the only caller —
    this repo's four-layer rule puts every HTTPException decision there.
    """
    return supabase.rpc(
        "set_lead_qualification",
        {
            "p_lead_id": lead_id,
            # The schema refuses an explicit null (422) and exclude_unset drops
            # an omitted field, so this key is always present and never None.
            "p_is_qualified": values["is_qualified"],
        },
    ).execute()


def set_sub_stage(lead_id: str, sub_stage: str | None):
    """The label inside the stage — 'In Progress', 'Junk', or null for neither
    (0092).

    A plain update, same shape and same reasoning as set_qualification above:
    no history row, because the STAGE's move is already recorded in
    lead_stage_history and a label on top of it is not a second event.

    NULL IS A REAL VALUE HERE, not a missing argument. The service sends it on
    every stage move to clear a label the new stage does not own — a lead
    marked Junk and reopened must not still read Junk. The ownership guard
    lives in update_lead_stage, which the service calls first."""
    return (
        supabase.table("lead")
        .update({"sub_stage": sub_stage})
        .eq("id", lead_id)
        .execute()
    )


def get_one_for_broker(lead_id: str, broker_id: str):
    """One of a broker's OWN leads, in the same shape /leads/mine returns rows.

    WHY THIS EXISTS. /leads/mine is paginated, so a deep link to a lead on page
    four had nothing to fetch it with — the renewal WhatsApp's "Set Up
    Reminder" button would open an unfiltered list. The frontend needs to load
    exactly the lead the link names.

    SCOPED IN THE QUERY, not checked afterwards. Filtering on
    assigned_broker_id means another broker's lead comes back as no rows and
    the service 404s — the same answer a lead that does not exist gets, so the
    endpoint cannot be used to discover whose book a given id sits in.
    """
    return (
        supabase.table("lead")
        .select(MY_LEAD_SELECT)
        .eq("id", lead_id)
        .eq("assigned_broker_id", broker_id)
        .limit(1)
        .execute()
    )


def set_renewal_reminders_off(lead_id: str, off: bool):
    """Stop (or resume) this lead's renewal ladder — 0119.

    Written only by services/renewal_inbound.py, when a broker taps Stop
    Reminder on a renewal WhatsApp. A NARROW SETTER rather than a general
    update(), matching set_sub_stage and set_qualification above: a repository
    function that takes an arbitrary dict is a function that can write any
    column, and this one has no business touching a stage or a premium.

    No ownership guard here — the caller has already proved the tap came from
    the broker who holds the lead, by matching the sender's number against the
    reminder's broker. That check belongs with the message, not with the write.
    """
    return (
        supabase.table("lead")
        .update({"renewal_reminders_off": off})
        .eq("id", lead_id)
        .execute()
    )


def list_meta_with_signals(since_iso: str, limit: int = 1000):
    """Meta leads that arrived since a moment, newest first, each with its
    broker and every conversion signal queued for it — the admin Meta
    Conversions screen's one read.

    FROM THE LEAD, NOT FROM meta_signal. The screen is one row per lead; read
    the other way round it would be up to ten rows per lead to fold back
    together, and a lead whose signals were somehow never queued would not
    appear at all — which is exactly the lead an admin most needs to see.

    source = 'meta' because nothing else is ever reported to Meta
    (ensure_conversion_signals, 0123). payload is NOT selected: it holds hashed
    user data the screen has no use for.
    """
    return (
        supabase.table("lead")
        .select(
            # is_qualified lets the Meta Quality screen tell "Not qualified"
            # (false) from "Not asked yet" (null); without it the two look
            # identical and the broker's own answer vanishes from the view.
            "id, name, stage, created_at, is_qualified, "
            # id on broker, so the admin screen's Broker filter has a stable
            # key — names are not unique and renaming one later would break
            # a bookmarked ?broker=... link.
            "broker(id, name), "
            # The same nested embed the broker's own list uses — see
            # _LEAD_SCREEN_SELECT at the top of this file. Powers the
            # Community → Group → Broker filters and the row labels.
            "broker_group(id, name, community(id, name)), "
            # sent_at drives the 7-day learning count the service does
            # separately; payload carries the Purchase premium that the
            # service reads for ONE key and never returns (user_data stays
            # inside the backend — the comment just above says so).
            "meta_signal(id, event_name, status, dataset_target, error_code, "
            "error_message, sent_at, payload)"
        )
        .eq("source", "meta")
        .gte("created_at", since_iso)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
