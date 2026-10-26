-- Shared trigger function, reused on every table's updated_at column.
-- Attach with: create trigger trg_<table>_updated_at before update on <table>
--              for each row execute function set_updated_at();
create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;
