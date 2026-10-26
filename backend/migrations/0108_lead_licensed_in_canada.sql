-- lead.licensed_in_canada — the Meta form's qualifying question.
--
-- WHY IT GETS A COLUMN. The answer has been arriving since the form went live
-- and is already stored: meta_lead_parser flattens Meta's field_data and
-- ingestion writes the whole map into lead.extra_data (0008). But extra_data
-- is an untyped jsonb blob, so nothing can query it, no screen can show it,
-- and no notification can include it. A named column is what makes an answer
-- we already have usable.
--
-- WHY IT MATTERS. It is the single biggest qualifier on an Ontario auto lead —
-- someone first licensed in 2020 or before rates very differently from 2021 or
-- later — so a broker who knows it before dialling knows how to pitch.
--
-- THE VALUES ARE THE FORM'S OWN, READ OFF REAL LEADS, not invented here:
--   "when_were_you_first_licensed_in_canada?" -> "2020_or_before" | "2021_or_later"
-- Stored in the form's own vocabulary rather than prettified on the way in.
-- Display text is a presentation concern and belongs at the edge; rewriting
-- values on ingest would mean this column and extra_data disagreed about what
-- the customer actually answered.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.

alter table lead add column licensed_in_canada text
  check (licensed_in_canada in ('2020_or_before', '2021_or_later'));

-- NULLABLE, AND THAT IS NOT AN OVERSIGHT. Website and phone leads never answer
-- it, and the Meta form could be edited to drop the question tomorrow. A
-- missing answer must never block a lead from being created — the same rule
-- meta_lead_parser already follows for name, phone and email, where a
-- placeholder is always preferred to a rejected lead.

-- Queried by the admin lead list when filtering on the qualifier. Partial:
-- leads with no answer are never filtered FOR, so they stay out of the index.
create index idx_lead_licensed_in_canada on lead(licensed_in_canada)
  where licensed_in_canada is not null;


-- ── backfill from what was never thrown away ─────────────────────────────
--
-- Every Meta lead already carries the answer in extra_data, so this is a pure
-- read of data we already hold — no re-fetch from Meta, nothing reconstructed.
--
-- The ->> operator returns null for a lead whose form did not ask, and the
-- case expression maps only the two known values: anything unrecognised stays
-- null rather than violating the check constraint. The raw value is still in
-- extra_data either way, so nothing is lost by being conservative here.
update lead
   set licensed_in_canada = case extra_data->>'when_were_you_first_licensed_in_canada?'
                              when '2020_or_before' then '2020_or_before'
                              when '2021_or_later'  then '2021_or_later'
                              else null
                            end
 where extra_data ? 'when_were_you_first_licensed_in_canada?';
