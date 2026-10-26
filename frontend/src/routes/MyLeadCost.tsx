import { useEffect, useMemo, useState } from 'react'
import { Download, Lock } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { getMyLeadCost, type Ledger } from '@/data/api'

// The broker's own view of what they were charged for leads.
//
// THE SAME LEDGER THE ADMIN SEES, deliberately: a broker querying a figure and
// the accounts person answering should be looking at identical rows in the
// same order. Two renderings of one statement is how "your screen says
// something different from mine" starts.
//
// OPEN MONTHS INCLUDED. This used to show closed months only — the backend
// 404'd anything still being adjusted, so a broker given ten leads today
// learned what they owed after the 5th of NEXT month. The lines were never
// provisional: cost_at_delivery is stamped as the delivery happens. What can
// still move is which lines carry a credit, so the screen shows the figure and
// withholds the word "final" rather than withholding the figure.
//
// No adjusting, and no other brokers: a broker cannot reduce their own charge,
// and this is their statement rather than a comparison.

function money(value: string | number) {
  return Number(value).toLocaleString('en-CA', { style: 'currency', currency: 'CAD' })
}

function shortDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-CA', { day: 'numeric', month: 'short' })
}

/** The last seven months, newest first, current included.
 *
 *  NO SKIP. The old version stepped over the current month AND, before the
 *  6th, the one just gone — because both 404'd. Every month here answers now,
 *  and `ledger.is_open` says which are still moving, so skipping would only
 *  hide a month a broker can read. */
function recentMonths(since: string | null): { value: string; label: string }[] {
  const now = new Date()
  const out: { value: string; label: string }[] = []
  for (let i = 0; i < 7; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    // STOPS AT THE JOIN MONTH. A broker onboarded in August has no May, and
    // offering one means offering a statement that reads $0 forever — which
    // looks like a bill they were spared rather than a month that never
    // existed. String compare is safe on YYYY-MM and needs no Date parsing.
    if (since && value < since) break
    out.push({ value, label: d.toLocaleString('en-CA', { month: 'long', year: 'numeric' }) })
  }
  return out
}

export default function MyLeadCost() {
  // ITS OWN STATE, not read off `ledger`. The effect clears `ledger` on every
  // month change, so a cutoff derived from it would vanish mid-switch and the
  // list would flick back to seven entries. Set once, then kept.
  const [since, setSince] = useState<string | null>(null)
  const months = useMemo(() => recentMonths(since), [since])
  const [month, setMonth] = useState(months[0].value)
  const [ledger, setLedger] = useState<Ledger | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setFailed(false)
    // CLEARED, not left in place. Without this the previous month's total and
    // badge sit under the new month's label until the request lands — one
    // month's money shown as another's.
    setLedger(null)
    getMyLeadCost(month)
      .then((l) => {
        if (cancelled) return
        setLedger(l)
        setSince(l.broker_since_month)
      })
      .catch(() => {
        if (cancelled) return
        setLedger(null)
        setFailed(true)
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [month])

  // THREE STATES, NOT TWO. `ledger` is null while loading and after a failure,
  // so `!ledger?.is_open` would read "not loaded" as "closed" and flash Final,
  // the finality wording and a PDF over a month that may well be open.
  const closed = ledger?.is_open === false
  const open = ledger?.is_open === true

  const label = months.find((m) => m.value === month)?.label ?? month
  const credited = ledger?.lines.filter((l) => l.credit_reason).length ?? 0

  return (
    <AppShell section="Lead cost">
      <div className="mx-auto max-w-4xl space-y-4 p-6">
        <div className="flex flex-wrap items-center gap-2 text-sm print:hidden">
          <span className="text-muted-foreground">Month</span>
          <select
            value={month}
            onChange={(e) => {
              // Cleared HERE as well as in the effect: the effect runs after
              // the render that follows this change, which is one paint with
              // the new month's label above the old month's figures.
              setLedger(null)
              setMonth(e.target.value)
            }}
            className="border-input rounded border bg-transparent px-2 py-1 outline-none"
          >
            {months.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>

          {/* CLOSED ONLY. On an open month this would be a claim about the
              money that is not yet true; while loading, one we cannot make. */}
          {closed && (
            <span className="bg-muted text-muted-foreground ml-auto inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium">
              <Lock className="size-3" /> Final
            </span>
          )}
          {open && (
            <span className="bg-success/10 text-success ml-auto rounded px-2 py-0.5 text-xs font-medium">
              Still open
            </span>
          )}
        </div>

        <div className="bg-card overflow-x-auto rounded border">
          {/* Letterhead, printed only — a broker saving this for their own
              records should have a document, not a screenshot. */}
          <div className="hidden border-b px-4 py-4 print:block">
            <div className="text-lg font-semibold">PolicyScanner Brokerage</div>
            <div className="text-muted-foreground text-xs">
              A division of Fairstar Insurance
            </div>
          </div>

          <div className="flex items-baseline justify-between gap-4 border-b px-4 py-3">
            <div>
              <div className="font-semibold">{label}</div>
              <div className="text-muted-foreground text-xs">
                {ledger ? `${ledger.lines.length} leads` : 'Lead cost statement'}
                {credited > 0 && ` · ${credited} not charged`}
              </div>
            </div>
            <div className="flex items-baseline gap-3">
              {/* AN EM DASH, NOT $0.00, until the figure is known. Zero is a
                  statement about money and reads as one — "you owe nothing"
                  is not what "still loading" means. */}
              <div className="text-lg font-semibold tabular-nums">
                {ledger ? money(ledger.charged) : '—'}
              </div>
              {/* CLOSED ONLY. A PDF is a document a broker keeps and compares
                  against the final bill; an open month's total is not that
                  yet, and saving one would manufacture the disagreement. */}
              {closed && (
                <Button
                  size="sm"
                  variant="outline"
                  className="print:hidden"
                  onClick={() => window.print()}
                >
                  <Download className="size-3.5" /> PDF
                </Button>
              )}
            </div>
          </div>

          {loading ? (
            <p className="text-muted-foreground p-8 text-center text-sm">Loading…</p>
          ) : failed ? (
            <p className="text-muted-foreground p-8 text-center text-sm">
              Could not load this month.
            </p>
          ) : !ledger?.lines.length ? (
            <p className="text-muted-foreground p-8 text-center text-sm">
              You were not charged for any leads in {label}.
            </p>
          ) : (
            <table className="w-full min-w-[36rem] border-collapse text-sm">
              <thead>
                <tr className="bg-secondary/60 text-muted-foreground border-b text-left text-[0.7rem] tracking-wide uppercase">
                  <Th>#</Th>
                  <Th>Customer</Th>
                  <Th>Phone</Th>
                  <Th>Received</Th>
                  <Th right>Not charged</Th>
                  <Th right>Charged</Th>
                </tr>
              </thead>
              <tbody>
                {/* Struck through, same as the admin ledger — a broker scanning
                    this sees at a glance which lines cost them nothing. */}
                {ledger.lines.map((line, i) => (
                  <tr
                    key={line.lead_id}
                    className={cn('border-b', line.credit_reason && 'text-muted-foreground')}
                  >
                    <Td muted className="text-xs tabular-nums">
                      {i + 1}
                    </Td>
                    <Td
                      className={cn(
                        !line.credit_reason && 'font-medium',
                        line.credit_reason && 'line-through',
                      )}
                    >
                      {line.name ?? <span className="text-muted-foreground">No name</span>}
                    </Td>
                    <Td
                      muted
                      className={cn('text-xs tabular-nums', line.credit_reason && 'line-through')}
                    >
                      {line.phone ?? '—'}
                    </Td>
                    <Td muted className={cn('text-xs', line.credit_reason && 'line-through')}>
                      {shortDate(line.delivered_at)}
                    </Td>
                    <Td right>
                      {line.credit_reason ? (
                        <span className="text-destructive text-xs">{line.credit_reason}</span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </Td>
                    <Td right className={cn(!line.credit_reason && 'font-medium')}>
                      {line.credit_reason ? (
                        <span className="text-muted-foreground">{money(0)}</span>
                      ) : (
                        money(line.cost)
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>

              <tfoot>
                <tr className="text-muted-foreground border-t">
                  <Td colSpan={4} className="py-2 text-xs">
                    {ledger.lines.length} leads allocated
                  </Td>
                  <Td right className="text-destructive py-2">
                    {Number(ledger.credited) > 0 ? `−${money(ledger.credited)}` : '—'}
                  </Td>
                  <Td right className="py-2">
                    {money(ledger.allocated)}
                  </Td>
                </tr>
                <tr className="bg-secondary/60 border-t-2 font-semibold">
                  <Td colSpan={5} className="py-3">
                    Charged for {label}
                  </Td>
                  <Td right className="py-3 text-base">
                    {money(ledger.charged)}
                  </Td>
                </tr>
              </tfoot>
            </table>
          )}
        </div>

        {/* Where to go when the number looks wrong. Without this the screen
            states a charge and offers no way to question it. */}
        <p className="text-muted-foreground text-xs print:hidden">
          Leads you entered yourself are never charged. If something here looks
          wrong, speak to your admin
          {/* THE SECOND HALF DEPENDS ON THE MONTH. Saying a figure is final
              while its credits can still move is the claim the old
              closed-months-only rule existed to avoid — the fix is to stop
              making it on an open month, not to hide the month. Neither
              sentence is offered while loading or after a failure, when we do
              not know which is true. */}
          {closed && ' — this statement is final and cannot be changed'}
          {open &&
            ' — this is the running total for a month that is still open, so' +
              ' credits and reassignments can still change it until it closes' +
              ' after the 5th'}
          .
        </p>

        <p className="text-muted-foreground hidden pt-2 text-xs print:block">
          Generated {new Date().toLocaleDateString('en-CA', { dateStyle: 'long' })} ·
          PolicyScanner Brokerage, a division of Fairstar Insurance
        </p>
      </div>
    </AppShell>
  )
}

function Th({ children, right }: { children?: React.ReactNode; right?: boolean }) {
  return <th className={cn('px-4 py-2.5 font-semibold', right && 'text-right')}>{children}</th>
}

function Td({
  children,
  right,
  muted,
  className,
  colSpan,
}: {
  children?: React.ReactNode
  right?: boolean
  muted?: boolean
  className?: string
  colSpan?: number
}) {
  return (
    <td
      colSpan={colSpan}
      className={cn(
        'px-4 py-2.5',
        right && 'text-right tabular-nums',
        muted && 'text-muted-foreground',
        className,
      )}
    >
      {children}
    </td>
  )
}
