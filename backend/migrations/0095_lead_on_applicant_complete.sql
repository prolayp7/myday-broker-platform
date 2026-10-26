-- Fire `Lead` when the broker has captured the applicant, not when the lead
-- arrives.
--
-- 0079 fires Lead on `after insert on lead` — the instant a Meta lead lands,
-- before anyone has called. Measured the day this was written: 42 Lead events
-- sent, 37 of them for leads whose details were never captured. 88% of what
-- Meta optimises against is people who never picked up, which teaches it to
-- find more of them.
--
-- Match quality improves as a side effect. An event fired at arrival can only
-- carry email, phone and lead_id, because nothing else exists yet; one fired
-- at completion carries name, date of birth, gender and location too.
--
-- ONLY `Lead` MOVES. CompleteRegistration (Quote Done) and Purchase (Sold)
-- keep the trigger and the payloads 0078 gave them.
--
-- WHY A TRIGGER AND NOT A PYTHON HOOK. The insert runs inside the broker's own
-- transaction, so a complete applicant and its Lead row commit together or not
-- at all — a Python hook that queued after the update could swallow a failure
-- and leave a permanently complete applicant with no Lead and nothing to retry
-- it. One trigger also covers the manual form, DASH, and any future write
-- path, with no per-path hook to forget.
--
-- NO HTTP HERE. This writes the outbox row; services/meta_signal.py's
-- 15-minute drain sends it. repo.list_pending has no event_name filter, so it
-- picks this up with no change. A Meta outage can never fail a broker's save.

-- ── The placeholder rule, in SQL ─────────────────────────────────────────
--
-- Mirrors services/driver.py::_PLACEHOLDER_CONTACT. A manually created lead
-- carries the literal "(not provided)" in whichever contact detail was not
-- collected (services/lead.py::_NOT_PROVIDED), so a plain `is not null` check
-- would read that as a real answer.
--
-- KEPT AS TWO IMPLEMENTATIONS ON PURPOSE. Python does not call this: a
-- Supabase RPC is an HTTP round trip and the check runs nine times per
-- evaluation, so sharing it would turn a regex match into nine network calls
-- to avoid duplicating one line. A parity test over one shared case list is
-- what keeps them honest instead.
--
-- `v ~ '^\s*$'` rather than `btrim(v) = ''` so tabs and newlines count as
-- empty too — btrim() alone strips spaces, while Python's .strip() takes all
-- whitespace, and that gap is exactly the bug this migration's companion fix
-- closes in _real_or_none.
create or replace function is_placeholder_contact(v text)
returns boolean as $$
  select v is null
      or v ~ '^\s*$'
      or btrim(v) ~* '^\(?\s*(not provided|not available|n/?a|unknown|none)\s*\)?$';
$$ language sql immutable;

comment on function is_placeholder_contact(text) is
  'True when a field holds no real value — null, whitespace, or a stand-in like "(not provided)". Hand-synced with services/driver.py::_PLACEHOLDER_CONTACT; a parity test asserts they agree.';


-- ── Lead, when the applicant is complete ─────────────────────────────────
create or replace function fn_meta_signal_on_applicant_complete()
returns trigger as $$
declare
  v_source text;
begin
  -- The nine fields services/lead.py::_REQUIRED_APPLICANT_FIELDS asks for,
  -- and in the same order. Cheapest test first: nine column reads beat a
  -- lookup on `lead`, and most rows fail here.
  --
  -- PROVINCE IS NOT AMONG THEM. This brokerage writes Ontario only, so the
  -- broker is not asked — meta_capi.hash_province defaults an empty value to
  -- "on" and Meta still receives it.
  --
  -- first_name AND last_name, not `name`: Meta hashes fn and ln as separate
  -- parameters, so a lead holding only the combined form can be matched on
  -- neither. A single-word name yields a first name and no surname, which
  -- leaves the lead incomplete until a broker adds one — intended, since a
  -- conversion Meta cannot match on a name is what this change exists to stop.
  if is_placeholder_contact(new.first_name)
     or is_placeholder_contact(new.last_name)
     or is_placeholder_contact(new.phone)
     or is_placeholder_contact(new.email)
     or new.date_of_birth is null
     or is_placeholder_contact(new.gender)
     or is_placeholder_contact(new.street_address)
     or is_placeholder_contact(new.city)
     or is_placeholder_contact(new.postal_code) then
    return new;
  end if;

  select source into v_source from lead where id = new.lead_id;

  if v_source is distinct from 'meta' then
    return new;
  end if;

  -- NOT GATED ON is_qualified, unlike the two stage events (0078). That
  -- question is only asked when the broker moves to Quote Done
  -- (services/lead.py::needs_qualifying), so at completion it is still null
  -- for nearly every lead — gating here would mean Lead almost never fires.
  -- Same reasoning 0079 gives for the arrival trigger.

  -- ONE Lead ROW EVER PER LEAD, INCLUDING AFTER A FAILURE.
  --
  -- idx_meta_signal_lead_event_active only blocks rows whose status is
  -- <> 'Failed', so `on conflict do nothing` alone would let a later applicant
  -- edit insert a SECOND Lead once the first had failed. That is a real
  -- double-count, because a row can be marked Failed AFTER Meta accepted the
  -- event: services/meta_signal.py marks terminal failure when the send
  -- succeeded but recording it did not ("Sent to Meta, but recording it
  -- failed"). Meta deduplicates on event_id, which is the signal row's own id
  -- — so a replacement row carries a new id, a new event_id, and Meta counts
  -- the conversion twice.
  --
  -- Recovering a Failed Lead is the admin retry's job; it resets the SAME row
  -- to Pending and keeps its id, which is what makes a token-expiry window
  -- recoverable without risking a double count.
  --
  -- `on conflict do nothing` STAYS: two simultaneous final saves can both pass
  -- this check before either inserts, and the index settles that race.
  if not exists (
    select 1 from meta_signal
    where lead_id = new.lead_id and event_name = 'Lead'
  ) then
    insert into meta_signal (lead_id, platform, event_name, triggered_by)
    values (new.lead_id, 'Meta', 'Lead', null)
    on conflict do nothing;
  end if;

  return new;
end;
$$ language plpgsql;

comment on function fn_meta_signal_on_applicant_complete() is
  'Queues the Meta Lead conversion once the applicant''s nine required fields are filled. Writes the outbox row only — the drain sends it.';

drop trigger if exists trg_meta_signal_on_applicant_complete on driver;

create trigger trg_meta_signal_on_applicant_complete
  after insert or update on driver
  for each row when (new.is_principal)
  execute function fn_meta_signal_on_applicant_complete();


-- ── The arrival trigger goes ─────────────────────────────────────────────
--
-- Dropped rather than left to fire alongside the new one. Both write the same
-- (lead_id, 'Lead') pair, and although the new function's `not exists` guard
-- would absorb it, the old one would always win the race — it fires at insert,
-- before any applicant exists — so Lead would still mean "a lead arrived" and
-- nothing would have changed.
drop trigger if exists trg_meta_signal_on_lead_created on lead;
drop function if exists fn_meta_signal_on_lead_created();
