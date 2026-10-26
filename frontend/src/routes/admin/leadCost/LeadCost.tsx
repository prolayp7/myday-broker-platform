import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Download, FileText, Lock, Pencil, Plus, Search, X } from 'lucide-react'
import { toast } from 'sonner'
import { AppShell } from '@/components/AppShell'
import { useConfirm } from '@/components/ConfirmDialog'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  addLeadCostCredit,
  firstValidationMessage,
  getBrokerLedger,
  getBrokerGroups,
  getGroupCost,
  getLeadCostSettings,
  removeLeadCostCredit,
  saveLeadCostSettings,
  type BrokerGroup,
  type CreditReason,
  type GroupCost,
  type Ledger,
  type LedgerLine,
} from '@/data/api'

// What each broker owes for the leads the system gave them.
//
// A SHEET, NOT A STACK OF CARDS. The person using this works in spreadsheets:
// dense rows, aligned columns, totals in the footer, tabular numerals so
// figures line up on the decimal.
//
// THE FIVE-DAY WINDOW is half of what this screen does. A month is charged
// 1st–31st, adjusted until the 5th of the next month, and frozen after — so
// every control disappears once `is_open` comes back false. The server decides
// that, not the client: a browser with a wrong clock must not be able to edit
// a settled month.
//
// Deliberately NOT a checklist of every lead. The accounts person is removing
// the handful they were told about, so they search for those — and the search
// is over that broker's own ledger, so subtracting the wrong lead is
// impossible rather than caught afterwards.

const REASONS: CreditReason[] = ['Wrong number', 'Duplicate', 'Test lead', 'Other']

/** The last six months, newest first. Enough to answer "what did we bill in
 *  the spring?" without a date picker for a page that only ever needs months. */
function recentMonths(): { value: string; label: string }[] {
  const out: { value: string; label: string }[] = []
  const now = new Date()
  for (let i = 0; i < 6; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    out.push({
      value: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`,
      label: d.toLocaleString('en-CA', { month: 'long', year: 'numeric' }),
    })
  }
  return out
}

function money(value: string | number) {
  return Number(value).toLocaleString('en-CA', {
    style: 'currency',
    currency: 'CAD',
  })
}

function shortDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-CA', { day: 'numeric', month: 'short' })
}

export default function LeadCost() {
  const months = useMemo(recentMonths, [])

  const [groups, setGroups] = useState<BrokerGroup[]>([])
  const [groupId, setGroupId] = useState('')
  const [month, setMonth] = useState(months[0].value)
  // Null = the group sheet. A broker id = that broker's sheet.
  const [brokerId, setBrokerId] = useState<string | null>(null)
  const [view, setView] = useState<'adjust' | 'report'>('adjust')

  const [price, setPrice] = useState<string | null>(null)
  // THE DRAFT, held as a string. A number would coerce a half-typed "20." and
  // fight the admin mid-edit; this is the value in the box, not the price.
  const [priceDraft, setPriceDraft] = useState('')
  const [savingPrice, setSavingPrice] = useState(false)
  // READ-ONLY UNTIL ASKED FOR. The price every future delivery is billed at
  // should not sit in a live input on a screen an admin opens to read a
  // report — Edit is the first of the two steps, the confirm is the second.
  const [editingPrice, setEditingPrice] = useState(false)
  const confirm = useConfirm()
  const [summary, setSummary] = useState<GroupCost | null>(null)
  const [ledger, setLedger] = useState<Ledger | null>(null)
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    getBrokerGroups()
      .then((g) => {
        setGroups(g)
        if (g.length) setGroupId((current) => current || g[0].id)
      })
      .catch(() => toast.error('Could not load the broker groups'))
    getLeadCostSettings()
      .then((s) => {
        setPrice(s.cost_per_lead)
        setPriceDraft(s.cost_per_lead)
      })
      .catch(() => setPrice(null))
  }, [])

  const loadSummary = useCallback(async () => {
    if (!groupId) return
    setLoading(true)
    try {
      setSummary(await getGroupCost(groupId, month))
    } catch (err) {
      setSummary(null)
      toast.error(firstValidationMessage(err) ?? 'Could not load the month')
    } finally {
      setLoading(false)
    }
  }, [groupId, month])

  const loadLedger = useCallback(async () => {
    if (!brokerId) return
    setLoading(true)
    try {
      setLedger(await getBrokerLedger(brokerId, month))
    } catch (err) {
      setLedger(null)
      toast.error(firstValidationMessage(err) ?? 'Could not load that broker')
    } finally {
      setLoading(false)
    }
  }, [brokerId, month])

  useEffect(() => {
    if (brokerId) loadLedger()
    else loadSummary()
  }, [brokerId, loadLedger, loadSummary])

  // THE SERVER DECIDES whether the month is editable. A client-side date
  // comparison would let a machine with a wrong clock offer controls the
  // backend then refuses with a 409.
  const open = brokerId ? (ledger?.is_open ?? false) : (summary?.is_open ?? false)
  const sheet = open ? view : 'report'
  const brokerName =
    summary?.brokers.find((b) => b.broker_id === brokerId)?.name ?? 'Broker'

  const credited = ledger?.lines.filter((l) => l.credit_reason) ?? []

  // Scoped to this broker's own month — a lead from elsewhere is not findable
  // here at all.
  //
  // Depends on `ledger`, not on the derived `chargeable`: that array is rebuilt
  // every render, so memoising against it would recompute every time and the
  // memo would be decoration.
  const matches = useMemo(() => {
    const digits = query.replace(/\D/g, '')
    if (digits.length < 3) return []
    return (ledger?.lines ?? [])
      .filter((l) => !l.credit_reason)
      .filter((l) => (l.phone ?? '').replace(/\D/g, '').includes(digits))
  }, [query, ledger])

  /** Save the price the whole system bills at.
   *
   *  FUTURE DELIVERIES ONLY. cost_at_delivery is stamped as each delivery
   *  happens, so this cannot correct a bill already on screen — which is
   *  exactly why the control says so out loud.
   *
   *  Shows what the SERVER returned, not what was typed: the server is the
   *  authority on what was stored, and a rejected value must not linger in
   *  the box looking saved.
   */
  async function onSavePrice() {
    const next = priceDraft.trim()

    // THE SECOND STEP. Naming both figures is the point: an admin who mistyped
    // sees the number they are moving away from, which is the one thing that
    // makes a wrong new price obvious before it is live.
    const ok = await confirm({
      title: `Change the lead price to ${money(next)}?`,
      description:
        `Every lead delivered from now on is billed at ${money(next)} instead ` +
        `of ${money(price ?? 0)}. Leads already delivered keep the price they ` +
        `were given, so this does not change any bill you can currently see.`,
      confirmText: 'Change price',
      cancelText: 'Cancel',
    })
    if (!ok) return

    setSavingPrice(true)
    try {
      const saved = await saveLeadCostSettings(next)
      setPrice(saved.cost_per_lead)
      setPriceDraft(saved.cost_per_lead)
      setEditingPrice(false)
      toast.success(`Lead price is now ${money(saved.cost_per_lead)}`)
    } catch (err) {
      // The box stays open and keeps what they typed — a rejected price is
      // one to correct, not to retype from scratch.
      toast.error(firstValidationMessage(err) ?? 'Could not save the price')
    } finally {
      setSavingPrice(false)
    }
  }

  function onCancelPriceEdit() {
    setPriceDraft(price ?? '')
    setEditingPrice(false)
  }

  async function credit(line: LedgerLine, reason: CreditReason) {
    setBusy(true)
    try {
      await addLeadCostCredit(line.lead_id, reason)
      setQuery('')
      await loadLedger()
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not subtract that lead')
    } finally {
      setBusy(false)
    }
  }

  async function uncredit(line: LedgerLine) {
    setBusy(true)
    try {
      await removeLeadCostCredit(line.lead_id)
      await loadLedger()
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not put that lead back')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AppShell section="Admin · Lead cost">
      <div className="mx-auto max-w-5xl space-y-4 p-6">
        {brokerId ? (
          <button
            type="button"
            onClick={() => {
              setBrokerId(null)
              setQuery('')
            }}
            className="text-muted-foreground hover:text-foreground -ml-1 inline-flex items-center gap-1 text-sm print:hidden"
          >
            <ArrowLeft className="size-4" /> All brokers
          </button>
        ) : (
          <Link
            to="/admin"
            className="text-muted-foreground hover:text-foreground -ml-1 inline-flex items-center gap-1 text-sm print:hidden"
          >
            <ArrowLeft className="size-4" /> Admin
          </Link>
        )}

        {/* THE GROUP IS A DROPDOWN ONLY ON THE GROUP SHEET. Inside a broker it
            is fixed text: changing it there would silently move the accounts
            person onto another group while they are part-way through
            subtracting one person's leads. Leaving is the back link's job. */}
        <div className="flex flex-wrap items-center gap-2 text-sm print:hidden">
          <span className="text-muted-foreground">Group</span>
          {brokerId ? (
            <span className="font-medium">
              {groups.find((g) => g.id === groupId)?.name ?? '—'}
            </span>
          ) : (
            <select
              value={groupId}
              onChange={(e) => {
                setGroupId(e.target.value)
                setBrokerId(null)
              }}
              className="border-input rounded border bg-transparent px-2 py-1 outline-none"
            >
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.community?.name ? `${g.community.name} · ` : ''}
                  {g.name}
                </option>
              ))}
            </select>
          )}

          {brokerId && (
            <>
              <span className="text-muted-foreground">·</span>
              <span className="font-medium">{brokerName}</span>
              {open && (
                <Button
                  size="sm"
                  variant="outline"
                  className="ml-1"
                  onClick={() => setView(view === 'adjust' ? 'report' : 'adjust')}
                >
                  {view === 'adjust' ? (
                    <>
                      <FileText className="size-3.5" /> Report
                    </>
                  ) : (
                    <>
                      <Pencil className="size-3.5" /> Adjust
                    </>
                  )}
                </Button>
              )}
            </>
          )}

          <span className="text-muted-foreground ml-2">Month</span>
          <select
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="border-input rounded border bg-transparent px-2 py-1 outline-none"
          >
            {months.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>

          {/* THE PRICE. Read-only until Edit is pressed, then confirmed
              before it saves — two steps, because this is the number every
              future delivery is billed at and the screen it lives on is one
              an admin opens to READ a report.

              The endpoint and the API client existed from the start and
              nothing ever called them, so until now the only way to change it
              was to edit the row in Supabase. */}
          <div className="ml-auto flex items-center gap-2 text-xs">
            {editingPrice ? (
              <>
                <span className="text-muted-foreground">System leads $</span>
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={priceDraft}
                  onChange={(e) => setPriceDraft(e.target.value)}
                  disabled={savingPrice}
                  autoFocus
                  aria-label="Cost per system lead"
                  className="border-input w-20 rounded border bg-transparent px-2 py-1 text-right tabular-nums outline-none"
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    savingPrice || !priceDraft.trim() || priceDraft.trim() === price
                  }
                  onClick={onSavePrice}
                >
                  {savingPrice ? 'Saving…' : 'Save'}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={savingPrice}
                  onClick={onCancelPriceEdit}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <>
                <span className="text-muted-foreground">
                  {price ? `System leads ${money(price)} · manual leads free` : ''}
                </span>
                {price !== null && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setEditingPrice(true)}
                    aria-label="Edit the cost per system lead"
                  >
                    <Pencil className="size-3.5" /> Edit
                  </Button>
                )}
              </>
            )}
          </div>

          {open ? (
            <span className="bg-success/10 text-success rounded px-2 py-0.5 text-xs font-medium">
              Open
            </span>
          ) : (
            <span className="bg-muted text-muted-foreground inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium">
              <Lock className="size-3" /> Frozen
            </span>
          )}
        </div>

        {/* UNPRICED IS NOT FREE. A null cost means the price was missing when
            the lead was delivered — a configuration failure. Shown loudly,
            because the alternative is quietly under-billing. */}
        {((brokerId ? ledger?.unpriced_count : summary?.unpriced_count) ?? 0) > 0 && (
          <div className="border-warning/30 bg-warning/5 rounded border p-3 text-sm">
            <span className="font-medium">
              {brokerId ? ledger?.unpriced_count : summary?.unpriced_count} leads have no
              price recorded.
            </span>{' '}
            <span className="text-muted-foreground">
              They were delivered while no lead cost was configured, and are not
              included in the totals below.
            </span>
          </div>
        )}

        <div className="bg-card overflow-x-auto rounded border">
          {loading ? (
            <p className="text-muted-foreground p-8 text-center text-sm">Loading…</p>
          ) : !brokerId ? (
            /* ── The group sheet ──────────────────────────────────────── */
            <table className="w-full min-w-[44rem] border-collapse text-sm">
              <thead>
                <tr className="bg-secondary/60 text-muted-foreground border-b text-left text-[0.7rem] tracking-wide uppercase">
                  <Th>Broker</Th>
                  <Th right>Leads</Th>
                  <Th right>Allocated</Th>
                  <Th right>Subtracted</Th>
                  <Th right>Charged</Th>
                  <Th />
                </tr>
              </thead>
              <tbody>
                {!summary?.brokers.length && (
                  <tr>
                    <Td colSpan={6} muted className="py-10 text-center">
                      No leads were delivered to this group in{' '}
                      {months.find((m) => m.value === month)?.label}.
                    </Td>
                  </tr>
                )}
                {summary?.brokers.map((b) => (
                  <tr key={b.broker_id} className="hover:bg-accent/45 border-b last:border-b-0">
                    <Td className="font-medium">{b.name}</Td>
                    <Td right muted>
                      {b.leads}
                    </Td>
                    <Td right>{money(b.allocated)}</Td>
                    <Td right>
                      {Number(b.credited) > 0 ? (
                        <span className="text-destructive">−{money(b.credited)}</span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </Td>
                    <Td right className="font-semibold">
                      {money(b.charged)}
                    </Td>
                    <Td right>
                      <div className="flex items-center justify-end gap-1">
                        {open && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setBrokerId(b.broker_id)
                              setView('adjust')
                            }}
                          >
                            Adjust
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setBrokerId(b.broker_id)
                            setView('report')
                          }}
                        >
                          <FileText className="size-3.5" /> Report
                        </Button>
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
              {!!summary?.brokers.length && (
                <tfoot>
                  <tr className="bg-secondary/60 border-t-2 font-semibold">
                    <Td>{summary.brokers.length} brokers</Td>
                    <Td right>{summary.brokers.reduce((n, b) => n + b.leads, 0)}</Td>
                    <Td right>{money(summary.total_allocated)}</Td>
                    <Td right className="text-destructive">
                      {Number(summary.total_credited) > 0
                        ? `−${money(summary.total_credited)}`
                        : '—'}
                    </Td>
                    <Td right className="text-base">
                      {money(summary.total_charged)}
                    </Td>
                    <Td />
                  </tr>
                </tfoot>
              )}
            </table>
          ) : sheet === 'report' ? (
            /* ── The ledger ───────────────────────────────────────────── */
            <div>
              <div className="hidden border-b px-4 py-4 print:block">
                <div className="text-lg font-semibold">PolicyScanner Brokerage</div>
                <div className="text-muted-foreground text-xs">
                  A division of Fairstar Insurance
                </div>
              </div>

              <div className="flex items-baseline justify-between gap-4 border-b px-4 py-3">
                <div>
                  <div className="font-semibold">{brokerName}</div>
                  <div className="text-muted-foreground text-xs">
                    {months.find((m) => m.value === month)?.label} · lead cost statement
                  </div>
                </div>
                <div className="flex items-baseline gap-3">
                  <div className="text-lg font-semibold tabular-nums">
                    {money(ledger?.charged ?? 0)}
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    className="print:hidden"
                    onClick={() => window.print()}
                  >
                    <Download className="size-3.5" /> PDF
                  </Button>
                </div>
              </div>

              <table className="w-full min-w-[40rem] border-collapse text-sm">
                <thead>
                  <tr className="bg-secondary/60 text-muted-foreground border-b text-left text-[0.7rem] tracking-wide uppercase">
                    <Th>#</Th>
                    <Th>Customer</Th>
                    <Th>Phone</Th>
                    <Th>Received</Th>
                    <Th right>Cost</Th>
                    <Th right>Not charged</Th>
                    <Th right>Charged</Th>
                  </tr>
                </thead>
                <tbody>
                  {!ledger?.lines.length && (
                    <tr>
                      <Td colSpan={7} muted className="py-10 text-center">
                        No leads were delivered to this broker that month.
                      </Td>
                    </tr>
                  )}
                  {/* A CREDITED LEAD IS STRUCK THROUGH, not removed. It stays in
                      the numbering because it really did arrive, and the strike
                      reads down a column without checking a figure per row. */}
                  {ledger?.lines.map((line, i) => (
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
                      <Td right className={cn(line.credit_reason && 'line-through')}>
                        {money(line.cost)}
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
                      {ledger?.lines.length ?? 0} leads allocated
                    </Td>
                    <Td right className="py-2">
                      {money(ledger?.allocated ?? 0)}
                    </Td>
                    <Td right className="text-destructive py-2 text-xs">
                      {credited.length ? `${credited.length} not charged` : '—'}
                    </Td>
                    <Td right className="text-destructive py-2">
                      {Number(ledger?.credited ?? 0) > 0
                        ? `−${money(ledger?.credited ?? 0)}`
                        : '—'}
                    </Td>
                  </tr>
                  <tr className="bg-secondary/60 border-t-2 font-semibold">
                    <Td colSpan={6} className="py-3">
                      Charged to {brokerName} ·{' '}
                      {months.find((m) => m.value === month)?.label}
                    </Td>
                    <Td right className="py-3 text-base">
                      {money(ledger?.charged ?? 0)}
                    </Td>
                  </tr>
                </tfoot>
              </table>
            </div>
          ) : (
            /* ── One broker's worksheet ───────────────────────────────── */
            <div>
              <div className="flex items-baseline justify-between gap-4 border-b px-4 py-3">
                <div>
                  <div className="font-semibold">{brokerName}</div>
                  <div className="text-muted-foreground text-xs">
                    {ledger?.lines.length ?? 0} leads allocated
                    {price ? ` × ${money(price)}` : ''}
                  </div>
                </div>
                <div className="text-lg font-semibold tabular-nums">
                  {money(ledger?.allocated ?? 0)}
                </div>
              </div>

              <table className="w-full min-w-[40rem] border-collapse text-sm">
                <thead>
                  <tr className="bg-secondary/60 text-muted-foreground border-b text-left text-[0.7rem] tracking-wide uppercase">
                    <Th>Customer</Th>
                    <Th>Phone</Th>
                    <Th>Received</Th>
                    <Th>Reason</Th>
                    <Th right>Amount</Th>
                    <Th />
                  </tr>
                </thead>
                <tbody>
                  {!credited.length && !open && (
                    <tr className="border-b">
                      <Td colSpan={6} muted className="py-6 text-center text-xs">
                        Nothing subtracted. The broker was charged for every lead.
                      </Td>
                    </tr>
                  )}

                  {credited.map((line) => (
                    <tr key={line.lead_id} className="border-b">
                      <Td>
                        {line.name ?? <span className="text-muted-foreground">No name</span>}
                      </Td>
                      <Td muted className="text-xs tabular-nums">
                        {line.phone ?? '—'}
                      </Td>
                      <Td muted className="text-xs">
                        {shortDate(line.delivered_at)}
                      </Td>
                      <Td className="text-xs">{line.credit_reason}</Td>
                      <Td right className="text-destructive">
                        −{money(line.cost)}
                      </Td>
                      <Td right>
                        {open && (
                          <button
                            type="button"
                            aria-label="Put back on the bill"
                            disabled={busy}
                            onClick={() => uncredit(line)}
                            className="text-muted-foreground hover:text-destructive"
                          >
                            <X className="size-3.5" />
                          </button>
                        )}
                      </Td>
                    </tr>
                  ))}

                  {/* THE ENTRY ROW, inside the grid. A spreadsheet types the
                      next line where the next line goes; results drop directly
                      beneath it. */}
                  {open && (
                    <tr className="bg-card border-b">
                      <Td colSpan={6}>
                        <div className="border-input focus-within:border-ring bg-background flex items-center gap-2 rounded-md border px-2.5 py-1.5 transition-colors">
                          <Search className="text-muted-foreground size-4 shrink-0" />
                          <input
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="Search this month's leads by phone number…"
                            className="text-foreground placeholder:text-muted-foreground min-w-0 flex-1 bg-transparent text-sm outline-none"
                          />
                        </div>
                      </Td>
                    </tr>
                  )}

                  {open &&
                    query.replace(/\D/g, '').length >= 3 &&
                    (matches.length === 0 ? (
                      <tr className="border-b">
                        <Td colSpan={6} muted className="text-xs">
                          No charged lead on that number this month.
                        </Td>
                      </tr>
                    ) : (
                      matches.map((line) => (
                        <tr key={line.lead_id} className="hover:bg-accent/45 border-b">
                          <Td>
                            {line.name ?? (
                              <span className="text-muted-foreground">No name</span>
                            )}
                          </Td>
                          <Td muted className="text-xs tabular-nums">
                            {line.phone ?? '—'}
                          </Td>
                          <Td muted className="text-xs">
                            {shortDate(line.delivered_at)}
                          </Td>
                          <Td colSpan={2}>
                            {/* Picking the reason IS the action — a separate
                                Add button would mean choosing a reason and
                                then confirming it, two steps for one
                                decision. */}
                            <select
                              defaultValue=""
                              disabled={busy}
                              onChange={(e) =>
                                e.target.value &&
                                credit(line, e.target.value as CreditReason)
                              }
                              className="border-input text-foreground w-full rounded border bg-transparent px-1.5 py-1 text-xs outline-none"
                            >
                              <option value="" disabled>
                                Subtract — pick a reason…
                              </option>
                              {REASONS.map((r) => (
                                <option key={r} value={r}>
                                  {r}
                                </option>
                              ))}
                            </select>
                          </Td>
                          <Td right>
                            <Plus className="text-muted-foreground ml-auto size-3.5" />
                          </Td>
                        </tr>
                      ))
                    ))}
                </tbody>

                <tfoot>
                  <tr className="text-muted-foreground border-t">
                    <Td colSpan={4} className="py-2 text-xs">
                      Subtracted ({credited.length})
                    </Td>
                    <Td right className="text-destructive py-2">
                      {Number(ledger?.credited ?? 0) > 0
                        ? `−${money(ledger?.credited ?? 0)}`
                        : '—'}
                    </Td>
                    <Td />
                  </tr>
                  <tr className="bg-secondary/60 border-t-2 font-semibold">
                    <Td colSpan={4} className="py-3">
                      Charged to {brokerName}
                    </Td>
                    <Td right className="py-3 text-base">
                      {money(ledger?.charged ?? 0)}
                    </Td>
                    <Td />
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </div>

        <p className="text-muted-foreground text-xs print:hidden">
          {open
            ? 'Adjustments freeze on the 5th of next month. Nothing can be changed after that.'
            : `Frozen. These are the figures charged for ${months.find((m) => m.value === month)?.label}.`}
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
