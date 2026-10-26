import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowDown, ArrowLeft, ArrowUp, ChevronDown, Search, X } from 'lucide-react'
import { toast } from 'sonner'
import { AppShell } from '@/components/AppShell'
import { useConfirm } from '@/components/ConfirmDialog'
import { CountTile } from '@/components/CountTile'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import {
  firstValidationMessage,
  getWebsiteLeadBoard,
  updateWebsiteBroker,
  updateWebsiteBrokers,
  type WebsiteBroker,
  type WebsiteLeadBoard,
} from '@/data/api'
import { cn } from '@/lib/utils'

// Who receives leads from the website's quote form, and how many a day.
//
// A ROUTING SYSTEM OF ITS OWN (migration 0127). Website leads ignore groups:
// they go in turn to every broker switched on here, whatever group they are
// in. Community and group are for finding people, not for routing.
//
// THE LIMIT IS SEPARATE from the broker's Facebook daily cap. Neither counts
// the other's leads, so raising one never takes anything from the other.
//
// Nobody is switched on until an admin does it: a broker with no setting yet
// is off.

// The server's own bound (schemas/website_lead.py). Mirrored here so a typo
// of 250 is refused on the spot rather than round-tripped to a 422.
const MAX_CAP = 200

// While submissions are waiting, the numbers on this screen change on their
// own — the drain runs after every save and every 60 s on the server. A slow
// poll keeps "waiting" honest without a refresh button.
const POLL_WHILE_WAITING_MS = 5000

// How long "Saved" stays beside a limit that has just been stored.
const SAVED_SIGN_MS = 2000

// Typing settles for this long before the search reaches the URL — a router
// navigation per keystroke makes the box lag.
const SEARCH_DEBOUNCE_MS = 300

type Change = { is_on?: boolean; daily_cap?: number }
type Field = keyof Change

// One save in flight per broker PER FIELD, keyed "brokerId:field". Per field,
// not per broker: clicking the switch right after typing a limit blurs the
// input first, and a per-broker guard would swallow that click while the
// limit saved — the admin would have to click twice and might not notice.
const pendingKey = (id: string, field: Field) => `${id}:${field}`

// A board row with its community in a shape that is never null. The board
// sends both since migration 0129; before it, every broker lands in one
// nameless community rather than the screen breaking.
type Row = WebsiteBroker & { community: string; communityKey: string }

// In the order an admin scans for trouble: who is taking leads, who is on but
// not taking any and why, then who is simply off.
const STATUSES = [
  'Taking leads',
  'Limit reached',
  'Limit is 0',
  'Not started',
  'Paused',
  'Blocked',
  'Inactive',
  'Off',
] as const
type Status = (typeof STATUSES)[number]

const SORT_KEYS = ['name', 'community', 'group', 'daily_cap', 'today', 'status'] as const
type SortKey = (typeof SORT_KEYS)[number]
type Sort = { key: SortKey; desc: boolean }

// Community, then group, then name — the order the admin already knows from
// the Broker groups screen.
const DEFAULT_SORT: Sort = { key: 'community', desc: false }

// "today" ascending, "-today" descending; anything else is the default.
function parseSort(raw: string | null): Sort {
  if (!raw) return DEFAULT_SORT
  const desc = raw.startsWith('-')
  const key = (desc ? raw.slice(1) : raw) as SortKey
  return SORT_KEYS.includes(key) ? { key, desc } : DEFAULT_SORT
}

// Why a broker is or is not taking website leads right now, in words — every
// rule the drain (0128) applies before it gives someone a lead. A rule added
// there must be added here, or this screen will call a broker "Taking leads"
// while the drain passes them over.
function statusOf(b: WebsiteBroker): Status {
  if (!b.is_active) return 'Inactive'
  if (b.is_blocked_by_admin) return 'Blocked'
  if (b.self_paused) return 'Paused'
  if (!b.is_on) return 'Off'
  if (b.eligible_from && new Date(b.eligible_from).getTime() > Date.now()) return 'Not started'
  if (b.daily_cap === 0) return 'Limit is 0'
  if (b.today >= b.daily_cap) return 'Limit reached'
  return 'Taking leads'
}

function compare(a: Row, b: Row, key: SortKey): number {
  switch (key) {
    case 'name':
      return a.name.localeCompare(b.name)
    case 'community':
      return a.community.localeCompare(b.community)
    case 'group':
      return (a.group_name ?? '').localeCompare(b.group_name ?? '')
    case 'daily_cap':
      return a.daily_cap - b.daily_cap
    case 'today':
      return a.today - b.today
    case 'status':
      return STATUSES.indexOf(statusOf(a)) - STATUSES.indexOf(statusOf(b))
  }
}

function setMulti(p: URLSearchParams, key: string, values: Iterable<string>) {
  p.delete(key)
  for (const v of values) p.append(key, v)
}

export default function WebsiteLeads() {
  const confirm = useConfirm()
  const [board, setBoard] = useState<WebsiteLeadBoard | null>(null)
  const [loading, setLoading] = useState(true)
  // A failed load is NOT an empty list. "No brokers yet" on a 401 or a 500
  // would be a false statement; this keeps the two apart.
  const [loadError, setLoadError] = useState(false)
  // Brokers with a save in flight. Their controls are disabled so a second
  // click cannot race the first — two PATCHes finishing out of order would
  // leave the screen showing a state nobody chose.
  const [pending, setPending] = useState<Set<string>>(() => new Set())
  const [retrying, setRetrying] = useState(false)
  // Brokers whose limit was stored a moment ago, for the "Saved" sign.
  const [justSaved, setJustSaved] = useState<Set<string>>(() => new Set())
  // A bulk change in flight. Every control on the screen is held while it
  // runs.
  const [bulk, setBulk] = useState(false)
  const [bulkCap, setBulkCap] = useState('')
  const alive = useRef(true)
  const loadInFlight = useRef(false)

  // THE FILTERS AND THE SORT LIVE IN THE URL, as on the All leads screen: a
  // refresh keeps them and a filtered view can be bookmarked or sent to
  // someone. replace, not push — ticking five filters should not need five
  // presses of Back to leave the screen.
  //
  // Several values at once per filter; none ticked means "all".
  const [params, setParams] = useSearchParams()
  const search = params.get('q') ?? ''
  const communitiesPicked = useMemo(() => new Set(params.getAll('community')), [params])
  const groupsPicked = useMemo(() => new Set(params.getAll('group')), [params])
  const switchPicked = useMemo(() => new Set(params.getAll('switch')), [params])
  const statusPicked = useMemo(() => new Set(params.getAll('status')), [params])
  const sort = useMemo(() => parseSort(params.get('sort')), [params])

  // ONE write per change. Two setParams calls in a row would each start from
  // the same old URL and the second would undo the first.
  //
  // Read through a ref, not the render's `params`: the debounced search
  // below writes from a timer set several renders ago, and starting from that
  // render's URL would undo any filter ticked since.
  const paramsRef = useRef(params)
  paramsRef.current = params
  const setParamsRef = useRef(setParams)
  setParamsRef.current = setParams
  const updateParams = useCallback((mutate: (p: URLSearchParams) => void) => {
    const next = new URLSearchParams(paramsRef.current)
    mutate(next)
    paramsRef.current = next
    setParamsRef.current(next, { replace: true })
  }, [])

  // THE SEARCH BOX IS LOCAL, and reaches the URL once typing settles — the
  // same arrangement as the All leads screen. lastWritten tells this screen's
  // own write apart from the URL changing underneath it (a chip removed, Clear
  // all, Back): only the second kind may overwrite what is in the box, or a
  // letter typed just as the write lands would be lost.
  const [searchDraft, setSearchDraft] = useState(search)
  const lastWritten = useRef(search)
  useEffect(() => {
    if (search === lastWritten.current) return
    lastWritten.current = search
    setSearchDraft(search)
  }, [search])
  useEffect(() => {
    if (searchDraft === lastWritten.current) return
    const timer = window.setTimeout(() => {
      lastWritten.current = searchDraft
      updateParams((p) => (searchDraft ? p.set('q', searchDraft) : p.delete('q')))
    }, SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [searchDraft, updateParams])

  const load = useCallback(async () => {
    // Never two at once: a poll slower than its interval must not stack.
    if (loadInFlight.current) return
    loadInFlight.current = true
    try {
      const next = await getWebsiteLeadBoard()
      if (!alive.current) return
      setBoard(next)
      setLoadError(false)
    } catch {
      if (!alive.current) return
      // Recorded unconditionally; the error PANEL renders only when there is
      // no board yet (`loadError && !board`). Once a board is on screen, one
      // failed background poll must not tear the table down — the switches
      // are the admin's controls, and they still work.
      setLoadError(true)
      toast.error('Could not load the website lead settings')
    } finally {
      loadInFlight.current = false
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    alive.current = true
    // loading is cleared inside load() itself, by whichever call actually
    // fetched — StrictMode's second mount call returns at the in-flight
    // guard, and clearing it there would show "No brokers yet" too early.
    load()
    return () => {
      alive.current = false
    }
  }, [load])

  // The poll: only while something is waiting. Keyed on the boolean, not
  // the board, so a new board does not restart the timer; and paused while
  // a save is in flight, so a poll cannot land between an optimistic set and
  // the server's answer and flip the switch back for a moment.
  const polling = (board?.waiting ?? 0) > 0 && pending.size === 0 && !bulk
  useEffect(() => {
    if (!polling) return
    const id = window.setInterval(load, POLL_WHILE_WAITING_MS)
    return () => window.clearInterval(id)
  }, [polling, load])

  function patchRow(id: string, fields: Partial<WebsiteBroker>) {
    setBoard((b) =>
      b && { ...b, brokers: b.brokers.map((x) => (x.broker_id === id ? { ...x, ...fields } : x)) },
    )
  }

  function showSaved(id: string) {
    setJustSaved((s) => new Set(s).add(id))
    window.setTimeout(() => {
      if (!alive.current) return
      setJustSaved((s) => {
        const next = new Set(s)
        next.delete(id)
        return next
      })
    }, SAVED_SIGN_MS)
  }

  async function save(broker: WebsiteBroker, change: Change) {
    const id = broker.broker_id
    const field: Field = 'is_on' in change ? 'is_on' : 'daily_cap'
    const key = pendingKey(id, field)
    if (pending.has(key)) return
    setPending((p) => new Set(p).add(key))

    // Optimistic, so the switch moves under the finger. On failure ONLY THIS
    // ROW'S FIELD is put back, from the value it showed at click time — never
    // the whole board, which by then may carry another broker's successful
    // save or a fresher count from the poll.
    patchRow(id, change)

    try {
      const saved = await updateWebsiteBroker(id, change)
      if (!alive.current) return
      // The server's answer for THIS FIELD is authoritative; take it rather
      // than trusting the optimistic guess. Only this field: the response
      // carries both, and writing the other back would flip a switch the
      // admin changed while this save was in flight.
      patchRow(id, field === 'is_on' ? { is_on: saved.is_on } : { daily_cap: saved.daily_cap })
      // The switch shows its own result by moving. A typed limit looks the
      // same stored or not, so it is the one that says so.
      if (field === 'daily_cap') showSaved(id)
      // The drain runs after the save's response, so a switch-on can release
      // waiting submissions now. Re-read to show that; the poll above keeps
      // following if the drain takes longer than this.
      window.setTimeout(() => alive.current && load(), 800)
    } catch (err) {
      if (!alive.current) return
      // Spelled out per field so the compiler checks the type; a computed
      // key would widen to an index signature it cannot check.
      patchRow(id, field === 'is_on' ? { is_on: broker.is_on } : { daily_cap: broker.daily_cap })
      toast.error(firstValidationMessage(err) ?? `Could not save ${broker.name}`)
    } finally {
      if (alive.current) {
        setPending((p) => {
          const next = new Set(p)
          next.delete(key)
          return next
        })
      }
    }
  }

  async function retry() {
    setRetrying(true)
    await load()
    if (alive.current) setRetrying(false)
  }

  const rows: Row[] = useMemo(
    () =>
      (board?.brokers ?? []).map((b) => ({
        ...b,
        community: b.community_name ?? '—',
        communityKey: b.community_id ?? '',
      })),
    [board],
  )

  // The dropdowns offer only what is on the board. Communities and groups are
  // both keyed by id, never by name: two groups can share a name, and a
  // renamed community must not break a bookmarked filter. Groups narrow to
  // the chosen communities.
  const communityOptions = useMemo(() => {
    const seen = new Map(rows.map((r) => [r.communityKey, r.community]))
    return [...seen].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label))
  }, [rows])
  const groupOptions = useMemo(() => {
    const seen = new Map<string, string>()
    for (const r of rows) {
      if (communitiesPicked.size && !communitiesPicked.has(r.communityKey)) continue
      // With more than one community in play the same group name can appear
      // twice, so the community is said too.
      const name = r.group_name ?? 'No group name'
      seen.set(r.group_id, communitiesPicked.size === 1 ? name : `${name} · ${r.community}`)
    }
    return [...seen].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label))
  }, [rows, communitiesPicked])

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    const filtered = rows.filter(
      (r) =>
        // The search box finds a broker by any of the three things an admin
        // might remember them by.
        (!q ||
          r.name.toLowerCase().includes(q) ||
          (r.group_name ?? '').toLowerCase().includes(q) ||
          r.community.toLowerCase().includes(q)) &&
        (!communitiesPicked.size || communitiesPicked.has(r.communityKey)) &&
        (!groupsPicked.size || groupsPicked.has(r.group_id)) &&
        // On and Off both ticked is everyone, the same as neither.
        (switchPicked.size !== 1 || r.is_on === switchPicked.has('on')) &&
        (!statusPicked.size || statusPicked.has(statusOf(r))),
    )
    const dir = sort.desc ? -1 : 1
    // The chosen column first; community, group, name settle the ties so the
    // order is the same every time.
    return filtered.sort(
      (a, b) =>
        dir * compare(a, b, sort.key) ||
        compare(a, b, 'community') ||
        compare(a, b, 'group') ||
        compare(a, b, 'name'),
    )
  }, [rows, search, communitiesPicked, groupsPicked, switchPicked, statusPicked, sort])

  function pickCommunities(next: Set<string>) {
    updateParams((p) => {
      setMulti(p, 'community', next)
      // Drop any ticked group that is no longer on offer: a group from an
      // unticked community would leave the table empty for no visible reason.
      if (next.size) {
        const allowed = new Set(rows.filter((r) => next.has(r.communityKey)).map((r) => r.group_id))
        setMulti(p, 'group', [...groupsPicked].filter((id) => allowed.has(id)))
      }
    })
  }

  function toggleSort(key: SortKey) {
    const next: Sort = sort.key === key ? { key, desc: !sort.desc } : { key, desc: false }
    updateParams((p) => {
      // The default order is the absence of the parameter, so a plain link to
      // this screen and a "sorted back to normal" one are the same URL.
      if (next.key === DEFAULT_SORT.key && !next.desc) p.delete('sort')
      else p.set('sort', (next.desc ? '-' : '') + next.key)
    })
  }

  function clearFilters() {
    updateParams((p) => {
      for (const key of ['q', 'community', 'group', 'switch', 'status']) p.delete(key)
    })
  }

  // Every active filter, spelled out with its own way to remove it. "2
  // selected" on a button says how many, not which.
  const groupLabel = new Map(rows.map((r) => [r.group_id, r.group_name ?? 'No group name']))
  const communityLabel = new Map(rows.map((r) => [r.communityKey, r.community]))
  const without = (set: Set<string>, value: string) => new Set([...set].filter((v) => v !== value))
  const chips: { key: string; label: string; remove: () => void }[] = [
    ...(search
      ? [{ key: 'q', label: `"${search}"`, remove: () => updateParams((p) => p.delete('q')) }]
      : []),
    ...[...communitiesPicked].map((id) => ({
      key: `community:${id}`,
      label: communityLabel.get(id) ?? 'Unknown community',
      remove: () => pickCommunities(without(communitiesPicked, id)),
    })),
    ...[...groupsPicked].map((id) => ({
      key: `group:${id}`,
      label: groupLabel.get(id) ?? 'Unknown group',
      remove: () => updateParams((p) => setMulti(p, 'group', without(groupsPicked, id))),
    })),
    ...[...switchPicked].map((s) => ({
      key: `switch:${s}`,
      label: s === 'on' ? 'Switched on' : 'Switched off',
      remove: () => updateParams((p) => setMulti(p, 'switch', without(switchPicked, s))),
    })),
    ...[...statusPicked].map((s) => ({
      key: `status:${s}`,
      label: s,
      remove: () => updateParams((p) => setMulti(p, 'status', without(statusPicked, s))),
    })),
  ]
  const anyFilter = chips.length > 0

  // The totals are for the WHOLE board, never the filtered view: they answer
  // "how much can the website hand out today", which a filter must not change.
  const on = rows.filter((r) => r.is_on)
  const limitToday = on.reduce((sum, r) => sum + r.daily_cap, 0)
  const deliveredToday = rows.reduce((sum, r) => sum + r.today, 0)
  // Only brokers who can take a lead this minute. A paused broker's unused
  // limit is not capacity anyone can use.
  const taking = rows.filter((r) => statusOf(r) === 'Taking leads')
  const leftToday = taking.reduce((sum, r) => sum + (r.daily_cap - r.today), 0)
  // Leads are waiting and nobody can take them. Without this line the screen
  // looks calm while customers sit unanswered.
  const stuck = (board?.waiting ?? 0) > 0 && taking.length === 0

  // ── Changing everyone shown, at once ──────────────────────────────────
  //
  // ONE REQUEST, all or nothing (migration 0129). It used to be one save per
  // broker in a loop here, and a session that expired halfway left some
  // brokers changed and some not, with nothing on screen saying which.
  // Only brokers whose value would actually change are sent.
  async function runBulk(change: Change) {
    const field: Field = 'is_on' in change ? 'is_on' : 'daily_cap'
    const targets = visible.filter((r) => r[field] !== change[field])
    const what = field === 'is_on' ? (change.is_on ? 'switched on' : 'switched off') : `set to ${change.daily_cap} a day`
    if (targets.length === 0) {
      toast(`Everyone shown is already ${what}`)
      return
    }
    const people = `${targets.length} broker${targets.length === 1 ? '' : 's'}`
    const ok = await confirm({
      title:
        field === 'is_on'
          ? `Switch website leads ${change.is_on ? 'on' : 'off'} for ${people}?`
          : `Set the website limit to ${change.daily_cap} a day for ${people}?`,
      description: anyFilter
        ? 'This changes every broker in the filtered list below.'
        : 'No filter is set, so this changes every broker.',
      confirmText: field === 'is_on' ? (change.is_on ? 'Switch on' : 'Switch off') : 'Set limit',
    })
    if (!ok) return

    setBulk(true)
    try {
      const saved = await updateWebsiteBrokers(
        targets.map((t) => t.broker_id),
        change,
      )
      if (!alive.current) return
      // The server's rows, not the request: what it stored is what shows.
      // Only the field that was sent, as in save() above.
      const byId = new Map(saved.map((s) => [s.broker_id, s]))
      setBoard(
        (b) =>
          b && {
            ...b,
            brokers: b.brokers.map((x) => {
              const s = byId.get(x.broker_id)
              if (!s) return x
              return field === 'is_on' ? { ...x, is_on: s.is_on } : { ...x, daily_cap: s.daily_cap }
            }),
          },
      )
      if (field === 'daily_cap') setBulkCap('')
      toast(`${people} ${what}`)
      // As after a single save: the drain runs after the response.
      window.setTimeout(() => alive.current && load(), 800)
    } catch (err) {
      if (!alive.current) return
      // Nothing was changed for anyone, and nothing on screen was moved
      // ahead of the answer, so there is nothing to put back.
      toast.error(firstValidationMessage(err) ?? 'Could not save — nobody was changed')
    } finally {
      if (alive.current) setBulk(false)
    }
  }

  function applyBulkCap() {
    const n = Number(bulkCap)
    if (bulkCap.trim() === '' || !Number.isInteger(n) || n < 0 || n > MAX_CAP) {
      toast.error(`Enter a limit from 0 to ${MAX_CAP}`)
      return
    }
    runBulk({ daily_cap: n })
  }

  return (
    <AppShell section="Admin · Website leads">
      <div className="mx-auto max-w-6xl space-y-6 p-6">
        <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
          <Link to="/admin">
            <ArrowLeft /> Admin
          </Link>
        </Button>

        <div>
          <h1 className="text-2xl font-semibold">Website leads</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Leads from the website go in turn to the brokers switched on here, whatever
            their group. The limit is separate from their Facebook limit.
          </p>
        </div>

        {stuck && board && (
          <p role="alert" className="bg-card rounded border p-4 text-sm font-semibold">
            {board.waiting} lead{board.waiting === 1 ? ' is' : 's are'} waiting and nobody is free to
            take {board.waiting === 1 ? 'it' : 'them'}.{' '}
            <span className="text-muted-foreground font-normal">
              Switch a broker on, or raise a limit, and {board.waiting === 1 ? 'it goes' : 'they go'} out
              straight away.
            </span>
          </p>
        )}

        {/* The day at a glance. Waiting is ordinary (everyone on is busy);
            "could not be imported" needs a person. */}
        {board && (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <CountTile
              title="Switched on"
              subtitle={`of ${rows.length} brokers`}
              count={on.length}
              onClick={() =>
                updateParams((p) =>
                  setMulti(p, 'switch', switchPicked.size === 1 && switchPicked.has('on') ? [] : ['on']),
                )
              }
            />
            <CountTile title="Left today" subtitle={`of a limit of ${limitToday}`} count={leftToday} />
            <CountTile title="Delivered today" subtitle="since the daily reset" count={deliveredToday} />
            <CountTile title="Waiting" subtitle="for a free broker" count={board.waiting} />
            <CountTile title="Could not be imported" subtitle="needs a person" count={board.failed} />
          </div>
        )}

        {board && board.brokers.length > 0 && (
          <div className="bg-card space-y-2.5 rounded border p-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <div className="border-input text-muted-foreground flex min-w-48 flex-1 items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-sm">
                <Search className="size-4" />
                <input
                  value={searchDraft}
                  onChange={(e) => setSearchDraft(e.target.value)}
                  placeholder="Search broker, group or community…"
                  aria-label="Search broker, group or community"
                  className="text-foreground min-w-0 flex-1 bg-transparent outline-none"
                />
              </div>
              <MultiFilter
                label="Community"
                picked={communitiesPicked}
                onChange={pickCommunities}
                options={communityOptions}
              />
              <MultiFilter
                label="Group"
                picked={groupsPicked}
                onChange={(next) => updateParams((p) => setMulti(p, 'group', next))}
                options={groupOptions}
              />
              <MultiFilter
                label="Switch"
                picked={switchPicked}
                onChange={(next) => updateParams((p) => setMulti(p, 'switch', next))}
                options={[
                  { value: 'on', label: 'Switched on' },
                  { value: 'off', label: 'Switched off' },
                ]}
              />
              <MultiFilter
                label="Status"
                picked={statusPicked}
                onChange={(next) => updateParams((p) => setMulti(p, 'status', next))}
                options={STATUSES.map((s) => ({ value: s, label: s }))}
              />
            </div>

            {/* ALWAYS A LINE, even with nothing to say. Appearing on the first
                tick, it would push the table down under the admin's cursor. */}
            <div className="flex min-h-7 flex-wrap items-center gap-1.5 text-xs">
              <span className="text-muted-foreground">Filters:</span>
              {!anyFilter && <span className="text-muted-foreground">none — showing everyone</span>}
              {chips.map((c) => (
                <span key={c.key} className="bg-secondary inline-flex items-center gap-1 rounded-full py-0.5 pr-1 pl-2.5">
                  {c.label}
                  <button
                    type="button"
                    onClick={c.remove}
                    aria-label={`Remove filter ${c.label}`}
                    className="text-muted-foreground hover:text-foreground rounded-full p-0.5"
                  >
                    <X className="size-3" />
                  </button>
                </span>
              ))}
              {anyFilter && (
                <button
                  type="button"
                  onClick={clearFilters}
                  className="text-muted-foreground hover:text-foreground ml-1 underline"
                >
                  Clear all
                </button>
              )}
            </div>
          </div>
        )}

        <div className="bg-card overflow-x-auto rounded border">
          {loading ? (
            <p className="text-muted-foreground p-8 text-center text-sm">Loading…</p>
          ) : loadError && !board ? (
            <div className="space-y-3 p-8 text-center text-sm">
              <p className="text-muted-foreground">Could not load the website lead settings.</p>
              <Button variant="outline" size="sm" onClick={retry} disabled={retrying}>
                {retrying ? 'Trying…' : 'Try again'}
              </Button>
            </div>
          ) : !board?.brokers.length ? (
            <p className="text-muted-foreground p-8 text-center text-sm">No brokers yet.</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2.5 text-sm">
                <div className="font-semibold">
                  Brokers{' '}
                  <span className="text-muted-foreground font-normal">
                    ({anyFilter ? `${visible.length} of ${rows.length}` : rows.length})
                  </span>
                </div>

                {/* Acts on exactly the rows in the table below — the filter is
                    how the admin chooses who. */}
                <div className="ml-auto flex flex-wrap items-center gap-2">
                  <span className="text-muted-foreground">
                    {bulk
                      ? 'Saving…'
                      : `For ${visible.length === rows.length ? 'all' : 'the'} ${visible.length} shown:`}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={bulk || visible.length === 0}
                    onClick={() => runBulk({ is_on: true })}
                  >
                    Switch on
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={bulk || visible.length === 0}
                    onClick={() => runBulk({ is_on: false })}
                  >
                    Switch off
                  </Button>
                  <Input
                    type="number"
                    min={0}
                    max={MAX_CAP}
                    step={1}
                    value={bulkCap}
                    disabled={bulk || visible.length === 0}
                    onChange={(e) => setBulkCap(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && applyBulkCap()}
                    placeholder="Limit"
                    className="h-(--height-control) w-20"
                    aria-label="Limit a day for everyone shown"
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={bulk || visible.length === 0 || bulkCap.trim() === ''}
                    onClick={applyBulkCap}
                  >
                    Set limit
                  </Button>
                </div>
              </div>

              {/* table-fixed with set widths: left to size themselves, the
                  columns would re-measure against whichever rows a filter
                  leaves and slide about. Broker takes what is left. */}
              <table className="w-full min-w-240 table-fixed border-collapse text-sm">
                <thead>
                  <tr className="bg-secondary/60 text-muted-foreground border-b text-left text-xs tracking-wide uppercase">
                    <Th sort={sort} sortKey="name" onSort={toggleSort}>Broker</Th>
                    <Th sort={sort} sortKey="community" onSort={toggleSort} className="w-36">Community</Th>
                    <Th sort={sort} sortKey="group" onSort={toggleSort} className="w-40">Group</Th>
                    <Th className="w-40">Website leads</Th>
                    <Th sort={sort} sortKey="daily_cap" onSort={toggleSort} className="w-40">Limit a day</Th>
                    <Th sort={sort} sortKey="today" onSort={toggleSort} right className="w-24">Today</Th>
                    <Th sort={sort} sortKey="status" onSort={toggleSort} className="w-36">Status</Th>
                  </tr>
                </thead>
                <tbody>
                  {visible.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="text-muted-foreground p-8 text-center">
                        No brokers match these filters.
                      </td>
                    </tr>
                  ) : (
                    visible.map((b) => (
                      <BrokerRow
                        key={b.broker_id}
                        broker={b}
                        switchBusy={bulk || pending.has(pendingKey(b.broker_id, 'is_on'))}
                        capBusy={bulk || pending.has(pendingKey(b.broker_id, 'daily_cap'))}
                        capSaved={justSaved.has(b.broker_id)}
                        onSave={save}
                      />
                    ))
                  )}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </AppShell>
  )
}

function BrokerRow({
  broker,
  switchBusy,
  capBusy,
  capSaved,
  onSave,
}: {
  broker: Row
  switchBusy: boolean
  capBusy: boolean
  capSaved: boolean
  onSave: (b: WebsiteBroker, change: Change) => void
}) {
  // The limit is typed, so it saves on blur or Enter — never per keystroke,
  // where "12" can arrive as "1" then "12" in either order.
  const [cap, setCap] = useState(String(broker.daily_cap))
  useEffect(() => setCap(String(broker.daily_cap)), [broker.daily_cap])

  function commitCap() {
    const n = Number(cap)
    const valid = cap.trim() !== '' && Number.isInteger(n) && n >= 0 && n <= MAX_CAP
    if (!valid) {
      if (cap.trim() !== '' && Number.isInteger(n) && n > MAX_CAP) {
        toast.error(`The limit can be at most ${MAX_CAP} a day`)
      }
      setCap(String(broker.daily_cap))
      return
    }
    // "05" is 5: tidy the box even when nothing is saved.
    setCap(String(n))
    if (n !== broker.daily_cap) onSave(broker, { daily_cap: n })
  }

  // Said, not coloured: the one state that is actually receiving leads gets
  // the weight, everything else is quiet.
  const status = statusOf(broker)

  return (
    <tr className="hover:bg-accent/45 border-b last:border-b-0">
      <td className="truncate px-4 py-2.5 font-medium">{broker.name}</td>
      <td className="truncate px-4 py-2.5">{broker.community}</td>
      <td className="truncate px-4 py-2.5">
        <Link to={`/admin/broker-groups/${broker.group_id}`} className="hover:underline">
          {broker.group_name ?? 'No group name'}
        </Link>
      </td>
      <td className="px-4 py-2.5">
        <Switch
          checked={broker.is_on}
          disabled={switchBusy}
          onCheckedChange={(on) => onSave(broker, { is_on: on })}
          aria-label={`Website leads for ${broker.name}`}
        />
      </td>
      <td className="px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Input
            type="number"
            min={0}
            max={MAX_CAP}
            step={1}
            value={cap}
            disabled={capBusy}
            onChange={(e) => setCap(e.target.value)}
            onBlur={commitCap}
            onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
            className="h-(--height-control) w-20"
            aria-label={`Website leads a day for ${broker.name}`}
          />
          {/* Its space is always held, so the row does not shift when it
              shows. */}
          <span role="status" className={cn('text-muted-foreground text-xs', !capSaved && 'invisible')}>
            Saved
          </span>
        </div>
      </td>
      <td className={cn('px-4 py-2.5 text-right tabular-nums', !broker.today && 'text-muted-foreground')}>
        {broker.today} / {broker.daily_cap}
      </td>
      <td
        className={cn(
          'px-4 py-2.5 whitespace-nowrap',
          status === 'Taking leads' ? 'font-medium' : 'text-muted-foreground',
        )}
      >
        {status}
      </td>
    </tr>
  )
}

// A header with sortKey is a button: first click sorts A→Z / low→high, the
// second reverses it. The arrow shows only on the column in charge.
function Th({
  children,
  right,
  sort,
  sortKey,
  onSort,
  className,
}: {
  children?: React.ReactNode
  right?: boolean
  sort?: Sort
  sortKey?: SortKey
  onSort?: (key: SortKey) => void
  className?: string
}) {
  if (!sortKey || !sort || !onSort) {
    return (
      <th scope="col" className={cn('px-4 py-2.5 font-semibold', right && 'text-right', className)}>
        {children}
      </th>
    )
  }
  const active = sort.key === sortKey
  const Arrow = sort.desc ? ArrowDown : ArrowUp
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.desc ? 'descending' : 'ascending') : 'none'}
      className={cn('px-4 py-2.5 font-semibold', right && 'text-right', className)}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          'hover:text-foreground inline-flex items-center gap-1 tracking-wide uppercase',
          active && 'text-foreground',
        )}
      >
        {children}
        <Arrow className={cn('size-3', !active && 'invisible')} />
      </button>
    </th>
  )
}

// Tick as many as wanted; the menu stays open between ticks so several can
// be chosen in one go. Every filter on the bar is this one control, so they
// look and behave the same.
function MultiFilter({
  label,
  picked,
  onChange,
  options,
}: {
  label: string
  picked: Set<string>
  onChange: (next: Set<string>) => void
  options: { value: string; label: string }[]
}) {
  const chosen = options.filter((o) => picked.has(o.value))
  // "2 of 5", not "2 selected": the button is a fixed width and the longer
  // wording was cut to "2 sel…" beside a label as long as Community. Which
  // two is spelled out on the Filters line underneath.
  const summary =
    chosen.length === 0
      ? 'All'
      : chosen.length === 1
        ? chosen[0].label
        : `${chosen.length} of ${options.length}`

  function toggle(value: string, on: boolean) {
    const next = new Set(picked)
    if (on) next.add(value)
    else next.delete(value)
    onChange(next)
  }

  return (
    // modal={false}: a modal menu locks page scroll while open, which removes
    // the scrollbar and nudges the whole page sideways.
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        {/* A FIXED width, with the summary truncated inside it. Sized to its
            text, the button would grow and shrink with every tick and push
            everything beside it along the bar. */}
        <button
          type="button"
          className="border-input flex w-44 items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-left text-sm"
        >
          <span className="text-muted-foreground">{label}</span>
          <span className="min-w-0 flex-1 truncate">{summary}</span>
          <ChevronDown className="text-muted-foreground size-3.5 shrink-0" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-72 overflow-y-auto">
        {options.map((o) => (
          <DropdownMenuCheckboxItem
            key={o.value}
            checked={picked.has(o.value)}
            onCheckedChange={(on) => toggle(o.value, on)}
            // Radix closes the menu on select by default.
            onSelect={(e) => e.preventDefault()}
          >
            {o.label}
          </DropdownMenuCheckboxItem>
        ))}
        {chosen.length > 0 && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => onChange(new Set())} className="text-muted-foreground">
              Clear selection
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
