import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'

import { AppShell } from '@/components/AppShell'
import { LeadStageBadge } from '@/components/LeadStageBadge'
import { Button } from '@/components/ui/button'
import {
  completeReminder,
  dismissReminder,
  firstValidationMessage,
  getMyReminders,
  snoozeReminder,
  type Reminder,
} from '@/data/api'
import { cn } from '@/lib/utils'

/**
 * THE BROKER'S WHOLE LIST, in one place.
 *
 * Reminders could only be seen inside a single lead until now, on a tab that
 * is currently hidden. A broker's morning question is "what do I owe today?"
 * across their book, and nothing answered it — /my-day/reminders has returned
 * the counts since 0061 with no caller at all.
 *
 * A WORKLIST, not a dashboard. Dense rows, everything actionable without
 * opening anything, and stage plus renewal date on each line so a list can be
 * triaged by reading it: a Lost lead renewing next week is a different job
 * from a Quote Done chasing a document.
 */

/** The Ontario day boundary, which is the one that matters — a follow-up due
 *  at 11pm is due TODAY, and computing that in UTC would push it to tomorrow.
 *  The backend makes the same distinction (services/reminder.py::_TZ). */
function ontarioToday(): Date {
  const now = new Date()
  const local = new Date(
    now.toLocaleString('en-US', { timeZone: 'America/Toronto' }),
  )
  local.setHours(0, 0, 0, 0)
  return local
}

type Bucket = 'overdue' | 'today' | 'week' | 'later'

/** Which bucket a reminder falls in. MUTUALLY EXCLUSIVE, so the chip counts
 *  sum to the total — a filter strip whose numbers do not add up is one
 *  nobody trusts. */
function bucketOf(dueAt: string, today: Date): Bucket {
  const due = new Date(dueAt)
  const dueDay = new Date(due)
  dueDay.setHours(0, 0, 0, 0)

  if (dueDay < today) return 'overdue'
  if (dueDay.getTime() === today.getTime()) return 'today'

  // The rest of this week, Ontario-reckoned. Sunday is 0, so this counts
  // forward to Saturday rather than assuming a Monday start.
  const endOfWeek = new Date(today)
  endOfWeek.setDate(today.getDate() + (6 - today.getDay()))
  return dueDay <= endOfWeek ? 'week' : 'later'
}

/** "2d ago", "Today", "Tomorrow", "in 5d" — a relative age reads faster than a
 *  date when the question is what to do next. */
function relativeDue(dueAt: string, today: Date): string {
  const due = new Date(dueAt)
  const dueDay = new Date(due)
  dueDay.setHours(0, 0, 0, 0)
  const days = Math.round(
    (dueDay.getTime() - today.getTime()) / (1000 * 60 * 60 * 24),
  )

  if (days === 0) {
    return `Today ${due.toLocaleTimeString('en-CA', {
      hour: 'numeric',
      minute: '2-digit',
    })}`
  }
  if (days === 1) return 'Tomorrow'
  if (days === -1) return 'Yesterday'
  return days < 0 ? `${-days}d ago` : `in ${days}d`
}

function shortDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(`${iso}T00:00:00`).toLocaleDateString('en-CA', {
    day: '2-digit',
    month: 'short',
  })
}

const CHIPS: { key: Bucket | 'all'; label: string }[] = [
  { key: 'overdue', label: 'Overdue' },
  { key: 'today', label: 'Today' },
  { key: 'week', label: 'This week' },
  { key: 'later', label: 'Later' },
  { key: 'all', label: 'All' },
]

export default function Reminders() {
  const [rows, setRows] = useState<Reminder[] | null>(null)
  const [filter, setFilter] = useState<Bucket | 'all'>('all')
  const [busy, setBusy] = useState<string | null>(null)

  const today = useMemo(ontarioToday, [])

  const load = useCallback(async () => {
    try {
      setRows(await getMyReminders())
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not load your reminders')
      setRows([])
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const counts = useMemo(() => {
    const c: Record<Bucket | 'all', number> = {
      overdue: 0,
      today: 0,
      week: 0,
      later: 0,
      all: rows?.length ?? 0,
    }
    for (const r of rows ?? []) c[bucketOf(r.due_at, today)] += 1
    return c
  }, [rows, today])

  const visible = useMemo(
    () =>
      (rows ?? []).filter(
        (r) => filter === 'all' || bucketOf(r.due_at, today) === filter,
      ),
    [rows, filter, today],
  )

  /**
   * Close or push one reminder.
   *
   * OPTIMISTIC ON THE LIST, not on the row: the reminder leaves the list
   * entirely when it closes, and animating a row that is about to vanish
   * reads as a glitch. Reloaded on failure so the list matches the server
   * rather than a guess.
   */
  async function act(r: Reminder, how: 'complete' | 'dismiss' | 'snooze') {
    setBusy(r.id)
    try {
      if (how === 'snooze') {
        // 24 HOURS FROM NOW, not from the original due time. Adding a day to
        // due_at looks equivalent and is not: a reminder that fell due on
        // Monday and is snoozed on Wednesday would move to TUESDAY — still in
        // the past, immediately overdue again, and now that a due callback
        // sends a WhatsApp it would fire again within the minute. Snoozing
        // has to buy you a day from the moment you press it.
        //
        // No date picker, deliberately: a dialog in the way of a one-click
        // action. A broker who wants a specific day sets a new reminder.
        const next = new Date(Date.now() + 24 * 60 * 60 * 1000)
        await snoozeReminder(r.id, next.toISOString())
      } else if (how === 'complete') {
        await completeReminder(r.id)
      } else {
        await dismissReminder(r.id)
      }
      await load()
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'That did not save')
      await load()
    } finally {
      setBusy(null)
    }
  }

  return (
    <AppShell section="Reminders">
      <div className="mx-auto max-w-5xl space-y-3 p-6">
        {/* THE CHIPS CARRY THEIR OWN COUNTS, like the Leads rail. The page
            answers "how much do I owe" before anything is clicked, which is
            the question it exists for. */}
        <div className="flex flex-wrap items-center gap-1.5">
          {CHIPS.map((chip) => {
            const active = filter === chip.key
            const n = counts[chip.key]
            return (
              <button
                key={chip.key}
                type="button"
                onClick={() => setFilter(chip.key)}
                aria-pressed={active}
                className={cn(
                  'rounded-md border px-2.5 py-1 text-xs transition-colors duration-200',
                  active
                    ? 'bg-foreground text-background border-foreground font-medium'
                    : 'border-input hover:bg-accent',
                  // An empty bucket is dimmed rather than hidden: a broker
                  // should be able to see that Overdue is zero, which is the
                  // whole reassurance the page offers.
                  !active && n === 0 && 'text-muted-foreground',
                )}
              >
                {chip.label}
                <span className="ml-1.5 tabular-nums">{n}</span>
              </button>
            )
          })}
        </div>

        {rows === null ? (
          <div className="text-muted-foreground p-6 text-center text-sm">
            Loading…
          </div>
        ) : visible.length === 0 ? (
          /* NOT an empty table. "Nothing due" is a result worth stating —
             a broker who is clear should be told so, not left wondering
             whether the page failed to load. */
          <div className="rounded-md border border-dashed p-8 text-center">
            <p className="text-sm font-medium">
              {counts.all === 0 ? 'Nothing due — you are clear' : 'Nothing here'}
            </p>
            <p className="text-muted-foreground mt-1 text-xs">
              {counts.all === 0
                ? 'Follow-ups you set on a lead appear here, with renewals the system spots for you.'
                : 'Try another filter.'}
            </p>
          </div>
        ) : (
          <div className="border-input overflow-hidden rounded-md border">
            <table className="w-full text-xs">
              <thead className="bg-muted/60 text-muted-foreground text-2xs">
                <tr className="[&>th]:px-3 [&>th]:py-1.5 [&>th]:text-left [&>th]:font-semibold [&>th]:tracking-wide [&>th]:uppercase">
                  <th className="w-28">Due</th>
                  <th>Customer</th>
                  <th className="w-28">Stage</th>
                  <th className="w-20">Renewal</th>
                  <th className="w-20">Kind</th>
                  <th>Note</th>
                  <th className="w-32" />
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => {
                  const bucket = bucketOf(r.due_at, today)
                  const overdue = bucket === 'overdue'
                  return (
                    <tr
                      key={r.id}
                      className={cn(
                        'border-input border-t align-middle [&>td]:px-3 [&>td]:py-2',
                        busy === r.id && 'opacity-50',
                      )}
                    >
                      {/* OVERDUE IS STATED, not left to be worked out from a
                          date — a left rule and a relative age, so a list read
                          at a glance sorts itself. */}
                      <td
                        className={cn(
                          'border-l-2 whitespace-nowrap tabular-nums',
                          overdue
                            ? 'border-l-error text-error font-medium'
                            : 'border-l-transparent',
                        )}
                      >
                        {relativeDue(r.due_at, today)}
                      </td>

                      <td className="min-w-0">
                        {/* Leads has no ?lead=<id> param — selectedId is local
                            state — so the phone doubles as the search that
                            opens the right row. A real deep link is worth
                            adding separately. */}
                        <Link
                          to={`/leads?q=${encodeURIComponent(r.lead?.phone ?? r.lead?.name ?? '')}`}
                          className="hover:text-foreground font-medium hover:underline"
                        >
                          {r.lead?.name ?? 'Unknown'}
                        </Link>
                        {r.lead?.phone && (
                          <a
                            href={`tel:${r.lead.phone}`}
                            className="text-muted-foreground hover:text-foreground ml-2 tabular-nums"
                          >
                            {r.lead.phone}
                          </a>
                        )}
                      </td>

                      <td>
                        {r.lead?.stage ? (
                          <LeadStageBadge stage={r.lead.stage} />
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>

                      <td className="text-muted-foreground whitespace-nowrap tabular-nums">
                        {shortDate(r.lead?.renewal_date ?? null)}
                      </td>

                      <td>
                        <span
                          className={cn(
                            'rounded px-1.5 py-0.5 text-2xs font-medium',
                            // A renewal is an insurer's deadline that cannot
                            // be moved — miss it and the customer auto-renewed
                            // elsewhere for a year. It must not look like an
                            // ordinary callback.
                            r.kind === 'Renewal'
                              ? 'text-warning bg-warning-muted'
                              : 'text-muted-foreground bg-muted',
                          )}
                        >
                          {r.kind}
                        </span>
                      </td>

                      <td className="min-w-0">
                        <span className="line-clamp-2">{r.note}</span>
                        {r.attempt_count > 0 && (
                          <span className="text-muted-foreground ml-1 text-2xs">
                            · chased {r.attempt_count}x
                          </span>
                        )}
                      </td>

                      <td>
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={busy === r.id}
                            onClick={() => act(r, 'complete')}
                            className="h-6 px-2 text-2xs"
                          >
                            Done
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={busy === r.id}
                            onClick={() => act(r, 'snooze')}
                            className="h-6 px-2 text-2xs"
                            title="Push to tomorrow"
                          >
                            Snooze 24h
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={busy === r.id}
                            onClick={() => act(r, 'dismiss')}
                            className="text-muted-foreground h-6 px-2 text-2xs"
                            title="No longer relevant"
                          >
                            Dismiss
                          </Button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  )
}
