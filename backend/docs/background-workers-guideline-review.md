# Background Workers — Guideline Accuracy Review

`architecture-guidelines.md` §6 ("Background workers") generalizes rules
from *one* loop (the Meta CAPI drain) across *both* background loops (the
CAPI drain and the Zoom recording sweep) as if they behave identically.
Checking each claim against the actual code (`services/meta_signal.py`,
`services/zoom_recording.py`, `repositories/meta_signal.py`,
`repositories/zoom_call.py`, `routes/zoom_webhooks.py`, and migrations
`0057_meta_signal_triggers.sql` / `0059_zoom_call.sql`) shows two of the
four claims don't actually hold for the Zoom sweep. This file documents
exactly what's inaccurate, why, and what a genuine fix looks like — kept
separate from `architecture-guidelines.md` itself since this is a review
of that document, not new architecture guidance.

The four bullets under review, verbatim from §6:

> - **Never let an exception escape the loop.**
> - **Run blocking work in a thread** (`asyncio.to_thread(...)`).
> - **Cap retries with a terminal state**, don't retry forever.
> - **Single-process only.**

Plus the opening framing sentence:

> Both exist because a Postgres trigger can create a queue row but cannot
> make an HTTP call — something in the app has to come along afterward and
> actually send/fetch.

---

## Accurate as written — no change needed

**"Never let an exception escape the loop"** — true for both.
`run_drain_forever()` wraps `asyncio.to_thread(send_pending_signals)` in
`try/except Exception`; `run_sweep_forever()` wraps
`asyncio.to_thread(sweep)` the same way. Both re-raise on
`asyncio.CancelledError` (so shutdown still works) and log-and-continue on
anything else.

**"Run blocking work in a thread"** — true for both. Both loops call their
work via `asyncio.to_thread(...)`, and both do so for the same stated
reason (blocking HTTP/Supabase calls would stall the event loop otherwise).

These two survive the review unchanged.

---

## Inaccurate #1 — the "Postgres trigger can't make an HTTP call" framing only explains one of the two loops

**The claim:** "Both exist because a Postgres trigger can create a queue
row but cannot make an HTTP call."

**What's actually true:**

- **Meta CAPI drain — accurate.** `meta_signal` rows are written by
  Postgres trigger functions (`migrations/0057_meta_signal_triggers.sql`,
  e.g. `fn_meta_signal_on_lead_delivery()`), fired directly off
  `lead_delivery`/stage-change writes that themselves happen in SQL
  (`assign_lead`, `update_lead_stage`). The migration's own header explains
  why triggers were chosen over application code: some of those writes
  (round-robin assignment, the daily reset drain) happen entirely in SQL
  with no Python code in the path, so a Python-side hook would silently
  miss them. A trigger can insert a row but cannot itself call Meta's API —
  which is genuinely why a separate drain process has to exist.

- **Zoom recording sweep — not accurate.** There is no trigger involved.
  The "queue" the sweep reads (`zoom_call` rows where
  `recording_object_path IS NULL AND call_result = 'connected'`,
  `repositories/zoom_call.py::list_awaiting_recording()`) is populated by
  **application code**: `routes/zoom_webhooks.py`'s `receive_zoom_webhook()`
  handler calls `zoom_ingestion.ingest_call_event()` to write the row in
  the first place, and separately calls
  `zoom_recording.store_for_call_id()` synchronously when a
  `phone.recording_completed` event arrives. The sweep exists for a
  different reason entirely, one the code's own comments already state
  correctly: it's a safety net for a **dropped or missed webhook
  delivery**, not a workaround for triggers being unable to make HTTP
  calls. From `services/zoom_recording.py`'s module docstring: *"TWO WAYS
  IN, deliberately. The webhook copies within seconds of a call ending;
  the sweep catches anything the webhook missed."*

**Why this matters:** the guideline's framing implies a shared root cause
("triggers can't do HTTP") that would mislead anyone building a *third*
background loop into thinking that's the only valid justification for one.
It isn't — "retry a webhook-driven side effect that might have silently
failed to fire" is an equally valid, and here more common, reason, and
it comes with different failure semantics (see next section).

**Suggested corrected text for §6's opening paragraph:**

> Two long-running `asyncio` tasks are started in `main.py`'s `lifespan`
> hook and cancelled explicitly on shutdown: the Meta CAPI drain
> (`services/meta_signal.py::run_drain_forever`) and the Zoom recording
> sweep (`services/zoom_recording.py::run_sweep_forever`). They exist for
> two related but distinct reasons. The CAPI drain exists because
> `meta_signal` rows are written by Postgres triggers
> (`migrations/0057_meta_signal_triggers.sql`) that can insert a row but
> cannot make an HTTP call — something has to come along afterward and
> actually send it. The Zoom sweep exists because the webhook that
> normally copies a recording within seconds can be dropped or missed
> entirely (a Zoom outage, a deploy mid-restart) with no error raised
> anywhere — the sweep is the safety net that guarantees a missed webhook
> doesn't mean a permanently lost recording.

---

## Inaccurate #2 — "cap retries with a terminal state" only holds for the CAPI drain

**The claim:** "Cap retries with a terminal state, don't retry forever...
after `MAX_ATTEMPTS`, mark the row terminally failed so it surfaces to an
admin instead of silently retrying forever."

**What's actually true:**

- **Meta CAPI drain — accurate, and well-built.**
  `services/meta_signal.py` defines `MAX_ATTEMPTS = 3`, and
  `_send_one()` calls `repo.mark_attempt_failed(row["id"], ..., attempt_count,
  terminal=exhausted)` on both a Meta-side rejection (immediately terminal
  — retrying an identical bad payload can't succeed) and a transient
  failure once attempts are exhausted. `repositories/meta_signal.py`'s
  `mark_attempt_failed()` writes `status: 'Failed'` when `terminal=True`,
  explicitly so the row is never picked up again — this is a real,
  admin-visible terminal state.

- **Zoom recording sweep — this rule does not exist in the code at all.**
  `repositories/zoom_call.py::list_awaiting_recording()` is:

  ```python
  def list_awaiting_recording(limit: int = 50):
      return (
          supabase.table("zoom_call")
          .select("id, zoom_call_id, occurred_at")
          .is_("recording_object_path", "null")
          .eq("call_result", "connected")
          .order("occurred_at")
          .limit(limit)
          .execute()
      )
  ```

  There is no `attempt_count`, no `retry_count`, no terminal/failed status
  column on `zoom_call` at all (confirmed against
  `migrations/0059_zoom_call.sql`, which defines the table, and every
  later Zoom-related migration — none add one). `sweep()` in
  `services/zoom_recording.py` catches per-call exceptions
  (`except Exception: failed += 1; logger.exception(...)`) but never marks
  anything as done-trying. The consequence is two genuinely unbounded
  cases:

  1. **A connected call that Zoom simply never recorded.**
     `store_recording()` calls `zoom_client.find_recording(zoom_call_id)`;
     if that returns `None` (normal — not every connected call is
     recorded), the function returns `None` without ever writing
     `recording_object_path`. The row still has
     `call_result = 'connected'` and `recording_object_path IS NULL`, so
     it matches `list_awaiting_recording()` again on the *next* sweep, and
     every sweep after that, forever. There is no way to record "checked,
     confirmed no recording exists" as distinct from "haven't checked
     yet."
  2. **A genuinely permanent failure** (corrupted download, a
     misconfigured `GCS_RECORDINGS_BUCKET`, a Zoom API error that will
     never resolve) is retried every `_SWEEP_INTERVAL_SECONDS` (600s)
     indefinitely, with no admin-visible signal that this particular call
     is stuck — unlike a `Failed` `meta_signal` row, there's nothing to
     query for "which recordings need a human."

  Both cases mean the sweep's candidate pool
  (`list_awaiting_recording()`'s result set) can only grow over time,
  never shrink for a row that will never succeed — a slow, unbounded
  increase in both the query's row count and the number of (pointless,
  for case 1) calls made to Zoom's API every 10 minutes.

**Suggested corrected text for §6's bullet:**

> - **Cap retries with a terminal state where the underlying data supports
>   it.** The Meta CAPI drain does this: `MAX_ATTEMPTS = 3`, and a
>   Meta-side rejection or an exhausted retry count marks the `meta_signal`
>   row `Failed` (`repositories/meta_signal.py::mark_attempt_failed`) so it
>   surfaces to an admin instead of retrying forever. **The Zoom recording
>   sweep does not currently have an equivalent mechanism** — `zoom_call`
>   has no attempt-count or terminal-status column, so a call that will
>   never have a recording (Zoom genuinely has none) or whose fetch fails
>   permanently is retried every sweep cycle indefinitely, with no
>   admin-visible "stuck" state. See
>   `docs/background-workers-guideline-review.md` for the gap and a
>   proposed fix. When writing a new retry loop, don't assume this pattern
>   is already universal — check whether the underlying table actually has
>   the columns to support it.

**Proposed fix for the actual gap (not just the doc), for consideration:**

Add two columns to `zoom_call` (new migration, following the numbering and
house-rules convention in `README.md` — indexed if used in a filter,
`updated_at` trigger already exists on the table):

```sql
alter table zoom_call
  add column recording_fetch_attempts int not null default 0,
  add column recording_fetch_status text
    check (recording_fetch_status in ('pending', 'no_recording', 'failed'));
```

- `recording_fetch_status = 'no_recording'` — set when
  `zoom_client.find_recording()` returns `None`. Distinguishes "Zoom
  confirmed there's nothing to fetch" from "haven't successfully checked
  yet," so case 1 above stops being re-queried forever. (If a recording
  can legitimately appear late — e.g. Zoom finishes processing after the
  first check — this status would need its own short re-check window
  rather than being permanently excluded; worth confirming with Zoom's own
  processing-time behavior before treating it as truly terminal.)
- `recording_fetch_status = 'failed'` once `recording_fetch_attempts` hits
  a `MAX_ATTEMPTS`-style cap on genuine errors (download/upload
  exceptions), mirroring `meta_signal`'s pattern — surfaced to an admin
  view rather than retried forever.

`list_awaiting_recording()` would then filter to
`recording_fetch_status is null or recording_fetch_status = 'pending'`
(or simply exclude `'no_recording'`/`'failed'`) instead of the current
unconditional `recording_object_path IS NULL`.

---

## Inaccurate #3 (minor) — "race over the same pending rows" doesn't match how the Zoom sweep describes its own multi-worker safety

**The claim:** "Single-process only... Multiple workers would each start
their own copy and race over the same pending rows."

**What's actually true:** this framing matches the CAPI drain's own
module docstring closely (*"Under multiple workers each would start its
own copy of this loop and they would race for the same Pending rows"*) —
accurate for that loop, and a genuine risk since two processes racing to
send the same `meta_signal` row could both attempt a send before either
writes `mark_sent`.

The Zoom sweep's own docstring describes a **different, more benign**
outcome under the same multi-worker scenario: *"Two processes would both
sweep; the work is idempotent, so the cost is duplicated effort rather
than duplicated recordings."* `store_recording()` supports this — it
checks `zoom_call_row.get("recording_object_path")` first and returns
early if already set, so a second process re-processing the same row
mid-flight would (assuming the first process's write lands first) find
nothing to do.

This is a smaller inaccuracy than the two above — not wrong exactly, but
the guideline states one risk level ("race," implying contention/possible
duplicate work with real consequence) as if it applies uniformly, when the
two loops' own authors documented different outcomes for the same
scenario. Worth tightening so a reader doesn't assume the Zoom sweep is as
fragile under accidental multi-worker deployment as the CAPI drain is.

**Suggested corrected text:**

> - **Single-process only.** Both loops assume exactly one process (no
>   `--workers` in the Procfile). Under the CAPI drain, two processes would
>   genuinely race for the same `Pending` `meta_signal` row — a real
>   correctness risk, not just wasted work. Under the Zoom sweep, two
>   processes would both attempt the same call, but `store_recording()`'s
>   existing-path check makes that idempotent — duplicated effort, not
>   duplicated recordings or corrupted state. Neither is a reason to
>   actually run multiple workers; both assumptions should be revisited
>   together if the deploy ever changes.

---

## Summary

| Bullet in §6 | Holds for CAPI drain? | Holds for Zoom sweep? | Action |
|---|---|---|---|
| Never let an exception escape the loop | Yes | Yes | No change |
| Run blocking work in a thread | Yes | Yes | No change |
| Postgres-trigger framing (opening sentence) | Yes | **No** — no trigger involved; it's a missed-webhook safety net | Reword, see above |
| Cap retries with a terminal state | Yes | **No** — no attempt/status column exists on `zoom_call` | Reword doc + genuine schema/code gap, proposed fix above |
| Single-process only / "race" | Yes (real race) | Partially — multi-worker is idempotent-safe per the code's own docs, not a race | Reword, see above |
