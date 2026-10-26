-- Makes a failed webhook_event actually recoverable instead of just a
-- generic red flag: WHY it failed (unmapped form_id, Graph API error, etc.)
-- so an admin can fix the real cause and re-run ingestion against this same
-- stored event later. Nullable — only ever set when status = 'failed'.
alter table webhook_event add column failure_reason text;
