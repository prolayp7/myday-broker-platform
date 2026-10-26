import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { CountTile } from '@/components/CountTile'
import { getMyDay, type MyDay as MyDayResponse } from '@/data/api'
import { LEAD_STAGES } from '@/domain/leadStages'

// The broker's landing screen. Every figure here is REAL (GET /my-day) — it
// used to render a hardcoded mock (Sarah Mitchell, $11,640) on the retired
// 11-stage taxonomy, which meant every tile also navigated to a Leads filter
// that returned nothing.
//
// The "This month" cards — premium written and commission earned — follow a
// month picker, so a broker can look back at any month they have worked.
// Overdue/due-today follow-ups are still an inert note: nothing here reads
// them yet, and a tile reading 0 would be read as "you have none" when the
// truth is "nothing tracks them" — the one number worse than no number.

/**
 * Money arrives as a string and is parsed here. Whole dollars — a premium
 * total is read at a glance on a landing page, and the cents are noise at
 * that size. An unparseable figure reads as $0 rather than "$NaN".
 */
function money(s: string) {
  const n = parseFloat(s)
  return (Number.isFinite(n) ? n : 0).toLocaleString('en-CA', {
    style: 'currency',
    currency: 'CAD',
    maximumFractionDigits: 0,
  })
}

function greeting(name: string) {
  const h = new Date().getHours()
  const g = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
  // First name only — the whole line is a greeting, not an identification.
  return `${g}, ${name.split(' ')[0]}`
}

// Same interval and reasoning as Leads.tsx's live inbox poll: leads arrive on
// Meta's own schedule, and this screen's whole job is showing today's counts
// the moment a broker opens it — a stale figure here is worse than on the
// list, since it is the first thing checked. Paused while the tab is hidden,
// resumed with an immediate refetch on return.
const POLL_INTERVAL_MS = 20_000

/** '2026-10' for a date, in the browser's local calendar (the broker's). */
function monthKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

/** '2026-10' -> 'October 2026'. */
function monthLabel(key: string): string {
  const [y, m] = key.split('-').map(Number)
  return new Date(y, m - 1, 1).toLocaleDateString('en-CA', { month: 'long', year: 'numeric' })
}

/** The month `delta` months from `key`. */
function shiftMonth(key: string, delta: number): string {
  const [y, m] = key.split('-').map(Number)
  return monthKey(new Date(y, m - 1 + delta, 1))
}

/** How far back the month dropdown reaches. The arrows go further. */
const MONTHS_LISTED = 24

export default function MyDay() {
  const navigate = useNavigate()
  // The month the premium and commission cards describe. In the URL, so a
  // refresh keeps it; absent means the current month. Never past it.
  const [params, setParams] = useSearchParams()
  const currentMonth = monthKey(new Date())
  const asked = params.get('month')
  const month = asked && /^\d{4}-\d{2}$/.test(asked) && asked <= currentMonth ? asked : currentMonth
  function chooseMonth(next: string) {
    const p = new URLSearchParams(params)
    if (next === currentMonth) p.delete('month')
    else p.set('month', next)
    setParams(p, { replace: true })
  }
  const [data, setData] = useState<MyDayResponse | null>(null)
  // A failed load is not an empty day. Without this the screen would show a
  // confident set of zeros to a broker whose connection blipped.
  const [failed, setFailed] = useState(false)

  // silent: a background poll tick must not clobber `data` back to null and
  // flash the skeleton — only the very first load (data still null) does that.
  const load = useCallback((opts?: { silent?: boolean }) => {
    return getMyDay(month === currentMonth ? undefined : month)
      .then((d) => {
        setData(d)
        setFailed(false)
      })
      .catch(() => {
        if (!opts?.silent) setFailed(true)
      })
  }, [month, currentMonth])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    function tick() {
      if (document.visibilityState !== 'visible') return
      load({ silent: true })
    }
    const id = setInterval(tick, POLL_INTERVAL_MS)

    // Returning to the tab should show current data immediately, not after
    // waiting out the remainder of an interval that ran while hidden.
    function onVisibility() {
      if (document.visibilityState === 'visible') tick()
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [load])

  if (failed) {
    return (
      <AppShell section="My Day">
        <div className="p-6 text-sm text-muted-foreground">
          Couldn’t load your dashboard.{' '}
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="text-info underline underline-offset-2"
          >
            Try again
          </button>
        </div>
      </AppShell>
    )
  }

  if (!data) {
    return (
      <AppShell section="My Day">
        <div className="p-6 text-muted-foreground">Loading…</div>
      </AppShell>
    )
  }

  // Stage keys carry spaces ('Quote Done'), so they must be encoded — Leads
  // reads ?stage= back through isLeadStage() and silently drops anything that
  // doesn't match a real stage exactly.
  const goStage = (key?: string) =>
    navigate(key ? `/leads?stage=${encodeURIComponent(key)}` : '/leads')

  return (
    <AppShell section="My Day">
      <div className="mx-auto max-w-7xl space-y-6 p-6">
        <h1 className="text-2xl font-semibold">{greeting(data.broker.name)}</h1>

        <section>
          <SectionTitle>Needs attention</SectionTitle>
          <div className="flex flex-wrap gap-3">
            <CountTile
              title="New today"
              subtitle="Received since midnight"
              count={data.attention.new_today}
              state="info"
              onClick={() => goStage('New')}
            />
            <CountTile
              title="Stale"
              subtitle="No movement in 5+ days"
              count={data.attention.stale}
              state={data.attention.stale > 0 ? 'warning' : 'neutral'}
              // No stale filter exists on Leads yet, so this opens the full
              // book rather than pretending to narrow it.
              onClick={() => goStage()}
            />
          </div>
        </section>

        <section>
          <SectionTitle>Pipeline</SectionTitle>
          {/* Driven by LEAD_STAGES, so a stage added in SQL and listed there
              appears here with no change to this file. `all` is the backend's
              own total, not a sum of the tiles — the two can differ if a lead
              holds a stage the frontend doesn't know about yet, and the
              server's count is the honest one. */}
          <div className="grid grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-3">
            <CountTile
              title="All"
              count={data.counts.all ?? 0}
              state="neutral"
              onClick={() => goStage()}
            />
            {LEAD_STAGES.map((s) => (
              <CountTile
                key={s.key}
                title={s.label}
                count={data.counts[s.key] ?? 0}
                state={s.state}
                onClick={() => goStage(s.key)}
              />
            ))}
          </div>
        </section>

        {/* What the carrier actually WROTE in the chosen month, by issued_at —
            the date the broker marked each policy Issued — and the broker's
            commission on those same policies. A policy sold today appears here
            when the carrier issues it, not when it is sold. */}
        <section>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-foreground text-sm font-semibold">
              {month === currentMonth ? 'This month' : monthLabel(month)}
            </h2>
            <div className="flex items-center gap-1">
              <button
                type="button"
                aria-label="Previous month"
                onClick={() => chooseMonth(shiftMonth(month, -1))}
                className="text-muted-foreground hover:text-foreground rounded-md p-1"
              >
                <ChevronLeft className="size-4" />
              </button>
              <select
                aria-label="Month"
                value={month}
                onChange={(e) => chooseMonth(e.target.value)}
                className="border-border bg-card rounded-md border px-2 py-1 text-sm"
              >
                {Array.from({ length: MONTHS_LISTED }, (_, i) => shiftMonth(currentMonth, -i))
                  .concat(month < shiftMonth(currentMonth, -(MONTHS_LISTED - 1)) ? [month] : [])
                  .map((m) => (
                    <option key={m} value={m}>
                      {monthLabel(m)}
                    </option>
                  ))}
              </select>
              <button
                type="button"
                aria-label="Next month"
                disabled={month >= currentMonth}
                onClick={() => chooseMonth(shiftMonth(month, 1))}
                className="text-muted-foreground hover:text-foreground rounded-md p-1 disabled:opacity-30"
              >
                <ChevronRight className="size-4" />
              </button>
            </div>
          </div>
          {/* Figures to read, not controls. A click would have to open a list,
              and the Issued list is "everything ever issued", not this month. */}
          <div className="flex flex-wrap gap-3">
            <MonthCard
              value={money(data.premium.written_this_month)}
              title="Premium written"
              count={data.premium.policy_count}
              month={month}
            />
            <MonthCard
              value={data.premium.commission_earned == null ? '—' : money(data.premium.commission_earned)}
              title="Commission earned"
              count={data.premium.policy_count}
              month={month}
            />
          </div>
        </section>

        <section>
          <SectionTitle>Not set up yet</SectionTitle>
          <div className="grid gap-3 sm:grid-cols-2">
            <NotWired
              title="Follow-ups"
              detail="Overdue and due-today counts exist on the backend but nothing reads them here yet."
            />
          </div>
        </section>
      </div>
    </AppShell>
  )
}

/** One figure for the chosen month. Both cards share it, so they always look
 *  alike and say the same thing when nothing was issued. */
function MonthCard({
  value,
  title,
  count,
  month,
}: {
  value: string
  title: string
  count: number
  month: string
}) {
  const monthName = monthLabel(month).split(' ')[0]
  return (
    <div className="border-border bg-card w-full max-w-xs rounded-lg border p-4">
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="text-sm font-medium">{title}</div>
      <div className="text-muted-foreground text-xs">
        {count === 0
          ? `Nothing issued in ${monthName}`
          : `${count} ${count === 1 ? 'policy' : 'policies'} issued`}
      </div>
    </div>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-2 text-sm font-semibold text-foreground">{children}</h2>
}

/**
 * A block the screen is designed to hold but has no data for.
 *
 * Deliberately NOT a CountTile and deliberately not clickable: it must not
 * read as a real figure of zero, and there is nowhere for it to go.
 */
function NotWired({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-md border border-dashed bg-muted/30 p-3">
      <div className="text-sm font-medium text-muted-foreground">{title}</div>
      <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>
    </div>
  )
}
