-- An UNQUALIFIED lead tells Meta nothing.
--
-- CompleteRegistration IS the qualified-lead signal — that is what the event
-- means to us, whatever Meta named it. Until this migration it fired for EVERY
-- Meta lead reaching Quote Done, and Purchase for every one reaching Sold,
-- with nobody ever asked whether the lead was real. So Meta's optimisation was
-- being trained on every form-filler who got a quote, junk included — the
-- opposite of what this table exists to achieve.
--
-- The broker's answer lives in lead.is_qualified (0077):
--
--   true   said yes        -> both signals fire, as before
--   false  said no         -> NOTHING is sent for this lead, ever
--   null   not asked yet   -> nothing sent
--
-- `IS TRUE`, NOT `= true`. Against a null, `= true` evaluates to NULL, which
-- is not true, so the branch would be skipped either way — but only by
-- accident of three-valued logic. `is true` states the rule the code actually
-- means: fire only on an explicit yes. The two nulls this column carries
-- (never asked, and never applicable) both correctly send nothing.
--
-- THE STAGE MOVE IS BLOCKED WHILE NULL, in services/lead.py::update_stage,
-- beside the premium guard it is modelled on. So in practice a Meta lead
-- reaches Quote Done only after the question has been answered — this trigger
-- is the second line, not the only one. It is still written to handle null,
-- because SQL-side stage changes (the morning drain, a manual RPC, a future
-- admin path) do not pass through that service.
--
-- WHAT THIS DOES NOT DO: it does not delete or cancel a CompleteRegistration
-- already queued or Sent. Once a signal has gone to Meta it is a fact about
-- the past. The gate stops new ones.
--
-- ─────────────────────────────────────────────────────────────────────────
-- BUILT FROM 0075, WHICH IS THE CURRENT DEFINITION.
--
-- The chain is 0057 -> 0067 -> 0075. This matters, and 0075's own header
-- records why: its first draft copied 0057's body verbatim and silently
-- reverted 0067's rename of total_new_premium -> total_new_premium_annual,
-- breaking Sold with 'column "total_new_premium" does not exist'. A worse bug
-- than the one it was fixing.
--
-- Diffed against 0075 before applying. The ONLY differences are:
--   * v_is_qualified declared and read in the same select as source
--   * `if v_is_qualified is true` guarding each of the two inserts
-- Everything else — the source gate, the total_new_premium_annual select, the
-- jsonb_build_object payload, both `on conflict do nothing` clauses — is
-- byte-identical to 0075.
--
-- fn_meta_signal_on_lead_delivery is NOT touched. The 'Lead' event fires when
-- a lead is DELIVERED to a broker, before anyone has looked at it — there is
-- no judgement to gate it on, and gating it would mean never telling Meta a
-- lead arrived.

create or replace function fn_meta_signal_on_stage_history()
returns trigger as $$
declare
  v_platform text;
  v_is_qualified boolean;
  v_total_new_premium numeric;
begin
  if new.to_stage not in ('Quote Done', 'Sold') then
    return new;
  end if;

  -- One read for both facts, rather than two round-trips to the same row.
  select source, is_qualified
  into v_platform, v_is_qualified
  from lead where id = new.lead_id;

  if v_platform <> 'meta' then
    return new;
  end if;

  -- THE GATE. An unqualified lead (false) and an unanswered one (null) are
  -- different facts about the broker, but the same instruction to us: send
  -- nothing. Checked once here rather than twice below, so the two events can
  -- never drift apart on the question of what "qualified" means.
  if v_is_qualified is not true then
    return new;
  end if;

  if new.to_stage = 'Quote Done' then
    insert into meta_signal (lead_id, platform, event_name, triggered_by)
    values (new.lead_id, 'Meta', 'CompleteRegistration', new.broker_id)
    on conflict do nothing;

  elsif new.to_stage = 'Sold' then
    -- ANNUAL, and no ordering — both per 0067, which renamed the column and
    -- collapsed premium_history to one row per lead. Reporting a twelfth of
    -- the policy's worth would train Meta's model on a figure wrong by an
    -- order of magnitude, and there is nothing left to order by.
    select total_new_premium_annual into v_total_new_premium
    from premium_history
    where lead_id = new.lead_id
    limit 1;

    insert into meta_signal (lead_id, platform, event_name, triggered_by, payload)
    values (
      new.lead_id, 'Meta', 'Purchase', new.broker_id,
      -- Not the final POST payload (that's built and hashed by the sender) —
      -- a small note-to-self so a 'Sold' lead with no premium row is visible
      -- immediately in this table, rather than only discovered when the
      -- sender tries to build a Purchase event with nothing to put in value.
      jsonb_build_object('total_new_premium_at_sale', v_total_new_premium)
    )
    on conflict do nothing;
  end if;

  return new;
end;
$$ language plpgsql;

-- The trigger itself is unchanged and still bound to this function;
-- create or replace rebinds it automatically.
