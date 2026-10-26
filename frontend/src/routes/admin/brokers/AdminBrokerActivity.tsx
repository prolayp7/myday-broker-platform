import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, ChevronRight, CircleDot } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { MOCK_GROUPS, MOCK_BROKERS } from '@/data/mock/brokerActivity'

// Admin · Brokers — "who's carrying what, and what have they actually taken."
// Page 1 of a two-page flow: pick a group (pills), see its broker roster,
// click a broker to open their own leads-taken history (Page 2,
// BrokerLeadHistory.tsx, a real page navigation — not an in-place expand,
// matching how BrokerGroupList -> BrokerGroupDetail -> BrokerDetail already
// works elsewhere in admin).
//
// TEMPORARY: built against mock/brokerActivity.ts so the layout can be
// reviewed with a realistic multi-broker, multi-day dataset (today's real DB
// has exactly 1 lead). Swapped for GET /broker-groups + GET /brokers/{id}
// counts right after review — see that file's header comment.

export default function AdminBrokerActivity() {
  const [selectedGroupId, setSelectedGroupId] = useState(MOCK_GROUPS[0]?.id ?? null)

  const brokers = MOCK_BROKERS.filter((b) => b.group_id === selectedGroupId)
  const selectedGroup = MOCK_GROUPS.find((g) => g.id === selectedGroupId)

  return (
    <AppShell section="Admin · Brokers">
      <div className="mx-auto max-w-3xl space-y-6 p-6">
        <div>
          <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
            <Link to="/admin">
              <ArrowLeft /> Admin
            </Link>
          </Button>
          <h1 className="text-2xl font-semibold">Brokers</h1>
          <p className="text-muted-foreground mt-1 max-w-xl text-sm">
            Click a group to see the brokers in it. Click a broker to open their lead history.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {MOCK_GROUPS.map((g) => (
            <button
              key={g.id}
              type="button"
              onClick={() => setSelectedGroupId(g.id)}
              className={cn(
                'rounded-full border px-3.5 py-1.5 text-xs font-medium transition',
                selectedGroupId === g.id
                  ? 'bg-primary border-transparent text-primary-foreground'
                  : 'text-muted-foreground hover:bg-muted',
              )}
            >
              {g.label}
            </button>
          ))}
        </div>

        <div className="overflow-hidden rounded-lg border bg-card">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-muted-foreground border-b text-left">
                <th className="px-4 py-2.5 text-xs font-semibold tracking-wide uppercase">Broker</th>
                <th className="px-4 py-2.5 text-xs font-semibold tracking-wide uppercase">Status</th>
                <th className="px-4 py-2.5 text-right text-xs font-semibold tracking-wide uppercase">
                  Leads taken
                </th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {brokers.length === 0 ? (
                <tr>
                  <td colSpan={4} className="text-muted-foreground p-8 text-center">
                    No brokers in {selectedGroup?.label ?? 'this group'}.
                  </td>
                </tr>
              ) : (
                brokers.map((b) => (
                  <tr key={b.id} className="border-b last:border-0">
                    <td className="px-4 py-2.5">
                      <Link
                        to={`/admin/brokers/${b.id}/leads`}
                        className="flex items-center gap-2.5 hover:underline"
                      >
                        <span className="bg-muted text-muted-foreground flex size-7 shrink-0 items-center justify-center rounded-full text-2xs font-semibold">
                          {initials(b.name)}
                        </span>
                        <div>
                          <div className="font-semibold">{b.name}</div>
                          <div className="text-muted-foreground text-xs">broker</div>
                        </div>
                      </Link>
                    </td>
                    <td className="px-4 py-2.5">
                      {b.status === 'active' ? (
                        <span className="text-success text-sm font-semibold">Active</span>
                      ) : (
                        <span className="text-error inline-flex items-center gap-1.5 text-sm font-semibold">
                          <CircleDot className="size-3.5" /> Paused
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{b.leads_taken}</td>
                    <td className="px-4 py-2.5 text-right">
                      <Link to={`/admin/brokers/${b.id}/leads`}>
                        <ChevronRight className="text-muted-foreground size-4" />
                      </Link>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </AppShell>
  )
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/)
  if (!parts[0]) return '?'
  return (parts[0][0] + (parts[1]?.[0] ?? '')).toUpperCase()
}
