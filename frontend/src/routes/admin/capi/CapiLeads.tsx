import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { AppShell } from '@/components/AppShell'
import {
  getMetaHealth,
  getMetaLeadProgress,
  type MetaHealth,
  type MetaLeadProgress,
  type MetaMilestone,
} from '@/data/api'

// Admin › Meta Quality: every Facebook lead, and what Meta has been told about
// it — on the same paper as the customer screen (classic.css, .classic).
//
// READ-ONLY. It shows what happened and nothing else. The one interaction is
// reading Facebook's reason for a refusal: a red "Not sent" opens it in a single
// message area above the table, which closes by itself. Retrying lives on the
// details page (/admin/capi/details), for when an admin means to act.
//
// The learning line answers the other question this screen exists for: is Meta
// getting enough Qualified events (~50 a week) to finish learning?

/** How far back the list reaches. Wide enough that "over 7 days" has
 *  something in it. */
const WINDOW_DAYS = 90

/** Meta's rough weekly need of optimisation events to leave learning. */
const LEARNING_TARGET = 50

/** How long a Facebook error stays open before closing by itself. */
const MESSAGE_MS = 15_000

const DAY_MS = 24 * 60 * 60 * 1000

// By the day the lead ARRIVED. Meta refuses an event dated more than seven
// days back, so these are the leads running out of time and the ones past it.
const AGES = [
  { key: 'any', label: 'Any' },
  { key: '5to7', label: '5 to 7 days' },
  { key: 'over7', label: 'Over 7 days' },
] as const
type AgeKey = (typeof AGES)[number]['key']

// What one cell can say, and the filter value it answers to.
type CellKey = 'sent' | 'notsent' | 'notq' | 'notasked' | 'none'
type Cell = { key: CellKey; text: string; tone: 'plain' | 'grey' | 'bold' | 'error'; event?: MetaMilestone }

const COLUMN_FILTERS = {
  lead: [
    { key: 'all', label: 'All' },
    { key: 'sent', label: 'Sent' },
    { key: 'notsent', label: 'Not sent' },
  ],
  qual: [
    { key: 'all', label: 'All' },
    { key: 'sent', label: 'Sent' },
    { key: 'notsent', label: 'Not sent' },
    { key: 'notq', label: 'Not qualified' },
    { key: 'notasked', label: 'Not asked yet' },
  ],
  sale: [
    { key: 'all', label: 'All' },
    { key: 'sent', label: 'Sent' },
    { key: 'notsent', label: 'Not sent' },
    { key: 'none', label: 'No sale' },
  ],
} as const

const EVENT_LABEL: Record<MetaMilestone, string> = {
  Lead: 'Lead',
  CompleteRegistration: 'Qualified',
  Purchase: 'Sale',
}

function money(value: number | string | null | undefined): string | null {
  const n = Number(value)
  if (value == null || value === '' || !Number.isFinite(n) || n <= 0) return null
  return `$${n.toLocaleString('en-CA', { maximumFractionDigits: 0 })}`
}

/** One milestone as a cell. "Not sent" covers waiting and refused; only a
 *  refusal is red, because only a refusal has a reason to read. */
function milestoneCell(lead: MetaLeadProgress, event: MetaMilestone): Cell {
  const state = lead.milestones[event]
  if (state === 'sent') {
    const value = event === 'Purchase' ? money(lead.sale_value) : null
    return { key: 'sent', text: value ? `Sent · ${value}` : 'Sent', tone: 'plain' }
  }
  if (state === 'failed') return { key: 'notsent', text: 'Not sent', tone: 'error', event }
  if (state === 'waiting') return { key: 'notsent', text: 'Not sent · waiting', tone: 'bold' }
  if (state === 'not_set_up') return { key: 'notsent', text: 'Not sent · no dataset', tone: 'bold' }
  // Nothing queued: for a sale that is simply "no sale yet".
  return event === 'Purchase'
    ? { key: 'none', text: '—', tone: 'grey' }
    : { key: 'notsent', text: 'Not sent', tone: 'bold' }
}

/** The Qualified column. The broker's answer comes first; only a lead marked
 *  qualified has a Meta event to report on. Until the backend sends the
 *  answer, a lead with no CompleteRegistration reads "Not asked yet". */
function qualifiedCell(lead: MetaLeadProgress): Cell {
  if (lead.qualified === false) return { key: 'notq', text: 'Not qualified', tone: 'grey' }
  if (lead.qualified == null && lead.milestones.CompleteRegistration === 'none') {
    return { key: 'notasked', text: 'Not asked yet', tone: 'grey' }
  }
  return milestoneCell(lead, 'CompleteRegistration')
}

function ageInDays(arrivedAt: string): number {
  return (Date.now() - new Date(arrivedAt).getTime()) / DAY_MS
}

function matchesAge(lead: MetaLeadProgress, age: AgeKey): boolean {
  if (age === 'any') return true
  const days = ageInDays(lead.arrived_at)
  return age === '5to7' ? days >= 5 && days < 7 : days >= 7
}

/** Unique {value, label} options, sorted by label, for a filter dropdown. */
function options(pairs: ({ id: string; name: string } | null | undefined)[]) {
  const seen = new Map<string, string>()
  for (const p of pairs) if (p) seen.set(p.id, p.name)
  return [...seen].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label))
}

/** A broker as a filter option. broker_id arrives with the backend change;
 *  until then the name stands in as the key. */
function brokerOf(lead: MetaLeadProgress) {
  if (!lead.broker_name) return null
  return { id: lead.broker_id ?? lead.broker_name, name: lead.broker_name }
}

type Message = { key: string; text: string }

export default function CapiLeads() {
  const [leads, setLeads] = useState<MetaLeadProgress[] | null>(null)
  const [qualifiedSent7d, setQualifiedSent7d] = useState<number | null>(null)
  const [health, setHealth] = useState<MetaHealth | null>(null)
  const [loading, setLoading] = useState(true)
  // A failed load is not an empty list — "No leads" on a 500 would be false.
  const [loadError, setLoadError] = useState(false)
  const [message, setMessage] = useState<Message | null>(null)
  const messageTimer = useRef<number | null>(null)
  const alive = useRef(true)

  // Filters live in the URL, as on the other admin lists: a refresh keeps the
  // view and it can be sent to someone. The search box is plain local state —
  // a router navigation per keystroke makes typing lag.
  const [params, setParams] = useSearchParams()
  const age = (AGES.find((a) => a.key === params.get('age'))?.key ?? 'any') as AgeKey
  const community = params.get('community') ?? ''
  const group = params.get('group') ?? ''
  const broker = params.get('broker') ?? ''
  const fLead = params.get('lead') ?? 'all'
  const fQual = params.get('qual') ?? 'all'
  const fSale = params.get('sale') ?? 'all'
  const [search, setSearch] = useState('')

  function setParam(updates: Record<string, string | null>) {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(updates)) {
      if (value && value !== 'all') next.set(key, value)
      else next.delete(key)
    }
    setParams(next, { replace: true })
  }

  const load = useCallback(async () => {
    try {
      const data = await getMetaLeadProgress(WINDOW_DAYS)
      if (!alive.current) return
      setLeads(data.leads)
      setQualifiedSent7d(data.qualified_sent_7d ?? null)
      setLoadError(false)
    } catch {
      if (!alive.current) return
      setLoadError(true)
      toast.error('Could not load the conversions')
    } finally {
      if (alive.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    alive.current = true
    load()
    // Asks Meta directly, so it is slower and may fail on its own. It must
    // not hold up or blank the list, which is read from our own database.
    getMetaHealth()
      .then((h) => alive.current && setHealth(h))
      .catch(() => {})
    return () => {
      alive.current = false
      if (messageTimer.current) window.clearTimeout(messageTimer.current)
    }
  }, [load])

  /** Open Facebook's reason for one refusal, or close it if it is already
   *  open. One message at a time; it closes by itself after MESSAGE_MS. */
  function toggleMessage(key: string, text: string) {
    if (messageTimer.current) window.clearTimeout(messageTimer.current)
    if (message?.key === key) {
      setMessage(null)
      return
    }
    setMessage({ key, text })
    messageTimer.current = window.setTimeout(() => setMessage(null), MESSAGE_MS)
  }

  // Cascading choices: a community narrows the groups, and a community or a
  // group narrows the brokers. Built from the leads in the list, so every
  // option has something behind it.
  const all = leads ?? []
  const communityOptions = options(all.map((l) => l.community))
  const groupOptions = options(all.filter((l) => !community || l.community?.id === community).map((l) => l.group))
  const brokerOptions = options(
    all
      .filter((l) => (!community || l.community?.id === community) && (!group || l.group?.id === group))
      .map(brokerOf),
  )

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return (leads ?? [])
      .map((lead) => ({
        lead,
        cells: [milestoneCell(lead, 'Lead'), qualifiedCell(lead), milestoneCell(lead, 'Purchase')] as const,
      }))
      .filter(
        ({ lead, cells }) =>
          matchesAge(lead, age) &&
          (!community || lead.community?.id === community) &&
          (!group || lead.group?.id === group) &&
          (!broker || brokerOf(lead)?.id === broker) &&
          (fLead === 'all' || cells[0].key === fLead) &&
          (fQual === 'all' || cells[1].key === fQual) &&
          (fSale === 'all' || cells[2].key === fSale) &&
          (!q || lead.name.toLowerCase().includes(q) || (lead.broker_name ?? '').toLowerCase().includes(q)),
      )
  }, [leads, age, community, group, broker, fLead, fQual, fSale, search])

  function errorText(lead: MetaLeadProgress, event: MetaMilestone): string {
    const failure = lead.failures.find((f) => f.event_name === event)
    const code = failure?.error_code != null ? ` (code ${failure.error_code})` : ''
    return `${lead.name} — Facebook error on ${EVENT_LABEL[event]}${code}: ${
      failure?.error_message ?? 'no reason recorded'
    }`
  }

  const learned = qualifiedSent7d != null && qualifiedSent7d >= LEARNING_TARGET

  return (
    <AppShell section="Admin · Meta Quality">
      <div className="classic min-w-0">
        <div className="cl-paper cl-paper-wide">
          <Link to="/admin" className="cl-link cl-back">
            ← Admin
          </Link>

          <div className="cl-hdr">
            <div className="cl-name">META QUALITY</div>
            <div className="cl-learn">
              <span>
                Qualified sent to Meta, last 7 days:{' '}
                <b>
                  {qualifiedSent7d ?? '—'} / {LEARNING_TARGET}
                </b>
              </span>
              <progress
                className={learned ? 'cl-progress cl-progress-done' : 'cl-progress'}
                max={LEARNING_TARGET}
                value={Math.min(qualifiedSent7d ?? 0, LEARNING_TARGET)}
                aria-label="Qualified events sent in the last 7 days"
              />
              <span className="cl-dim">Meta needs about {LEARNING_TARGET} a week to finish learning.</span>
            </div>
          </div>

          {/* Only when something is wrong. A healthy connection is the normal
              state and needs no line of its own. */}
          {health && !health.token.ok && (
            <p role="alert" className="cl-alert">
              {health.token.reason ?? 'Meta is not accepting our events'}
            </p>
          )}
          {health && health.stuck_count > 0 && (
            <p role="alert" className="cl-alert">
              {health.stuck_count} waiting over an hour — the sender may be stopped
            </p>
          )}

          <div className="cl-strip">
            <div className="cl-strip-row">
              <span className="cl-strip-k">Show</span>
              <label>
                Community:{' '}
                <select
                  className="cl-fsel"
                  value={community}
                  onChange={(e) => setParam({ community: e.target.value, group: null, broker: null })}
                >
                  <option value="">All</option>
                  {communityOptions.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Group:{' '}
                <select
                  className="cl-fsel"
                  value={group}
                  onChange={(e) => setParam({ group: e.target.value, broker: null })}
                >
                  <option value="">All</option>
                  {groupOptions.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Broker:{' '}
                <select className="cl-fsel" value={broker} onChange={(e) => setParam({ broker: e.target.value })}>
                  <option value="">All</option>
                  {brokerOptions.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Age:{' '}
                <select className="cl-fsel" value={age} onChange={(e) => setParam({ age: e.target.value })}>
                  {AGES.map((a) => (
                    <option key={a.key} value={a.key}>
                      {a.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="cl-strip-row">
              <span className="cl-strip-k">Status</span>
              {(
                [
                  ['lead', 'Lead', fLead],
                  ['qual', 'Qualified', fQual],
                  ['sale', 'Sale', fSale],
                ] as const
              ).map(([param, label, value]) => (
                <label key={param}>
                  {label}:{' '}
                  <select className="cl-fsel" value={value} onChange={(e) => setParam({ [param]: e.target.value })}>
                    {COLUMN_FILTERS[param].map((o) => (
                      <option key={o.key} value={o.key}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
              {leads && (
                <span className="cl-dim">
                  {rows.length} of {leads.length} leads
                </span>
              )}
              <input
                className="cl-search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="search a name"
                aria-label="Search lead or broker"
              />
            </div>
          </div>

          {/* ONE PLACE for Facebook's reason, not a line under every row: the
              table stays the same height whatever is open. */}
          <div className={message ? 'cl-msg cl-msg-on' : 'cl-msg'} role="status" aria-live="polite">
            {message ? message.text : 'Click a red "Not sent" to see Facebook’s reason here.'}
          </div>

          <section className="cl-sec">
            <div className="cl-bar">
              <span>Leads</span>
              <span className="cl-bar-note">last {WINDOW_DAYS} days · newest first</span>
            </div>
            {loading ? (
              <div className="cl-empty">Loading…</div>
            ) : loadError && !leads ? (
              <div className="cl-empty">
                Could not load the conversions.{' '}
                <button type="button" className="cl-link" onClick={load}>
                  Try again
                </button>
              </div>
            ) : (
              <div className="cl-scroll">
                <table className="cl-table cl-table-list">
                  <thead>
                    <tr>
                      <th scope="col">Lead</th>
                      <th scope="col">Date</th>
                      <th scope="col">Broker</th>
                      <th scope="col">Lead</th>
                      <th scope="col">Qualified</th>
                      <th scope="col">Sale</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="cl-grey">
                          {leads?.length ? 'No leads match these filters.' : 'No Facebook leads in this period.'}
                        </td>
                      </tr>
                    ) : (
                      rows.map(({ lead, cells }) => (
                        <tr key={lead.lead_id}>
                          <td>
                            <b>{lead.name}</b>
                          </td>
                          <td>
                            {new Date(lead.arrived_at).toLocaleDateString('en-CA', { month: 'short', day: 'numeric' })}
                          </td>
                          <td className={lead.broker_name ? undefined : 'cl-grey'}>
                            {lead.broker_name ?? 'Unassigned'}
                          </td>
                          {cells.map((cell, i) => {
                            const key = `${lead.lead_id}:${i}`
                            if (cell.tone === 'error' && cell.event) {
                              const event = cell.event
                              return (
                                <td key={i}>
                                  <button
                                    type="button"
                                    className={message?.key === key ? 'cl-errbtn cl-errbtn-on' : 'cl-errbtn'}
                                    aria-expanded={message?.key === key}
                                    onClick={() => toggleMessage(key, errorText(lead, event))}
                                  >
                                    {cell.text}
                                  </button>
                                </td>
                              )
                            }
                            return (
                              <td key={i} className={cell.tone === 'grey' ? 'cl-grey' : cell.tone === 'bold' ? 'cl-b' : undefined}>
                                {cell.text}
                              </td>
                            )
                          })}
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <div className="cl-foot">
            <span>Read-only. All events go to the Auto Insurance dataset.</span>
            <Link to="/admin/capi/details" className="cl-link">
              Charts, field details and retry
            </Link>
          </div>
        </div>
      </div>
    </AppShell>
  )
}
