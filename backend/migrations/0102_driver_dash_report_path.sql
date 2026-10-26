-- WHERE THIS DRIVER'S DASH REPORT IS KEPT.
--
-- Until now the PDF was read and dropped — routes/dash.py said so outright:
-- "THE PDF IS NOT STORED. It is read, used, and dropped." So the parse was
-- one-shot: if Azure timed out (90s cap), returned a bad scan, or was down,
-- the upload was gone and the broker started over with the same file.
--
-- THE PATH, NOT THE FILE, AND NOT A URL. The object lives in Supabase Storage
-- and this column points at it; a link is minted per download and expires in
-- fifteen minutes, so storing a URL would be storing something that stops
-- working. Same shape broker_document and the Zoom recordings already use.
--
-- ON DRIVER, NOT LEAD. A DASH report describes ONE person, and a lead can
-- carry two drivers — the applicant and a spouse. One column on lead could
-- hold only one of their reports. Every other field DASH fills already lives
-- here (0094 name parts, 0098 policy dates), so this is where it belongs.
--
-- NOT IN UpdateDriverRequest, deliberately: this is written by the upload
-- path alone. A broker typing a storage path by hand is not a thing that
-- should be possible.
--
-- NO CASCADE, and worth stating because it looks like there is one. Supabase
-- Storage has no foreign key to Postgres — deleting this row does NOT remove
-- the object. services/driver.py::delete_driver does that explicitly, row
-- first so a storage failure can never leave a live driver pointing at a file
-- that is gone.
--
-- STILL NO RETENTION RULE. A lead that never converts keeps its customer's
-- claims history until someone decides otherwise. Named here rather than
-- implied, so the next person reads it as an open question and not an
-- oversight.

alter table driver add column dash_report_path text;

comment on column driver.dash_report_path is
  'Object path of this driver''s stored DASH PDF in the dash-pdfs bucket. Null until a report is uploaded. Written only by the DASH upload path.';
