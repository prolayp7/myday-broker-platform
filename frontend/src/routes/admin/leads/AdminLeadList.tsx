import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Search } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import {
  getAdminLeads,
  getBrokerGroups,
  ADMIN_LEAD_STAGES,
  type AdminLead,
  type AdminLeadCounts,
  type AdminLeadFilters,
  type BrokerGroup,
} from '@/data/api'
import { ReassignDialog } from './ReassignDialog'

// Admin › Leads — the oversight view of EVERY lead across all communities,
// groups and brokers (SAP Fiori List Report floorplan). One screen, reached
// three ways via URL filters: /admin/leads (all), ?broker=<id> (from a broker's
// page — this doubles as broker-lead-history), ?group=<id> (from a group).
//
// SPEED MODEL (the whole reason this screen is shaped this way):
//  · ONE request per view — getAdminLeads returns rows + total + KPI counts in
//    a single payload, so it's one network round-trip no matter how much is
//    shown.
//  · Filters are SERVER-SIDE and live in the URL — the browser never holds more
//    than one page, so it stays fast at any lead volume, and the ?broker/?group
//    "doors" work for free (a filter is just a URL param).
//  · Debounced search + keep-previous-rows-while-refetching make filter changes
//    feel instant even though each one is a real server round-trip.

// Community name → dot color. Documented map for the known communities; an
// unknown/new community falls back to neutral grey (no fabricated backend
// field — community has no color column).
const COMMUNITY_COLOR: Record<string, string> = {
  Dragon: 'var(--comm-dragon)',
  Tiger: 'var(--comm-tiger)',
  Nature: 'var(--comm-nature)',
}
const communityColor = (name: string | null | undefined) =>
  (name && COMMUNITY_COLOR[name]) || 'var(--comm-unknown)'

// Honest stage semantics: New = info, Issued = success, Lost = negative,
// everything mid-pipeline neutral.
function stageClass(stage: string): string {
  if (stage === 'New') return 'bg-info-muted text-info'
  if (stage === 'Issued') return 'bg-success-muted text-success'
  if (stage === 'Lost') return 'bg-error-muted text-error'
  return 'bg-secondary text-muted-foreground'
}

const PAGE_SIZE = 50

export default function AdminLeadList() {
  const [params, setParams] = useSearchParams()

  // All filter state derives from the URL — the URL is the single source of
  // truth, so back/forward and shared links reproduce the exact view.
  const brokerId = params.get('broker') ?? ''
  const groupId = params.get('group') ?? ''
  const community = params.get('community') ?? ''
  const stage = params.get('stage') ?? ''
  const unassigned = params.get('unassigned') === '1'
  const search = params.get('q') ?? ''
  const page = Math.max(1, Number(params.get('page')) || 1)

  const [data, setData] = useState<AdminLead[]>([])
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState<AdminLeadCounts>({ all: 0, unassigned: 0, assigned: 0, today: 0 })
  const [loading, setLoading] = useState(true)
  const [refetching, setRefetching] = useState(false) // keep-previous-rows: dim, don't blank
  const [groups, setGroups] = useState<BrokerGroup[]>([])
  const [reassignFor, setReassignFor] = useState<AdminLead | null>(null)

  // Local search box mirrors the URL but is debounced into it (300ms) so typing
  // doesn't fire a request per keystroke.
  const [searchDraft, setSearchDraft] = useState(search)
  useEffect(() => setSearchDraft(search), [search])

  // Group list for the filter dropdown + resolving the ?group banner name.
  useEffect(() => {
    getBrokerGroups().then(setGroups).catch(() => setGroups([]))
  }, [])

  // Single fetch. `firstLoad` shows the skeleton; later fetches keep the old
  // rows visible (dimmed) until the new page lands.
  const firstLoad = useRef(true)
  const load = useCallback(() => {
    const filters: AdminLeadFilters = {
      search: search || undefined,
      community: community || undefined,
      groupId: groupId || undefined,
      brokerId: brokerId || undefined,
      stage: stage || undefined,
      unassigned: unassigned || undefined,
      page,
      limit: PAGE_SIZE,
    }
    if (firstLoad.current) setLoading(true)
    else setRefetching(true)
    getAdminLeads(filters)
      .then((res) => {
        // Defensive: never let a malformed/unexpected body (e.g. an error
        // payload) put a non-array into `data` — a stray undefined here would
        // crash the whole screen on the next `.find`/`.map`.
        setData(Array.isArray(res?.rows) ? res.rows : [])
        setTotal(typeof res?.total === 'number' ? res.total : 0)
        setCounts(res?.counts ?? { all: 0, unassigned: 0, assigned: 0, today: 0 })
      })
      .catch(() => {
        setData([])
        setTotal(0)
        setCounts({ all: 0, unassigned: 0, assigned: 0, today: 0 })
      })
      .finally(() => {
        setLoading(false)
        setRefetching(false)
        firstLoad.current = false
      })
  }, [search, community, groupId, brokerId, stage, unassigned, page])
  useEffect(load, [load])

  // ── URL mutation helpers — every filter change resets to page 1 ──
  function setFilter(key: string, value: string | null) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    next.delete('page')
    setParams(next, { replace: true })
  }
  function goPage(p: number) {
    const next = new URLSearchParams(params)
    if (p <= 1) next.delete('page')
    else next.set('page', String(p))
    setParams(next)
  }
  function clearAll() {
    setParams(new URLSearchParams(), { replace: true })
  }

  // Debounce the search draft into the URL.
  useEffect(() => {
    if (searchDraft === search) return
    const t = setTimeout(() => setFilter('q', searchDraft || null), 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchDraft])

  const activeGroup = groups.find((g) => g.id === groupId)
  const brokerName = data.find((l) => l.assigned_broker_id === brokerId)?.broker?.name
  const hasContext = Boolean(brokerId || groupId)
  const anyFilter = Boolean(brokerId || groupId || community || stage || unassigned || search)

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const from = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const to = Math.min(page * PAGE_SIZE, total)

  return (
    <AppShell section="Admin">
      <div className="mx-auto max-w-7xl space-y-4 p-6">
        <Link
          to="/admin"
          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
        >
          <ArrowLeft className="size-4" /> Admin
        </Link>

        <div>
          <h1 className="text-2xl font-semibold">Leads</h1>
          <p className="text-muted-foreground mt-1 max-w-2xl text-sm">
            Every lead across all communities, groups and brokers — find where each one is, spot leads
            sitting unworked in the pool, and reassign.
          </p>
        </div>

        {/* Context banner — appears when arriving pre-filtered from a broker/group page */}
        {hasContext && (
          <div className="bg-info-muted border-primary/25 flex items-center gap-3 rounded-lg border px-3.5 py-2.5 text-sm">
            <span>
              Showing leads for{' '}
              <span className="bg-card rounded-full border px-2 py-0.5 text-xs font-semibold">
                {brokerId ? `Broker · ${brokerName ?? '…'}` : `Group · ${activeGroup?.name ?? '…'}`}
              </span>
            </span>
            <button onClick={clearAll} className="text-primary ml-auto text-xs">
              Clear filter · show all leads
            </button>
          </div>
        )}

        {/* KPI chips — click to filter. Unassigned carries a warning stripe. */}
        <div className="flex flex-wrap gap-3">
          <KpiChip label="All leads" value={counts.all} active={!unassigned} onClick={() => setFilter('unassigned', null)} />
          <KpiChip label="Unassigned · in pool" value={counts.unassigned} warn active={unassigned} onClick={() => setFilter('unassigned', '1')} />
          <KpiChip label="Assigned" value={counts.assigned} />
          <KpiChip label="Arrived today" value={counts.today} />
        </div>

        {/* Filter bar */}
        <div className="bg-card flex flex-wrap items-center gap-2 rounded-lg border p-2.5 shadow-[var(--shadow-card)]">
          <div className="border-input text-muted-foreground flex min-w-48 flex-1 items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-sm">
            <Search className="size-4" />
            <input
              value={searchDraft}
              onChange={(e) => setSearchDraft(e.target.value)}
              placeholder="Search name or phone…"
              className="text-foreground min-w-0 flex-1 bg-transparent outline-none"
            />
          </div>
          <FilterSelect
            label="Community"
            value={community}
            onChange={(v) => setFilter('community', v)}
            options={['Dragon', 'Tiger', 'Nature'].map((c) => ({ value: c, label: c }))}
          />
          <FilterSelect
            label="Group"
            value={groupId}
            onChange={(v) => setFilter('group', v)}
            options={groups.map((g) => ({ value: g.id, label: g.name ?? 'Untitled' }))}
          />
          <FilterSelect
            label="Stage"
            value={stage}
            onChange={(v) => setFilter('stage', v)}
            options={ADMIN_LEAD_STAGES.map((s) => ({ value: s, label: s }))}
          />
          {anyFilter && (
            <button onClick={clearAll} className="text-primary ml-auto px-2 py-1.5 text-sm">
              Clear
            </button>
          )}
        </div>

        {/* Table */}
        <div className="bg-card overflow-hidden rounded-lg border shadow-[var(--shadow-card)]">
          <div className="flex items-center justify-between border-b px-4 py-2.5">
            <div className="text-sm font-semibold">
              Leads <span className="text-muted-foreground font-normal">({total})</span>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[56rem] border-collapse text-sm">
              <thead>
                <tr className="text-muted-foreground border-b text-left text-[0.7rem] tracking-wide uppercase">
                  <th className="px-4 py-2.5 font-semibold">Customer</th>
                  <th className="px-4 py-2.5 font-semibold">Community</th>
                  <th className="px-4 py-2.5 font-semibold">Group</th>
                  <th className="px-4 py-2.5 font-semibold">Broker</th>
                  <th className="px-4 py-2.5 font-semibold">Stage</th>
                  <th className="px-4 py-2.5 font-semibold">Received</th>
                  <th className="px-4 py-2.5"></th>
                </tr>
              </thead>
              <tbody className={refetching ? 'opacity-50 transition-opacity' : undefined}>
                {loading ? (
                  <SkeletonRows />
                ) : data.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="text-muted-foreground px-4 py-12 text-center">
                      <div className="text-foreground mb-1 font-semibold">No leads match these filters</div>
                      Try clearing a filter, or check the pool for unassigned leads.
                    </td>
                  </tr>
                ) : (
                  data.map((l) => (
                    <LeadRow key={l.id} lead={l} onReassign={() => setReassignFor(l)} />
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* Pager */}
          {!loading && total > 0 && (
            <div className="text-muted-foreground flex items-center justify-between border-t px-4 py-2.5 text-xs">
              <span>
                Showing {from}–{to} of {total}
              </span>
              <div className="flex items-center gap-1">
                <PagerBtn disabled={page <= 1} onClick={() => goPage(page - 1)}>‹</PagerBtn>
                <span className="px-2 tabular-nums">
                  Page {page} of {totalPages}
                </span>
                <PagerBtn disabled={page >= totalPages} onClick={() => goPage(page + 1)}>›</PagerBtn>
              </div>
            </div>
          )}
        </div>
      </div>

      {reassignFor && (
        <ReassignDialog
          lead={reassignFor}
          groups={groups}
          onClose={() => setReassignFor(null)}
          onReassigned={() => {
            setReassignFor(null)
            load()
          }}
        />
      )}
    </AppShell>
  )
}

function KpiChip({
  label,
  value,
  warn,
  active,
  onClick,
}: {
  label: string
  value: number
  warn?: boolean
  active?: boolean
  onClick?: () => void
}) {
  return (
    <button
      onClick={onClick}
      disabled={!onClick}
      className={[
        'bg-card relative min-w-36 flex-1 rounded-lg border p-3 text-left shadow-[var(--shadow-card)] transition',
        onClick ? 'cursor-pointer hover:border-primary/40' : 'cursor-default',
        active ? 'border-primary ring-primary ring-1' : '',
      ].join(' ')}
    >
      {warn && <span className="bg-warning absolute top-3 bottom-3 left-0 w-[3px] rounded-full" />}
      <div className={`text-2xl font-semibold tabular-nums ${warn ? 'text-warning' : ''}`}>{value}</div>
      <div className="text-muted-foreground mt-1 text-xs tracking-wide uppercase">{label}</div>
    </button>
  )
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (v: string | null) => void
  options: { value: string; label: string }[]
}) {
  return (
    <label className="border-input flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value || null)}
        className="text-foreground bg-transparent outline-none"
      >
        <option value="">All</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function LeadRow({ lead: l, onReassign }: { lead: AdminLead; onReassign: () => void }) {
  const communityName = l.broker_group?.community?.name
  const groupName = l.broker_group?.name ?? 'Untitled'
  const received = relativeTime(l.created_at)
  return (
    <tr className="hover:bg-accent/45 border-b last:border-b-0">
      <td className="relative px-4 py-2.5">
        {!l.assigned_broker_id && <span className="bg-warning absolute top-0 bottom-0 left-0 w-[3px]" />}
        <div className="font-semibold">{l.name}</div>
        <div className="text-muted-foreground text-xs">{l.phone}</div>
      </td>
      <td className="px-4 py-2.5">
        <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
          <span className="size-2.5 rounded-full" style={{ background: communityColor(communityName) }} />
          {communityName ?? '—'}
        </span>
      </td>
      <td className="px-4 py-2.5">
        <Link to={`/admin/broker-groups/${l.group_id}`} className="hover:text-primary hover:underline">
          {groupName}
        </Link>
      </td>
      <td className="px-4 py-2.5">
        {l.broker ? (
          <Link
            to={`/admin/broker-groups/${l.group_id}/brokers/${l.broker.id}`}
            className="hover:text-primary hover:underline"
          >
            {l.broker.name}
          </Link>
        ) : (
          <span className="text-warning inline-flex items-center gap-1.5 font-medium whitespace-nowrap">
            <span className="bg-warning size-2 rounded-full" />
            Unassigned
          </span>
        )}
      </td>
      <td className="px-4 py-2.5">
        <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${stageClass(l.stage)}`}>
          {l.stage}
        </span>
      </td>
      <td className="text-muted-foreground px-4 py-2.5 whitespace-nowrap tabular-nums">{received}</td>
      <td className="px-4 py-2.5">
        <button
          onClick={onReassign}
          className="border-border hover:border-primary/45 hover:text-primary rounded-md border px-2.5 py-1 text-xs font-medium whitespace-nowrap"
        >
          Reassign
        </button>
      </td>
    </tr>
  )
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 8 }).map((_, i) => (
        <tr key={i} className="border-b last:border-b-0">
          {['60%', '40%', '55%', '50%', '45%', '35%', '4rem'].map((w, j) => (
            <td key={j} className="px-4 py-3">
              <div className="bg-secondary h-3 animate-pulse rounded" style={{ width: w }} />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}

function PagerBtn({
  children,
  disabled,
  onClick,
}: {
  children: React.ReactNode
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      className="border-border bg-card hover:bg-accent inline-flex size-7 items-center justify-center rounded-md border disabled:opacity-40"
    >
      {children}
    </button>
  )
}

// "2h ago" / "3d ago" / a date past a week. Received = created_at (when the
// lead entered the system) — we do NOT use requested_at (null on real leads).
function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 7) return `${days}d ago`
  return new Date(iso).toLocaleDateString()
}
