-- The original "unique" on community.name is case-sensitive, so "Dragon"
-- and "dragon" could both exist as separate rows. Replace it with a
-- case-insensitive unique index instead.
alter table community drop constraint community_name_key;
create unique index community_name_lower_idx on community (lower(name));
