-- Where OUR copy of the recording lives, once we have fetched it.
--
-- SEPARATE FROM recording_url, which is Zoom's own download link and expires
-- (~30 days, and it needs an OAuth token besides). Overwriting that column
-- with our path would destroy the only record of where the audio came from,
-- which is what a re-fetch would need if an upload ever failed halfway.
--
-- Holds the object PATH, not a URL — e.g.
--   recordings/2026/07/31/06a6c159c2a5b1df1d5.mp3
-- The bucket name lives in configuration (GCS_RECORDINGS_BUCKET), so moving
-- buckets, or ever moving providers, does not mean rewriting every row. And a
-- stored URL would be either permanent — which the bucket refuses, public
-- access being blocked — or already expired by the time anyone read it.
--
-- DATE-FIRST PATH, deliberately. Retention is "1 year for a lost lead, 7 for a
-- customer", which the cleanup job runs as a prefix scan over a month at a
-- time. Keying by lead instead would make that a full-bucket walk, and would
-- leave a call that matched no lead with nowhere to go.
alter table zoom_call add column recording_object_path text;

-- Null until fetched. Set once the audio is in our own storage, so a broker
-- clicking Play weeks later reaches a file that still exists rather than a
-- Zoom URL that has since died.
--
-- Partial: the many calls with no recording (and every call not yet fetched)
-- stay out of the index entirely.
create index idx_zoom_call_recording_object on zoom_call(recording_object_path)
  where recording_object_path is not null;
