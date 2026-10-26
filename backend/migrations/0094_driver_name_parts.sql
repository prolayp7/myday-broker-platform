-- The driver's name, split into the parts Meta actually matches on.
--
-- Meta hashes fn and ln as SEPARATE parameters. We store one `name` column and
-- meta_capi.split_name splits it on the last space, which is wrong for the
-- format DASH prints. Measured on the live rows the day this was written:
--
--   split_name('CHEEMA,SAJIDA')   -> ('CHEEMA,SAJIDA', None)
--   split_name('KENNETH,DESIREE') -> ('KENNETH,DESIREE', None)
--
-- The whole string lands in fn, hash_name_part strips the comma, and Meta
-- receives 'cheemasajida' — a fused token that matches nobody — with no ln at
-- all. Both name parameters wasted on every DASH-sourced applicant.
--
-- WHY THREE COLUMNS AND NOT TWO. Meta has no middle-name parameter, so
-- middle_name is never sent. It exists so a middle name has somewhere to go
-- that ISN'T first_name: 'SMITH,JOHN MICHAEL' with the middle fused gives
-- fn='johnmichael', which matches nobody, against fn='john', which matches.
-- Real middle names are in the data — of six names with three parts, four are
-- full words (Leonora Llanes Bigornia, Rajiv Krishnan Radha) and only two are
-- initials. The column earns its place by keeping first_name clean.
--
-- `name` STAYS, and stays the thing DASH writes. It holds the report's printed
-- form, which the broker compares against the document in front of them —
-- dash_apply returns it as subject_name for exactly that. These three are the
-- split OF that name, not a replacement for it.
--
-- NULLABLE AND NOT BACKFILLED. Every existing row starts null and fills when
-- its name is next set. Backfilling would mean re-deriving 46 names to
-- populate columns that nothing reads until the sender is switched over in the
-- same migration series — work whose only output would be rows nobody looks
-- at. A null here reads correctly as "not split yet".
--
-- Safe to re-run: add column if not exists, and nothing else in the file.

alter table driver add column if not exists first_name  text;
alter table driver add column if not exists middle_name text;
alter table driver add column if not exists last_name   text;

comment on column driver.first_name is
  'Given name. Hashed to Meta as fn. Set by claude_client.split_name, correctable by the broker.';
comment on column driver.middle_name is
  'Middle name or initial. NEVER sent to Meta — it exists so a middle name does not end up fused into first_name, where it would hash to something matching nobody.';
comment on column driver.last_name is
  'Surname, which may be more than one word (RAJI MOL). Hashed to Meta as ln.';
