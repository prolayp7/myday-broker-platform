-- Zoom Phone and CardDAV contact sync are retired. The schema follows the code.
--
-- NEITHER REACHED PRODUCTION USE. Zoom was built, parked behind ZOOM_ENABLED
-- (never set anywhere), and brokers moved to ordinary cellular numbers instead.
-- CardDAV was built and wired to a Radicale container, but no broker ever synced
-- their leads through it. So there is no data here worth keeping — which is the
-- only reason this file is a DROP rather than a deprecation.
--
-- ORDER MATTERS. contact_log.zoom_call_id is a foreign key INTO zoom_call, so
-- the column goes before the table it points at. Postgres would refuse the
-- other way round.
--
-- THE CARDDAV TRIGGERS ARE THE URGENT PART. trg_lead_carddav_assign and
-- trg_lead_carddav_content fire on the `lead` table, not on carddav_contact —
-- so with the Python deleted but the triggers left in place, every lead
-- assignment would keep inserting Pending rows nothing would ever drain. The
-- table would grow forever with no reader. Dropping the Python without this
-- file is the one genuinely bad state.
--
-- WHAT IS DELIBERATELY NOT DROPPED:
--   normalize_phone_e164()  — 0058, shared with lead_phone and broker.phone_e164
--   set_updated_at()        — 0000, used by nearly every table
--   contact_log itself      — hand-logged contacts are real history and stay
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.

begin;


-- ── 1. CardDAV triggers first ───────────────────────────────────────────
--
-- Before anything they write to, so no insert can race the drop.
drop trigger if exists trg_lead_carddav_assign on lead;
drop trigger if exists trg_lead_carddav_content on lead;
drop function if exists queue_carddav_contact();
drop function if exists requeue_carddav_contact();

-- The queue itself. Its indexes and updated_at trigger go with it.
drop table if exists carddav_contact;

-- The credential columns. carddav_password_hash was already dropped in 0091 —
-- Radicale kept its own htpasswd file, so a hash here was written by one
-- function and read by nothing.
alter table broker
  drop column if exists carddav_username,
  drop column if exists carddav_password_set_at;


-- ── 2. Zoom: the FK column before the table ─────────────────────────────
--
-- The unique index goes with the column, but named explicitly: an index left
-- behind on a dropped column is not possible, and saying so records that it
-- was considered rather than forgotten.
drop index if exists idx_contact_log_zoom_call_id;
alter table contact_log drop column if exists zoom_call_id;

-- transcript went with it. It was only ever populated from a Zoom recording —
-- a hand-logged contact has nothing to transcribe — so a column nothing can
-- write is a column every query pays for and no screen can use.
alter table contact_log drop column if exists transcript;

-- Now the table. Its own triggers, functions and indexes fall with it.
drop table if exists zoom_call;
drop function if exists set_zoom_call_e164();

-- The broker's Zoom identity. zoom_user_id carried a unique index (0062) which
-- goes with the column.
alter table broker
  drop column if exists zoom_user_id,
  drop column if exists zoom_phone_number,
  drop column if exists zoom_extension;


-- ── 3. Dial intent ──────────────────────────────────────────────────────
--
-- Recorded which lead an outbound call was about, so an inbound Zoom webhook
-- could attribute it. With no webhook there is nothing to match against, and
-- its only endpoint was removed when Zoom was parked.
drop table if exists dial_intent;


commit;
