-- Phone numbers in one canonical form, so a Zoom call can find its lead.
--
-- WITHOUT THIS NOTHING MATCHES. Meta delivers "+14165551234", a broker types
-- "(416) 555-1234", Zoom reports "4165551234". Three spellings of one number,
-- zero string equality between them. Every call would land unmatched.
--
-- TWO TABLES CARRY A CUSTOMER PHONE and both are normalised here:
--   lead.phone   — what the ad captured. Always present, never verified.
--   driver.phone — what the broker confirmed on the call. Present once the
--                  lead has been worked, and the number that actually reaches
--                  the person.
-- Matching reads BOTH. Searching only the lead would miss every customer whose
-- number was corrected — which is precisely the ones a broker has spoken to.
--
-- Runs BEFORE the Zoom tables (0059-0061) on purpose: normalize_phone_e164()
-- defined here is used by zoom_call's own trigger, so it has to exist first.
--
-- Kept as a SEPARATE COLUMN rather than normalising lead.phone in place: the
-- original is what the customer actually gave us and what a broker reads on
-- screen, and rewriting it would quietly destroy extensions, notes, and second
-- numbers that people really do type into a phone field.

-- ── The normaliser ───────────────────────────────────────────────────────
-- Canada/US only, which is all this brokerage writes: RIBO licenses Ontario
-- and every lead is Ontario today. A number that is not recognisably NANP
-- returns null and simply never matches — deliberately better than returning
-- a mangled string that matches the WRONG lead.
create or replace function normalize_phone_e164(raw text)
returns text as $$
declare
  digits text;
begin
  if raw is null then
    return null;
  end if;

  -- lead.phone and lead.email are NOT NULL, so a lead captured with only one
  -- of them carries the literal string "(not provided)" in the other (see
  -- services/lead.py). Those placeholders reduce to zero digits and must never
  -- be treated as a number — otherwise every placeholder lead shares one
  -- "phone" and a single call matches all of them at once.
  digits := regexp_replace(raw, '\D', '', 'g');

  -- 10 digits = a bare NANP number, e.g. 4165551234.
  if length(digits) = 10 then
    return '+1' || digits;
  end if;

  -- 11 digits starting with 1 = the same number already carrying its country
  -- code, e.g. 14165551234.
  if length(digits) = 11 and left(digits, 1) = '1' then
    return '+' || digits;
  end if;

  -- Anything else: an extension, an international number, a placeholder, or
  -- junk. Null rather than a guess.
  return null;
end;
$$ language plpgsql immutable;

-- ── The column ───────────────────────────────────────────────────────────
alter table lead add column phone_e164 text;

-- Maintained by the database, not by application code. Leads are created down
-- several different paths — Meta ingestion, the manual-entry form, admin
-- tools — and a trigger is the only place that catches all of them. A service-
-- layer normalise would be correct on the path someone remembered and silently
-- absent on the others.
create or replace function set_lead_phone_e164()
returns trigger as $$
begin
  new.phone_e164 = normalize_phone_e164(new.phone);
  return new;
end;
$$ language plpgsql;

create trigger trg_lead_phone_e164
  before insert or update of phone on lead
  for each row execute function set_lead_phone_e164();

-- Backfill every lead already in the table.
update lead set phone_e164 = normalize_phone_e164(phone);

-- The matching lookup. Partial: placeholder and unparseable numbers are null
-- and are never searched for, so they stay out of the index entirely.
create index idx_lead_phone_e164 on lead(phone_e164)
  where phone_e164 is not null;

-- ── The same, on driver ──────────────────────────────────────────────────
-- THE NUMBER THAT ACTUALLY REACHES THE CUSTOMER. driver.phone is seeded from
-- the lead when the applicant row is created, then CORRECTED by the broker
-- over the call — so where the two differ, this is the verified one and
-- lead.phone is the marketing capture it replaced. A customer ringing back
-- from their corrected number would not match on lead.phone alone.
--
-- Every driver, not just the applicant: a spouse or child on the policy has
-- their own number and can ring in about the same lead.
alter table driver add column phone_e164 text;

create or replace function set_driver_phone_e164()
returns trigger as $$
begin
  new.phone_e164 = normalize_phone_e164(new.phone);
  return new;
end;
$$ language plpgsql;

create trigger trg_driver_phone_e164
  before insert or update of phone on driver
  for each row execute function set_driver_phone_e164();

update driver set phone_e164 = normalize_phone_e164(phone);

create index idx_driver_phone_e164 on driver(phone_e164)
  where phone_e164 is not null;
