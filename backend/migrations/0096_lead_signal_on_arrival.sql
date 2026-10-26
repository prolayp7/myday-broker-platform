-- `Lead` fires when the lead ARRIVES again, reversing 0095.
--
-- WHY THIS IS BEING PUT BACK. Meta advised that sending only positive signals
-- leaves their model unable to learn what a bad lead looks like. Holding Lead
-- until the applicant was complete meant Meta heard about the good leads and
-- never about the rest — no baseline to contrast them against, and no way to
-- tell which ads bring reachable people from which bring form-fillers.
--
-- 0095 was measurably wrong within two days of shipping. Measured the day this
-- was written:
--
--   Meta leads created since 0095 deployed : 11
--     of those reported to Meta            :  0
--     of those with a driver row at all    :  0
--
-- Not one had been opened by a broker, so under 0095's trigger none of them
-- would ever have fired. The wider shape says the same: of 55 Meta leads, 38
-- sit in New and 13 are Lost, while only 4 reached Quote Done. Those 13 Lost
-- are the negative signal Meta never saw. Firing on arrival gives 55 baseline
-- events against 4 conversions — a contrast the model can learn from.
--
-- 0079 had already recorded Meta's own definition, and 0095 overrode it:
-- "the leads generated on Meta and downloaded into the CRM — not the internal
-- moment a colleague picks one up. Their implementation guide asks for it on
-- all downloaded leads."
--
-- WHAT IS NOT REVERSED. Only the firing point moves. The first/middle/last
-- name columns, the Claude split, the required-field switch to first+last, and
-- the _real_or_none whitespace fix all stay — they improve match quality on
-- every event that fires, whenever it fires. CompleteRegistration (Quote Done)
-- and Purchase (Sold) are untouched.
--
-- NO BACKFILL of the 11 unreported leads. A clean-forward choice, not a
-- limitation: at ~1.6 days they were well inside Meta's 7-day event_time
-- window. The accepted cost is that one of them could later reach Quote Done
-- and send a CompleteRegistration with no Lead ahead of it.

drop trigger if exists trg_meta_signal_on_applicant_complete on driver;
drop function if exists fn_meta_signal_on_applicant_complete();

-- Nothing calls this once the trigger above is gone — verified, its only
-- remaining reference was a parity test. Keeping production SQL alive because
-- a test exercises it inverts the relationship: tests protect production code,
-- they are not a reason to keep unused code. Python keeps its own
-- _PLACEHOLDER_CONTACT regex, which is what services actually use.
drop function if exists is_placeholder_contact(text);

-- 0079's function, unchanged.
--
-- NO `not exists` GUARD, deliberately. 0095's version carried one because that
-- trigger fired on EVERY applicant update and so could rerun after a signal
-- had failed — and a Failed row falls outside
-- idx_meta_signal_lead_event_active, so a replacement would take a new id and
-- therefore a new event_id, which Meta would count twice. None of that applies
-- here: this fires only on `after insert on lead`, and lead.id is a primary
-- key (0008_lead.sql), so the same lead cannot be inserted twice and no later
-- activity re-enters this function at all. The guard would be dead code.
--
-- ON CONFLICT DO NOTHING stays, for the reason 0075 introduced it: this must
-- never be able to fail the insert that triggers it. A lead re-created with a
-- recycled id would otherwise take its own creation down with it.
--
-- NOT gated on is_qualified, unlike the two stage events (0078). At creation
-- nobody has looked at the lead, so there is no judgement to gate on — gating
-- here would mean never telling Meta a lead arrived, which is the whole point.
--
-- triggered_by stays NULL: at creation nobody has touched the lead.
create or replace function fn_meta_signal_on_lead_created()
returns trigger as $$
begin
  -- new.source directly: the row being inserted IS the lead, so unlike the
  -- old delivery trigger there is nothing to look up.
  if new.source = 'meta' then
    insert into meta_signal (lead_id, platform, event_name, triggered_by)
    values (new.id, 'Meta', 'Lead', null)
    on conflict do nothing;
  end if;

  return new;
end;
$$ language plpgsql;

comment on function fn_meta_signal_on_lead_created() is
  'Queues the Meta Lead conversion the moment a Meta lead is downloaded into the CRM. Writes the outbox row only — the drain sends it.';

drop trigger if exists trg_meta_signal_on_lead_created on lead;

create trigger trg_meta_signal_on_lead_created
  after insert on lead
  for each row
  execute function fn_meta_signal_on_lead_created();
