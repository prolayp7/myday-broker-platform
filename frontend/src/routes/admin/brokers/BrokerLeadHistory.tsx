import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { MOCK_BROKERS, MOCK_BROKER_LEADS, type MockBrokerLead, type MockStage } from '@/data/mock/brokerActivity'

// Admin · Brokers · [name] — Page 2 of the drilldown. Every lead this broker
// has ever taken, grouped by day. Deliberately reads from the immutable
// delivery record (lead_delivery, once wired to real data) rather than
// "leads currently assigned to me" — a lead later reassigned away must still
// show here, since the broker really worked it that day.
//
// Includes stage (unlike the main admin Leads screen, which stays allocation-
// only): this page is specifically about the broker's own activity/output, so
// whether they've actually progressed a lead is exactly the point.
//
// TEMPORARY: mock data (see mock/brokerActivity.ts) — swapped for
// GET /brokers/{id}/leads once that endpoint exists.

const STAGE_CLASSES: Record<MockStage, string> = {
  New: 'bg-secondary text-muted-foreground',
  // Same neutral treatment as New: work has started but nothing is at stake yet.
  // This Record is exhaustive over MockStage, so the compiler refuses to build
  // until a new stage is given a colour — which is how this file caught 0115.
  'In Progress': 'bg-secondary text-muted-foreground',
  'Quote Done': 'bg-warning-muted text-warning',
  Pending: 'bg-warning-muted text-warning',
  Sold: 'bg-warning-muted text-warning',
  Submitted: 'bg-warning-muted text-warning',
  Issued: 'bg-success-muted text-success',
  Lost: 'bg-error-muted text-error',
}

function dayKey(iso: string): string {
  return new Date(iso).toDateString()
}

function dayLabel(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  if (dayKey(iso) === dayKey(today.toISOString())) return `Today · ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

export default function BrokerLeadHistory() {
  const { brokerId } = useParams<{ brokerId: string }>()
  const [range, setRange] = useState<'all' | 'today' | 'week'>('all')

  const broker = MOCK_BROKERS.find((b) => b.id === brokerId)
  const allLeads = (brokerId && MOCK_BROKER_LEADS[brokerId]) || []

  const filtered = useMemo(() => {
    if (range === 'all') return allLeads
    const now = Date.now()
    const cutoff = range === 'today' ? new Date().setHours(0, 0, 0, 0) : now - 7 * 86400000
    return allLeads.filter((l) => new Date(l.delivered_at).getTime() >= cutoff)
  }, [allLeads, range])

  const days = useMemo(() => {
    const groups = new Map<string, MockBrokerLead[]>()
    for (const lead of filtered) {
      const key = dayKey(lead.delivered_at)
      if (!groups.has(key)) groups.set(key, [])
      groups.get(key)!.push(lead)
    }
    return Array.from(groups.entries())
      .sort((a, b) => new Date(b[1][0].delivered_at).getTime() - new Date(a[1][0].delivered_at).getTime())
      .map(([, leads]) => leads.sort((a, b) => new Date(a.delivered_at).getTime() - new Date(b.delivered_at).getTime()))
  }, [filtered])

  const todayCount = allLeads.filter((l) => new Date(l.delivered_at).setHours(0, 0, 0, 0) === new Date().setHours(0, 0, 0, 0)).length
  const weekCount = allLeads.filter((l) => new Date(l.delivered_at).getTime() >= Date.now() - 7 * 86400000).length

  if (!broker) {
    return (
      <AppShell section="Admin · Brokers">
        <div className="mx-auto max-w-3xl p-6">
          <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
            <Link to="/admin/brokers">
              <ArrowLeft /> Brokers
            </Link>
          </Button>
          <p className="text-muted-foreground mt-4">Broker not found.</p>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell section={`Admin · ${broker.name}`}>
      <div className="mx-auto max-w-3xl space-y-6 p-6">
        <div>
          <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
            <Link to="/admin/brokers">
              <ArrowLeft /> Brokers
            </Link>
          </Button>
          <h1 className="text-2xl font-semibold">{broker.name} — leads taken</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Every lead she's taken, grouped by day.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <RangePill active={range === 'all'} onClick={() => setRange('all')}>
            All time
          </RangePill>
          <RangePill active={range === 'today'} onClick={() => setRange('today')}>
            Today · {todayCount}
          </RangePill>
          <RangePill active={range === 'week'} onClick={() => setRange('week')}>
            This week · {weekCount}
          </RangePill>
        </div>

        {days.length === 0 ? (
          <div className="rounded-lg border bg-card p-10 text-center">
            <p className="text-muted-foreground text-sm">No leads in this range.</p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg border bg-card">
            {days.map((dayLeads) => (
              <div key={dayLeads[0].id}>
                <div className="text-muted-foreground bg-secondary flex items-center border-b px-4 py-2.5 text-xs font-bold tracking-wide uppercase">
                  {dayLabel(dayLeads[0].delivered_at)}
                  <span className="text-foreground ml-auto normal-case">{dayLeads.length} leads</span>
                </div>
                <table className="w-full text-sm">
                  <tbody>
                    {dayLeads.map((l) => (
                      <tr key={l.id} className="border-b last:border-0">
                        <td className="w-2/5 px-4 py-3">
                          <div className="font-semibold">{l.name}</div>
                          <div className="text-muted-foreground text-xs">{l.phone}</div>
                        </td>
                        <td className="px-4 py-3">
                          <span className="text-muted-foreground rounded-md border px-1.5 py-0.5 text-2xs">
                            {l.source}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          {l.assigned_by === 'system' ? (
                            'System'
                          ) : (
                            <span className="inline-flex items-center gap-1.5">
                              Admin
                              <span className="text-muted-foreground rounded-full border px-1.5 py-px text-2xs">
                                reassigned from {l.reassigned_from}
                              </span>
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <span className={cn('rounded-full px-2 py-0.5 text-2xs font-semibold', STAGE_CLASSES[l.stage])}>
                            {l.stage}
                          </span>
                        </td>
                        <td className="text-muted-foreground px-4 py-3 text-right tabular-nums">
                          {clockTime(l.delivered_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  )
}

function RangePill({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full border px-3.5 py-1.5 text-xs font-medium transition',
        active ? 'bg-primary border-transparent text-primary-foreground' : 'text-muted-foreground hover:bg-muted',
      )}
    >
      {children}
    </button>
  )
}
