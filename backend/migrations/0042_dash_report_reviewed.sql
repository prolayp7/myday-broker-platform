-- Broker-review state for a parsed DASH report.
--
-- Flow (see docs/dash-integration-design.md §4): Document AI + the middleware
-- auto-save the parsed result into the 6 DASH tables in the background, so it
-- has a durable home the instant it's ready — surviving a restart, a closed
-- tab, or the broker being pulled onto another call. parse_status = 'complete'
-- then means "parsed and stored", but NOT yet human-confirmed.
--
-- broker_reviewed is the second signal: false while the data is only
-- machine-parsed, flipped true the moment the broker reviews it and clicks Save
-- (PUT /dash-reports/{id}). This lets any downstream reader tell
-- parsed-but-unconfirmed apart from broker-confirmed data, without conflating
-- it with parse_status (which is about the PARSE lifecycle, not human review).
alter table dash_report
  add column broker_reviewed boolean not null default false;
