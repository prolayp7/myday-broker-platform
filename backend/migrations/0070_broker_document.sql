-- Broker documents: the proof behind the compliance fields.
--
-- Onboarding already collects ribo_license_number, license_expiry_date and the
-- E&O term (migration 0003) — but every one of those is an admin typing what a
-- document said. Nothing holds the document. Asked by a regulator, an insurer
-- or the principal to produce a broker's E&O certificate, the answer lived in
-- somebody's inbox.
--
-- THREE KINDS, all optional. Photo, RIBO licence, E&O certificate. Optional
-- deliberately: onboarding completes exactly as it did before, and the files
-- attach afterwards. An admin onboarding someone at 5pm is not blocked because
-- a PDF is on the broker's laptop.
--
-- ONE ROW PER BROKER PER KIND — that is what the unique constraint says.
-- Re-uploading an E&O certificate REPLACES the row and deletes the old object;
-- there is no version history. A broker has one current E&O certificate, and
-- keeping every superseded copy would leave the screen having to explain which
-- of four files is the live one. The renewal case is a replacement, not a
-- second document.
--
-- NO EXPIRY COLUMN, deliberately. license_expiry_date and eo_expiry_date
-- already exist on broker and are already on the onboarding form. A date here
-- too would be the same fact in two places, free to disagree, with nothing to
-- say which one a renewal reminder should believe. The document is proof; the
-- broker record is the fact.
--
-- THE PATH, NOT A URL — same reasoning as zoom_call.recording_object_path
-- (migration 0064). The bucket lives in the storage client, not in this row,
-- so moving buckets or providers is not a rewrite of every row. A stored URL
-- would be either permanent — which the bucket refuses, being private — or
-- already expired by the time anyone read it. Browsers get a short-lived
-- signed URL, generated per request.
--
-- SUPABASE STORAGE, not the GCS bucket recordings use (see
-- backend/supabase_storage.py). Recordings carry a retention policy (1 year
-- for a lost lead, 7 for a customer), a prefix-scanning cleanup job, real
-- volume and metered egress. These are three small files per broker, and
-- belong beside the rows that point at them.
create table broker_document (
  id uuid primary key default gen_random_uuid(),

  -- ON DELETE CASCADE, unlike most references to broker. Brokers are archived
  -- rather than deleted in normal operation (migration 0011), so this fires
  -- only on a genuine hard delete — and a document whose broker is gone is an
  -- orphan pointing at a file nobody may look at. The object itself is removed
  -- by the service before the row goes; this is the backstop.
  broker_id uuid not null references broker(id) on delete cascade,

  kind text not null check (kind in ('photo', 'ribo_license', 'eo_certificate')),

  -- e.g. brokers/{broker_id}/eo_certificate-9f2c4a....pdf
  --
  -- Broker-first, unlike recordings' date-first keying: the retention question
  -- here is "this broker left, remove their files", which is a prefix delete.
  -- The random suffix means a replacement never overwrites in place, so a
  -- failed upload cannot destroy the existing good copy before the new one is
  -- confirmed written.
  object_path text not null,

  -- What the admin's file was called. Kept so a download arrives as
  -- "RIBO licence 2026.pdf" rather than the opaque object key, which is the
  -- only thing the object path is good for.
  file_name text not null,
  content_type text not null,
  size_bytes int not null check (size_bytes > 0),

  -- WHO attached it. Nullable because the reference outlives the actor: an
  -- admin who has since been hard-deleted must not take the document with
  -- them. The audit_log row carries the same fact and is the real record.
  uploaded_by uuid references broker(id),
  uploaded_at timestamptz not null default now(),

  unique (broker_id, kind)
);

-- Every read is "this broker's documents" — the detail screen listing all
-- three, or the header asking for one photo. There is no query that scans by
-- kind across brokers.
create index idx_broker_document_broker on broker_document(broker_id);

-- Deny-all, like every other table here. The API reaches Supabase with the
-- service role key; nothing else should reach licence documents at all.
alter table broker_document enable row level security;
