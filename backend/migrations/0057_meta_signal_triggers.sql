-- The two watchers that turn real pipeline events into rows in meta_signal
-- (0056) — the notebook the sender (built later) reads to actually tell Meta
-- about them. Every trigger here does ONE thing: insert a row. None of them
-- call Meta directly, and none of them can fail the write they're attached
-- to — an httpx timeout must never be able to break a lead's assignment or
-- a broker's stage change.
--
-- WHY TRIGGERS, NOT APPLICATION CODE: assign_lead (0019) is a SQL function,
-- and leads are also handed out by the SQL-only morning reset drain (0020)
-- — a Python-side hook would silently miss every drain-assigned lead. Same
-- reasoning applies to stage changes: update_lead_stage (0032) is the one
-- write path, already atomic in SQL, so the signal has to be written in the
-- same place or a caller bypassing the Python service layer (there isn't
-- one today, but the DB shouldn't rely on that) would miss it too.


-- ── 1. Lead — fires when the round-robin engine delivers a lead ─────────
--
-- lead_delivery is written ONLY by assign_lead — never by an admin hand-out
-- or reassignment (see migrations/0018's own note on why the daily cap
-- ignores those). An insert here means a real broker now holds a real lead,
-- which is exactly what Meta's Lead event means.
create or replace function fn_meta_signal_on_lead_delivery()
returns trigger as $$
declare
  v_platform text;
begin
  select source into v_platform from lead where id = new.lead_id;

  -- Only Meta is built today (see meta_signal.platform's own check
  -- constraint) — a TikTok-sourced lead has nowhere to send a signal yet, so
  -- skip it rather than insert a row nothing will ever process correctly.
  if v_platform = 'meta' then
    insert into meta_signal (lead_id, platform, event_name, triggered_by)
    values (new.lead_id, 'Meta', 'Lead', null);
  end if;

  return new;
end;
$$ language plpgsql;

create trigger trg_meta_signal_on_lead_delivery
  after insert on lead_delivery
  for each row
  execute function fn_meta_signal_on_lead_delivery();


-- ── 2 & 3. CompleteRegistration + Purchase — fire on specific stage moves ─
--
-- Both watch lead_stage_history, not lead.stage directly: NEW.to_stage on an
-- INSERT is unambiguous, where a trigger on lead's own stage column would
-- also fire for unrelated updates to that row.
--
-- 'Quote Done' -> CompleteRegistration: the qualified-lead signal. Fires far
-- more often than a sale, so Meta's model learns from it faster, and a
-- broker having produced a real quote is itself proof this lead was real.
--
-- 'Sold' -> Purchase: the broker's own sale, which is the real transaction
-- for us — not the insurer's own paperwork (stage 'Issued'), which can lag
-- by weeks. Reads premium_history.total_new_premium for this lead AT THIS
-- EXACT MOMENT: the New Policy UI requires a premium to be entered before
-- 'Sold' can be reached at all, so by the time this row exists the value is
-- already there. Whatever that figure is becomes the amount reported to
-- Meta, permanently — a premium correction entered after this moment does
-- NOT re-fire or update the signal. 'Sold' is the finish line; what was true
-- then is what gets sent.
create or replace function fn_meta_signal_on_stage_history()
returns trigger as $$
declare
  v_platform text;
  v_total_new_premium numeric;
begin
  if new.to_stage not in ('Quote Done', 'Sold') then
    return new;
  end if;

  select source into v_platform from lead where id = new.lead_id;
  if v_platform <> 'meta' then
    return new;
  end if;

  if new.to_stage = 'Quote Done' then
    insert into meta_signal (lead_id, platform, event_name, triggered_by)
    values (new.lead_id, 'Meta', 'CompleteRegistration', new.broker_id);

  elsif new.to_stage = 'Sold' then
    -- Most recent policy_year row for this lead — the one the New Policy
    -- screen was just editing when the broker moved the stage.
    select total_new_premium into v_total_new_premium
    from premium_history
    where lead_id = new.lead_id
    order by policy_year desc
    limit 1;

    insert into meta_signal (lead_id, platform, event_name, triggered_by, payload)
    values (
      new.lead_id, 'Meta', 'Purchase', new.broker_id,
      -- Not the final POST payload (that's built and hashed by the sender) —
      -- a small note-to-self so a 'Sold' lead with no premium row is visible
      -- immediately in this table, rather than only discovered when the
      -- sender tries to build a Purchase event with nothing to put in value.
      jsonb_build_object('total_new_premium_at_sale', v_total_new_premium)
    );
  end if;

  return new;
end;
$$ language plpgsql;

create trigger trg_meta_signal_on_stage_history
  after insert on lead_stage_history
  for each row
  execute function fn_meta_signal_on_stage_history();
