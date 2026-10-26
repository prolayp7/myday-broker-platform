-- The 'Lead' event now fires when the lead is CREATED, not when a broker
-- receives it.
--
-- THE GAP. The trigger was bound to `after insert on lead_delivery` (0057),
-- which only assign_lead writes. So:
--
--   ingested and immediately assigned   -> Lead fired
--   ingested, nobody eligible           -> nothing, until the drain assigns it
--   ingested, then ADMIN-assigned       -> NOTHING, EVER. admin_assign_lead
--                                          deliberately writes no delivery row,
--                                          because a reassignment must not
--                                          consume anyone's daily cap.
--   never assigned at all               -> invisible to Meta permanently
--
-- Meta defines this event as the leads generated on Meta and downloaded into
-- the CRM — not the internal moment a colleague picks one up. Their
-- implementation guide asks for it on all downloaded leads. Broker delivery is
-- our own business, and Meta has no use for it.
--
-- WHY THIS IS ALSO SIMPLER. "A Meta lead exists" is one fact with one cause,
-- and the row's own insert is where it happens. Routing it through a second
-- table meant the event depended on WHICH code path assigned the lead, which
-- is exactly how the admin gap opened without anyone noticing.
--
-- triggered_by stays NULL. At creation nobody has touched the lead — the same
-- reason 0057 passed null from the delivery trigger, where the receiving
-- broker was known and still deliberately not recorded as the actor.
--
-- ON CONFLICT DO NOTHING is kept, for the reason 0075 introduced it: this must
-- never be able to fail the insert that triggers it. A lead re-created with a
-- recycled id would otherwise take its own creation down with it.
--
-- NOT gated on is_qualified, unlike the two stage events (0078). At creation
-- nobody has looked at the lead yet, so there is no judgement to gate on —
-- gating here would mean never telling Meta a lead arrived.

create or replace function fn_meta_signal_on_lead_created()
returns trigger as $$
begin
  -- new.source directly: the row being inserted IS the lead, so unlike the
  -- delivery trigger there is nothing to look up.
  if new.source = 'meta' then
    insert into meta_signal (lead_id, platform, event_name, triggered_by)
    values (new.id, 'Meta', 'Lead', null)
    on conflict do nothing;
  end if;

  return new;
end;
$$ language plpgsql;

create trigger trg_meta_signal_on_lead_created
  after insert on lead
  for each row
  execute function fn_meta_signal_on_lead_created();

-- The old path goes, rather than being left to fire alongside the new one:
-- two triggers writing the same (lead_id, 'Lead') pair would collide on
-- idx_meta_signal_lead_event_active, and although `on conflict do nothing`
-- would absorb it, keeping a second source for one fact is how the original
-- gap survived unnoticed.
drop trigger if exists trg_meta_signal_on_lead_delivery on lead_delivery;
drop function if exists fn_meta_signal_on_lead_delivery();
