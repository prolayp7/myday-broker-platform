import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, AlertTriangle, RefreshCw, CheckCircle2 } from 'lucide-react'
import { toast } from 'sonner'
import {
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import { AppShell } from '@/components/AppShell'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  getMetaSignals,
  getMetaSignalSummary,
  getMetaHealth,
  getMetaSendQuality,
  getMetaReceived,
  retryMetaSignal,
  retryDeadTokenMetaSignals,
  firstValidationMessage,
  type MetaSignalRow,
  type MetaSignalSummary,
  type MetaHealth,
  type MetaSendQuality,
  type MetaReceived,
} from '@/data/api'

// Admin › Meta Conversions.
//
// WHY IT EXISTS. A failure had nowhere to surface. Meta returns HTTP 400 for
// an EXPIRED ACCESS TOKEN, every 400 is treated as permanent, and a Failed row
// is never picked up by the drain again — so a token expiring overnight lost
// every conversion queued in that window, silently. One screen, read at a
// glance: how many fired, how many got there, and whether Meta is refusing us.
//
// The DAILY CHART is what makes a token expiry obvious. A wall of red starting
// on one day looks nothing like the occasional single failure, and no list
// shows that as fast.

/** Meta's code for an expired or revoked access token — the one failure with a
 *  known fix, and the one that hits many rows at once. */
const TOKEN_ERROR = '190'

const EVENT_COLOR: Record<string, string> = {
  Lead: '#6366f1',
  CompleteRegistration: '#0ea5e9',
  Purchase: '#10b981',
}

/** 'CompleteRegistration' is Meta's name for it; brokers call it a quote.
 *  Purchase is a sale, carrying the yearly premium. */
const EVENT_LABEL: Record<string, string> = {
  Lead: 'Lead',
  CompleteRegistration: 'Quote done',
  Purchase: 'Purchase',
}

/** Presets, not a calendar. "The last fortnight" is one click, and a date
 *  picker for a range nobody asks for is a component to maintain for nothing. */
const RANGES = [
  { days: 7, label: '7 days' },
  { days: 14, label: '14 days' },
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
]

/** One row of field chips — used twice per event.
 *
 *  TWO MODES, because the two sides are not the same kind of number.
 *
 *  valueFor -> OUR coverage: how many of these events carried the field, out
 *  of how many we sent. A real fraction, so it is coloured: on every event is
 *  working, on none is the finding.
 *
 *  countFor -> META'S count: how many times the key was RECEIVED. Meta counts
 *  key occurrences, and we send em/ph as arrays (the lead's contact plus the
 *  applicant's), so one event can contribute more than one. It is not a
 *  fraction of anything we hold — shown as a bare count, uncoloured, because
 *  a colour scale would imply coverage it cannot express.
 *
 *  DRIVEN BY THE SHARED FIELD LIST so the two rows line up: we and Meta name
 *  five of these differently (em/email, ph/phone, db/dob, ge/gen, zp/zip).
 */
type FieldLabel = { field: string; label: string; meta_key: string }

function ChipRow({
  label,
  fields,
  valueFor,
  countFor,
}: {
  label: string
  fields: FieldLabel[]
  valueFor?: (f: FieldLabel) => { sent: number; total: number } | undefined
  countFor?: (f: FieldLabel) => number | undefined
}) {
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
      <span className="text-muted-foreground w-24 shrink-0 text-xs">{label}</span>
      {fields.map((f) => {
        if (countFor) {
          const count = countFor(f)
          if (count === undefined) return null
          return (
            <span
              key={f.field}
              title={`${f.label}: ${count} received — key occurrences, not confirmed matches`}
              className="bg-muted text-muted-foreground inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs tabular-nums"
            >
              {f.label}
              <span className="text-foreground">{count}</span>
            </span>
          )
        }

        const value = valueFor?.(f)
        if (!value) return null
        const none = value.sent === 0
        const full = value.total > 0 && value.sent >= value.total
        return (
          <span
            key={f.field}
            title={`${f.label}: ${value.sent} of ${value.total}`}
            className={cn(
              'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs tabular-nums',
              none
                ? 'bg-destructive/10 text-destructive'
                : full
                  ? 'bg-emerald-500/10 text-emerald-600'
                  : 'bg-amber-500/10 text-amber-600',
            )}
          >
            {f.label}
            <span className="opacity-70">
              {value.sent}/{value.total}
            </span>
          </span>
        )
      })}
    </div>
  )
}

export default function CapiDashboard() {
  const [summary, setSummary] = useState<MetaSignalSummary | null>(null)
  const [failures, setFailures] = useState<MetaSignalRow[]>([])
  const [health, setHealth] = useState<MetaHealth | null>(null)
  const [quality, setQuality] = useState<MetaSendQuality | null>(null)
  // META'S SIDE, HELD SEPARATELY AND FETCHED SEPARATELY. Everything else on
  // this page is one database read; this one calls Meta three times at ten
  // seconds each. Inside the Promise.all below it could hold the whole
  // dashboard for half a minute on a Meta outage, so it gets its own effect
  // and its own state: local figures render the moment they arrive.
  const [received, setReceived] = useState<MetaReceived | null>(null)
  const [receivedLoading, setReceivedLoading] = useState(true)
  const [days, setDays] = useState(14)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      getMetaSignalSummary(days),
      getMetaSignals({ status: 'Failed', limit: 50 }),
      // Separately caught: a health check that cannot reach Meta must not
      // blank the figures, which are read straight from our own database and
      // are still true.
      getMetaHealth().catch(() => null),
      // Same treatment: this reads our own database, but it is a new endpoint
      // and the rest of the page must survive it failing.
      getMetaSendQuality(days).catch(() => null),
    ])
      .then(([s, f, h, q]) => {
        setSummary(s)
        setFailures(f.rows)
        setHealth(h)
        setQuality(q)
      })
      .catch(() => toast.error('Could not load the conversion figures'))
      .finally(() => setLoading(false))
  }, [days])

  useEffect(load, [load])

  // Deliberately NOT part of `load`. Same `days` so both halves describe the
  // same window, but a failure here leaves the rest of the page untouched —
  // and `received` staying null renders as "unavailable" rather than as
  // zeros, which would read as "nothing arrived".
  useEffect(() => {
    // GUARDED AGAINST AN OUT-OF-ORDER REPLY. This call can take 30 seconds
    // when Meta is slow, so switching 7 -> 90 -> 14 can land the older
    // response last and leave the panel showing a window nobody selected.
    // The flag makes every superseded request a no-op.
    let active = true
    setReceivedLoading(true)
    getMetaReceived(days)
      .then((r) => active && setReceived(r))
      .catch(() => active && setReceived(null))
      .finally(() => active && setReceivedLoading(false))
    return () => {
      active = false
    }
  }, [days])

  const tokenFailures = failures.filter((r) => String(r.error_code) === TOKEN_ERROR)

  async function retryOne(id: string) {
    setBusy(id)
    try {
      await retryMetaSignal(id)
      toast.success('Queued — the next send picks it up')
      load()
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not retry that one')
    } finally {
      setBusy(null)
    }
  }

  /** ONE CALL, not one per row.
   *
   *  This used to fan out a request per failure with Promise.allSettled, on the
   *  reasoning that the same guard applied to each and there was nothing extra
   *  to build. That held while the only endpoint was the single-row retry — but
   *  a token dying overnight fails every conversion queued behind it, and fifty
   *  parallel POSTs to fix one credential is a lot of requests to express one
   *  intention.
   *
   *  The backend now re-queues them in a single statement scoped to code 190,
   *  which is also narrower than this was: it can only touch dead-token
   *  failures, where the fan-out sent whatever happened to be in
   *  `tokenFailures` at render time. */
  async function retryAll() {
    setBusy('all')
    try {
      const { requeued } = await retryDeadTokenMetaSignals()
      toast.success(
        requeued === 0
          ? 'Nothing left to retry — they were already queued'
          : `${requeued} queued — the next send picks them up`,
      )
      load()
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not retry those')
    } finally {
      setBusy(null)
    }
  }

  const tiles = [
    { label: 'Fired', value: summary?.total ?? 0, tone: '' },
    { label: 'Reached Meta', value: summary?.by_status.Sent ?? 0, tone: 'text-emerald-600' },
    { label: 'Failed', value: summary?.by_status.Failed ?? 0, tone: 'text-destructive' },
    { label: 'Waiting', value: summary?.by_status.Pending ?? 0, tone: 'text-amber-600' },
  ]

  const pieData = (summary?.by_event ?? []).map((e) => ({
    name: EVENT_LABEL[e.event] ?? e.event,
    value: e.count,
    key: e.event,
  }))

  return (
    <AppShell section="Admin">
      <div className="mx-auto max-w-5xl space-y-6 p-6">
        <div>
          <Link
            to="/admin"
            className="text-muted-foreground hover:text-foreground mb-3 inline-flex items-center gap-1.5 text-sm"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Admin
          </Link>
          <h1 className="text-2xl font-semibold">Meta Conversions</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            What we have told Meta, and whether it got there.
          </p>
        </div>

        {/* THE STATUS STRIP. Asked of Meta directly, so a revoked or
            misconfigured token shows here BEFORE a conversion fails because of
            it — every other warning on this screen is news about a lead that
            already went unreported. */}
        {health && (
          <div
            className={
              health.token.ok
                ? 'flex flex-wrap items-center gap-x-6 gap-y-1 rounded-lg border p-3 text-sm'
                : 'border-destructive/40 bg-destructive/5 flex flex-wrap items-center gap-x-6 gap-y-1 rounded-lg border p-3 text-sm'
            }
          >
            <span className="flex items-center gap-2 font-medium">
              {health.token.ok ? (
                <>
                  <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                  Meta connection healthy
                </>
              ) : (
                <>
                  <AlertTriangle className="text-destructive h-4 w-4" />
                  {health.token.reason ?? 'Meta is not accepting us'}
                </>
              )}
            </span>
            {health.token.ok && (
              <>
                <span className="text-muted-foreground">
                  Token:{' '}
                  {health.token.expires_at
                    ? `expires ${new Date(health.token.expires_at * 1000).toLocaleDateString()}`
                    : 'no expiry'}
                </span>
                <span className="text-muted-foreground">
                  Dataset {health.token.dataset_id}
                </span>
              </>
            )}
            {health.stuck_count > 0 && (
              <span className="text-amber-600">
                {health.stuck_count} waiting over an hour — the sender may be stopped
              </span>
            )}
          </div>
        )}

        {/* Only for the token error: it is the one failure with a known fix,
            and it hits every row queued while the token was dead. Naming the
            fix beats repeating the same error text on twenty rows. */}
        {tokenFailures.length > 0 && (
          <div className="border-destructive/40 bg-destructive/5 flex items-start gap-3 rounded-lg border p-3">
            <AlertTriangle className="text-destructive mt-0.5 h-5 w-5 shrink-0" />
            <div className="flex-1">
              <p className="font-medium">
                The Meta access token has expired — {tokenFailures.length}{' '}
                {tokenFailures.length === 1 ? 'conversion' : 'conversions'} did not send.
              </p>
              <p className="text-muted-foreground mt-1 text-sm">
                Nothing is wrong with these events. Replace the token, then retry — they
                keep their original ids, so Meta cannot count any of them twice.
              </p>
            </div>
            <Button size="sm" disabled={busy !== null} onClick={retryAll} className="shrink-0">
              Retry all
            </Button>
          </div>
        )}

        <div className="flex items-center gap-2">
          {RANGES.map((r) => (
            <button
              key={r.days}
              onClick={() => setDays(r.days)}
              className={
                days === r.days
                  ? 'bg-primary text-primary-foreground rounded-md px-3 py-1.5 text-sm font-medium'
                  : 'text-muted-foreground hover:text-foreground rounded-md px-3 py-1.5 text-sm'
              }
            >
              {r.label}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {tiles.map((t) => (
            <div key={t.label} className="rounded-lg border p-4">
              <div className={`text-2xl font-semibold ${t.tone}`}>{t.value}</div>
              <div className="text-muted-foreground mt-0.5 text-xs">{t.label}</div>
            </div>
          ))}
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-lg border p-4">
            <h2 className="text-sm font-semibold">What fired</h2>
            <p className="text-muted-foreground mt-0.5 mb-2 text-xs">
              No Sold events means no sales are being reported, whatever the totals say.
            </p>
            {pieData.length === 0 ? (
              <p className="text-muted-foreground py-12 text-center text-sm">Nothing yet.</p>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie data={pieData} dataKey="value" nameKey="name" outerRadius={70} label>
                    {pieData.map((d) => (
                      <Cell key={d.key} fill={EVENT_COLOR[d.key] ?? '#94a3b8'} />
                    ))}
                  </Pie>
                  <Tooltip />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            )}
          </div>

          <div className="rounded-lg border p-4">
            <h2 className="text-sm font-semibold">Day by day</h2>
            <p className="text-muted-foreground mt-0.5 mb-2 text-xs">
              A block of red starting on one day is a Meta-side problem, not bad luck.
            </p>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={summary?.series ?? []}>
                {/* Day and month only — the year is noise on a 14-day window. */}
                <XAxis
                  dataKey="day"
                  tickFormatter={(d: string) => d.slice(5)}
                  fontSize={10}
                  tickLine={false}
                />
                <YAxis fontSize={10} tickLine={false} allowDecimals={false} width={24} />
                <Tooltip />
                <Legend />
                {/* Stacked, so the height is the day's total volume and the red
                    portion is what did not arrive. */}
                <Bar dataKey="sent" stackId="a" fill="#10b981" name="Sent" />
                <Bar dataKey="failed" stackId="a" fill="#ef4444" name="Failed" />
                <Bar dataKey="pending" stackId="a" fill="#f59e0b" name="Waiting" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* WHAT WE SENT — the matching fields each event actually carried.
            Meta matches a conversion to a person using these, and an event it
            cannot match is one it cannot attribute or optimise on.

            PER EVENT TYPE, never combined: a Lead fires when the lead row is
            created and is legitimately thin, while a CompleteRegistration
            fires at Quote Done with the applicant filled in. One average
            across both would describe neither.

            NOT a match score. Meta does not expose Event Match Quality for
            conversion-leads integrations (their docs name it as an
            exception), and inventing a local substitute would be a number
            with no meaning behind it. This says only what we sent. */}
        {quality && quality.events.length > 0 && (
          <div className="rounded-lg border">
            <div className="flex items-center justify-between border-b px-4 py-3">
              <h2 className="text-sm font-semibold">
                Matching data we sent
                <span className="text-muted-foreground ml-1.5 font-normal">· Auto Insurance</span>
              </h2>
              {/* WHICH DATASET IT WENT TO — the first thing to check when
                  Events Manager disagrees with this page. "not recorded" is
                  every row sent before the id was stored, and is a real
                  state rather than a mismatch. */}
              <span className="text-muted-foreground text-xs">
                {Object.entries(quality.datasets)
                  .map(([id, n]) => `Dataset: ${id} (${n})`)
                  .join(' · ')}
                {/* META'S OWN "last received". This single fact is what the
                    whole investigation started over — Events Manager appeared
                    to show 12 days of silence — and establishing it took an
                    hour of ad-hoc queries. It costs one field on a call we
                    already make. */}
                {received?.available && received.last_fired_time && (
                  <> · Meta last received {new Date(received.last_fired_time).toLocaleString()}</>
                )}
              </span>
            </div>

            <div className="divide-y">
              {quality.events.map((event) => (
                <div key={event.event_name} className="px-4 py-3">
                  <div className="mb-2 flex items-baseline justify-between">
                    <h3 className="text-sm font-medium">
                      {EVENT_LABEL[event.event_name] ?? event.event_name}
                    </h3>
                    <span className="text-muted-foreground text-xs tabular-nums">
                      {event.total} sent · {event.average_fields} fields each
                      {/* Meta's own count of the same event, shown BESIDE ours
                          and never subtracted from it. The two windows do not
                          share timestamp semantics — Meta's also includes test
                          sends and events from before the dataset switch — so
                          a difference is expected and a delta would imply a
                          precision we do not have. */}
                      {received?.available && received.totals?.[event.event_name] !== undefined && (
                        <> · Meta counted {received.totals[event.event_name]}</>
                      )}
                    </span>
                  </div>

                  {/* WHAT WE SENT. */}
                  <ChipRow
                    label="We sent"
                    fields={quality.fields}
                    valueFor={(f) => event.coverage[f.field]}
                  />

                  {/* WHAT META RECEIVED — the independent half. Meta names
                      five of these fields differently (em/email, ph/phone,
                      db/dob, ge/gen, zp/zip), so both rows are keyed through
                      the same label table and line up. */}
                  {receivedLoading ? (
                    <p className="text-muted-foreground mt-1.5 text-xs">
                      Asking Meta…
                    </p>
                  ) : received?.available && received.match_keys?.[event.event_name] ? (
                    <ChipRow
                      label="Meta received"
                      fields={quality.fields}
                      // COUNTS, NOT COVERAGE. Meta's match_keys counts key
                      // OCCURRENCES, not events carrying a key — and we send
                      // em/ph as arrays (the lead's contact plus the
                      // applicant's, deduplicated after hashing), so one event
                      // can contribute two. Live proof: Meta reports 41 Lead
                      // events and email 52. Dividing gave "52/41" rendered
                      // green, a fraction over 100% that means nothing.
                      //
                      // So no denominator and no green/amber/red here: those
                      // read as coverage, which this is not.
                      countFor={(f) => received.match_keys?.[event.event_name]?.[f.meta_key]}
                    />
                  ) : (
                    // SHOWN, never silently hidden: "we could not ask" and
                    // "nothing arrived" look identical as an absent row, and
                    // they mean opposite things.
                    <p className="text-muted-foreground mt-1.5 text-xs">
                      Meta reporting unavailable
                    </p>
                  )}
                </div>
              ))}
            </div>

            {/* Meta's own words on an event it ACCEPTED. Grouped, because the
                same warning on forty events is one problem. */}
            {quality.warnings.length > 0 && (
              <div className="border-t px-4 py-3">
                <h3 className="mb-1.5 text-xs font-medium">Meta's warnings</h3>
                {quality.warnings.map((w) => (
                  <p key={w.message} className="text-muted-foreground text-xs">
                    {w.message} <span className="tabular-nums">({w.count})</span>
                  </p>
                ))}
              </div>
            )}

            {/* Said plainly rather than left implied. Meta does not tell
                anyone whether one named customer matched — only aggregate
                quality by event type, and not at all for this integration. */}
            {/* WHAT THIS PANEL MAY AND MAY NOT CLAIM. It confirms three
                things: we sent the event, Meta counted it, and Meta received
                particular identifiers. It does NOT confirm that any specific
                customer matched a Meta account — "Meta received" means the
                identifier arrived, never that it found a person. Meta does not
                expose match results for conversion-leads integrations at all,
                which their documentation states and a controlled test against
                several datasets confirmed. */}
            <p className="text-muted-foreground border-t px-4 py-2 text-xs">
              Individual match result unavailable — Meta does not report event
              match quality for CRM integrations. These counts are identifiers
              sent and received, not confirmed matches.
            </p>
          </div>
        )}

        <div className="rounded-lg border">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <h2 className="text-sm font-semibold">Problems</h2>
            <span className="text-muted-foreground text-xs">
              {failures.length} failed
            </span>
          </div>
          {loading ? (
            <p className="text-muted-foreground p-6 text-center text-sm">Loading…</p>
          ) : failures.length === 0 ? (
            <p className="text-muted-foreground p-8 text-center text-sm">
              Nothing needs attention — every conversion reached Meta.
            </p>
          ) : (
            failures.map((r) => (
              <div
                key={r.id}
                className="flex items-start justify-between gap-4 border-b px-4 py-3 last:border-b-0"
              >
                <div className="min-w-0">
                  <div className="text-sm">
                    <span className="font-medium">
                      {EVENT_LABEL[r.event_name] ?? r.event_name}
                    </span>
                    <span className="text-muted-foreground">
                      {' · '}
                      {r.lead?.name ?? 'deleted lead'}
                    </span>
                  </div>
                  {/* Meta's own words, on the row rather than in a log nobody
                      reads. The attempt count matters: three failures is a
                      different problem from one. */}
                  <p className="text-muted-foreground mt-0.5 text-xs">
                    {r.error_code != null && <span>code {r.error_code} · </span>}
                    {r.error_message}
                    {r.attempt_count > 1 && <span> · {r.attempt_count} attempts</span>}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy !== null}
                  onClick={() => retryOne(r.id)}
                  className="shrink-0"
                >
                  <RefreshCw className="mr-1.5 h-3 w-3" />
                  Retry
                </Button>
              </div>
            ))
          )}
        </div>
      </div>
    </AppShell>
  )
}
