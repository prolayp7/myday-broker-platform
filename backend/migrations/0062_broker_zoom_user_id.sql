-- Which Zoom Phone user this broker is.
--
-- THE LINK THAT MAKES INBOUND CALLS ATTRIBUTABLE. Zoom's webhook says a call
-- was answered by user "abc123XYZ" — its own opaque id, not an email, not a
-- name. Without a column holding that id, there is nothing to turn it into a
-- broker row, and contact_log.broker_id (NOT NULL) cannot be filled at all.
--
-- Outbound never needed this: the broker placing the call is the logged-in
-- user, known from their own session. Inbound is the case with no session
-- behind it — someone simply picked up a ringing phone.
--
-- WHY NOT MATCH ON EMAIL: Zoom does expose the user's email on some payloads,
-- and matching broker.email against it would avoid this column entirely. It
-- was rejected — a broker whose work email differs from their Zoom login, or
-- who changes either one, silently stops being attributable, and the failure
-- looks like "some calls just aren't logged". An explicit id is set once and
-- keeps working.
alter table broker add column zoom_user_id text;

-- Unique, because two brokers claiming one Zoom user would make every call
-- from that user ambiguous. Nullable: brokers who do not take calls, and every
-- broker already in the table before Zoom is switched on, have none.
create unique index idx_broker_zoom_user_id on broker(zoom_user_id)
  where zoom_user_id is not null;
