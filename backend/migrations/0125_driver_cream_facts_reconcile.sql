-- The principal driver's three cream facts reconcile the moment they change.
--
-- WHY THIS EXISTS. 0123 made ensure_conversion_signals the producer and 0124
-- wired the qualification answer to it. But cream needs four conditions, and
-- three of them live on the DRIVER:
--
--     licensed_5y_plus     the broker answers, in the dialog
--     convictions_3y       the broker answers, in the dialog
--     at_fault_claims_6y   the DASH report supplies it
--
-- Those arrive AFTER the judgement, usually days after, because the report is
-- pulled late. Without this trigger a lead qualifies, gets its two common rows,
-- and then its cream facts complete with nothing watching — so cream never
-- fills. Measured before writing: those columns are set on 6, 6 and 23 of 218
-- principal drivers, because the only thing that ever reconciled them was a
-- stage move.
--
-- ── WHY THE TRIGGER IS NARROW, AND WHY THAT IS THE POINT ────────────────
--
-- `after update of <three columns>` plus a principal check plus a
-- value-actually-changed check. All three narrowings matter, because 0124's
-- design makes reconciliation FAILURE-PROPAGATING: whatever this trigger fires
-- on inherits the power to fail that write.
--
-- So the blast radius of a Meta outage is exactly the set of writes this fires
-- on, and that set must be as small as the feature allows:
--
--   * THREE COLUMNS, not the table. A broker correcting a phone number or an
--     address must never be refused because Meta is unreachable.
--   * PRINCIPAL ONLY. build_user_data reads that row alone, so a spouse's
--     record is not what Meta would be told about — and editing a spouse must
--     not be able to fail for a reason that has nothing to do with them.
--     Measured: 42 non-principal rows exist and none carries a cream fact
--     today, but services/dash_apply.py::_apply_operators writes operator rows,
--     so the path is live.
--   * ONLY ON A REAL CHANGE. `is distinct from` rather than `<>` because these
--     columns are nullable and null <> 0 is null, not true. A DASH re-apply
--     that rewrites the same values must not queue work.
--
-- NO BUSINESS LOGIC HERE. The trigger says only "this input changed, re-run the
-- one function". Every rule — the source gate, the cream test, the tenant
-- exclusion, unknown-is-never-cream — stays in ensure_conversion_signals, so
-- there is exactly one implementation and nothing to drift.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.

begin;


create or replace function fn_reconcile_on_driver_cream_facts()
returns trigger as $$
begin
  -- THE PRINCIPAL CHECK COMES FIRST, and reads NEW rather than OLD: a driver
  -- promoted to principal with facts already on the row should reconcile, and a
  -- demoted one should not.
  if not new.is_principal then
    return new;
  end if;

  -- `is distinct from` handles the nulls these columns are full of. Without it
  -- a null-to-null rewrite reads as "no change" only by accident, and a
  -- null-to-0 change — the exact moment a DASH report says "no claims" and a
  -- lead becomes cream — would read as null and be missed.
  if new.convictions_3y     is distinct from old.convictions_3y
     or new.licensed_5y_plus   is distinct from old.licensed_5y_plus
     or new.at_fault_claims_6y is distinct from old.at_fault_claims_6y
  then
    -- Same transaction as the driver write. A failure rolls the fact back with
    -- it, which is the correct outcome: a driver row whose cream facts are
    -- stored but whose conversion was never queued is a silent permanent loss,
    -- while a failed save is a retry the broker can see.
    perform ensure_conversion_signals(new.lead_id);
  end if;

  return new;
end;
$$ language plpgsql;

comment on function fn_reconcile_on_driver_cream_facts() is
  'Re-runs ensure_conversion_signals when a PRINCIPAL driver''s cream facts actually change. Holds no rules of its own — it only says the input changed.';


drop trigger if exists trg_reconcile_on_driver_cream_facts on driver;

-- `of <columns>` is enforced by Postgres before the function is even entered,
-- so an update touching only phone or address costs nothing at all — not even a
-- function call. The checks inside are the second and third narrowings.
create trigger trg_reconcile_on_driver_cream_facts
  after update of convictions_3y, licensed_5y_plus, at_fault_claims_6y
  on driver
  for each row
  execute function fn_reconcile_on_driver_cream_facts();


commit;


-- ── After applying, these should hold ───────────────────────────────────
--
-- Take a QUALIFIED Meta lead whose policy type is Auto and Homeowner or Auto
-- and Condo, and whose principal driver has no cream facts yet. It should have
-- the two common CompleteRegistration rows and no cream ones.
--
-- Now complete the three facts:
--
--   update driver set licensed_5y_plus = true, convictions_3y = 0,
--                     at_fault_claims_6y = 0
--   where lead_id = '<that lead>' and is_principal;
--
--   select event_name, dataset_target from meta_signal
--   where lead_id = '<that lead>' order by dataset_target;
--     -> CompleteRegistration now in common, common_full, cream, cream_full
--
-- An unrelated edit on the SAME driver queues nothing new:
--
--   update driver set city = 'Ottawa'
--   where lead_id = '<that lead>' and is_principal;
--     -> the row count is unchanged (the trigger never fires)
--
-- A SPOUSE with perfect facts queues nothing:
--
--   update driver set licensed_5y_plus = true, convictions_3y = 0,
--                     at_fault_claims_6y = 0
--   where lead_id = '<that lead>' and not is_principal;
--     -> unchanged
--
-- And rewriting the same values is a no-op — run the first update again and the
-- count still does not move, because `is distinct from` sees no change.
