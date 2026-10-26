import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Ban,
  BadgeCheck,
  Check,
  ChevronRight,
  CircleDashed,
  CircleDot,
  FileCheck,
  FileText,
  Handshake,
  Hourglass,
  Layers,
  Monitor,
  Moon,
  Sparkles,
  Sun,
} from 'lucide-react'
import { LEAD_STAGES } from '@/domain/leadStages'
import { getMyDay, type MyDay } from '@/data/api'
import { useTheme, type Theme } from '@/lib/theme'

// WIREFRAME — the mobile companion's landing screen. Placeholder data.
//
// THE SAME LANGUAGE AS THE LEADS SCREEN, deliberately: grouped cards on a grey
// page, 17pt rows, inset separators, tint not fill. The desktop My Day is a
// dashboard of bordered tiles in a 2-column grid — squeezed onto a phone it
// reads as a different app from the list it leads into, which is exactly what
// this replaces.
//
// EVERY ROW IS A DRILL-DOWN. A count on this screen is only useful as a way
// into the leads behind it, so the whole screen is a set of doors into
// /m/leads with a filter already applied. Nothing here is decoration.

// Same interval and reasoning as Leads.tsx's live inbox poll: leads arrive on
// Meta's own schedule, and this screen's whole job is showing "New today" the
// moment a broker opens the app — a stale count here is worse than on the
// list, since it is the first thing checked. Paused while the tab is hidden,
// resumed with an immediate refetch on return.
const POLL_INTERVAL_MS = 20_000

const STAGE_ICONS: Record<string, typeof CircleDot> = {
  New: CircleDot,
  'In Progress': CircleDashed,
  'Quote Done': FileText,
  Pending: Hourglass,
  Sold: Handshake,
  Submitted: FileCheck,
  Issued: BadgeCheck,
  Lost: Ban,
}

function greeting(name: string) {
  const h = new Date().getHours()
  const g = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
  // First name only — the line is a greeting, not an identification.
  return `${g}, ${name.split(' ')[0]}`
}

export default function MobileMyDay() {
  const navigate = useNavigate()
  const [theme, setTheme] = useTheme()
  const [data, setData] = useState<MyDay | null>(null)
  // A failed load is NOT an empty day. Without this the screen would show a
  // confident set of zeros to a broker whose connection blipped — the same
  // reasoning as the desktop My Day.
  const [failed, setFailed] = useState(false)

  // silent: a background poll tick must not clobber `data` back to null and
  // flash the skeleton — only the very first load (data still null) does that.
  const load = useCallback((opts?: { silent?: boolean }) => {
    return getMyDay()
      .then((d) => {
        setData(d)
        setFailed(false)
      })
      .catch(() => {
        if (!opts?.silent) setFailed(true)
      })
  }, [])

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

  // Every row lands on the leads list with a filter already applied. `stage`
  // omitted means all of them.
  const go = (stage?: string) =>
    navigate(stage ? `/m/leads?stage=${encodeURIComponent(stage)}` : '/m/leads')

  if (failed) {
    return (
      <div className="bg-background min-h-dvh px-6 pt-[calc(4rem+env(safe-area-inset-top))] text-center">
        <p className="text-[17px]">Couldn't load your day</p>
        <p className="text-muted-foreground pt-1 text-[15px]">
          Check your connection and try again.
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="text-primary min-h-[44px] px-4 pt-4 text-[17px]"
        >
          Retry
        </button>
      </div>
    )
  }

  // SKELETON, not a spinner: the shape is known, so showing it settles the
  // layout before the numbers land instead of shifting everything when they do.
  const attention = data?.attention
  const counts = data?.counts ?? {}
  const loading = !data

  return (
    // min-h-dvh, not min-h-screen: vh on mobile Safari is the viewport with the
    // URL bar collapsed, so the page carries a scroll it does not need.
    <div className="bg-background min-h-dvh pb-8">
      {/* iOS large title. Left-aligned and big, never centred in a bar — and
          it is the whole header, because a phone landing screen does not need
          furniture above the content it exists to show. */}
      {/* The top inset is ADDED to the padding rather than replacing it: in a
          browser tab it resolves to 0 and this stays pt-4, but installed to
          the home screen the page runs under the status bar (see
          apple-mobile-web-app-status-bar-style) and the greeting would sit
          behind the clock without it. */}
      <header className="mx-auto max-w-[var(--width-mobile)] px-4 pt-[calc(1rem+env(safe-area-inset-top))] pb-1">
        {/* A SKELETON, not a blank line. This rendered a non-breaking space
            while loading, so the screen opened with an empty band and the
            greeting appeared a beat later at 34px — the biggest text on the
            page arriving last, which reads as the app stuttering rather than
            loading. The bar holds the same height and pulses like the counts
            below it, so the whole screen resolves as one thing. */}
        {data ? (
          <h1 className="text-[34px] leading-tight font-bold">
            {greeting(data.broker.name)}
          </h1>
        ) : (
          <div className="bg-muted h-[41px] w-56 animate-pulse rounded-lg" aria-hidden />
        )}
      </header>

      {/* ── Today ────────────────────────────────────────────────────────
          FIRST because these are the numbers a broker opens the app to see.

          NOT called "Needs attention": that heading was right when both rows
          were problems, and "Closed this month" is the opposite — the number a
          broker is pleased to look at. */}
      <Group title="Today">
        <CountRow
          icon={<Sparkles className="size-[20px]" />}
          label="New today"
          detail="Received since midnight"
          count={attention?.new_today ?? 0}
          loading={loading}
          tone={(attention?.new_today ?? 0) > 0 ? 'info' : 'muted'}
          onClick={() => go('New')}
          last
        />
      </Group>

      {/* ── Pipeline ─────────────────────────────────────────────────────
          A LIST, not the desktop's grid of tiles. Eight boxes two-across is a
          dashboard pattern that works with a mouse and a wide screen; on a
          phone the same eight numbers read faster stacked, with the count in a
          fixed column so the eye runs straight down it. This is the shape iOS
          Settings uses for exactly this job. */}
      <Group title="Pipeline">
        {/* counts.all is lowercase — the key broker_stage_counts actually
            returns. This read counts.All, so the row sat at 0 forever. */}
        <CountRow
          icon={<Layers className="size-[20px]" />}
          label="All"
          count={counts.all ?? 0}
          loading={loading}
          onClick={() => go()}
        />
        {LEAD_STAGES.map((s, i) => {
          const Icon = STAGE_ICONS[s.key] ?? CircleDot
          return (
            <CountRow
              key={s.key}
              icon={<Icon className="size-[20px]" />}
              label={s.label}
              count={counts[s.key] ?? 0}
              loading={loading}
              onClick={() => go(s.key)}
              last={i === LEAD_STAGES.length - 1}
            />
          )
        })}
      </Group>

      {/* APPEARANCE. The phone has no profile menu, so the theme choice the
          desktop keeps in its dropdown lives here instead — as a Settings-style
          group, the shape everything else on this screen already uses. Three
          rows, not a switch: 'Match system' is a real third state. The tick
          marks the current one, the way iOS Settings does. */}
      <Group title="Appearance">
        {(
          [
            ['system', 'Match system', Monitor],
            ['light', 'Light', Sun],
            ['dark', 'Dark', Moon],
          ] as const
        ).map(([value, label, Icon], i, all) => (
          <ChoiceRow
            key={value}
            icon={<Icon className="size-[20px]" />}
            label={label}
            selected={theme === value}
            onClick={() => setTheme(value as Theme)}
            last={i === all.length - 1}
          />
        ))}
      </Group>
    </div>
  )
}

/**
 * A grouped section: grey caps label in the margin, white rounded card beneath.
 * Identical to the Leads screen's Group — the two screens are one app.
 */
function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mx-auto mt-6 max-w-[var(--width-mobile)]">
      <h2 className="text-muted-foreground px-5 pb-1.5 text-[13px] uppercase">{title}</h2>
      <div className="bg-card mx-3 overflow-hidden rounded-[10px]">{children}</div>
    </section>
  )
}

/**
 * One tappable row: icon, label, count, chevron.
 *
 * The COUNT sits right-aligned before the chevron, which is where iOS puts a
 * value in a Settings row. A fixed column means the eye runs straight down the
 * numbers instead of hunting for each one after a label of a different length.
 */
function CountRow({
  icon,
  label,
  detail,
  count,
  tone = 'muted',
  loading,
  onClick,
  last,
}: {
  icon: React.ReactNode
  label: string
  /** Second line, for rows whose meaning is not obvious from the label. */
  detail?: string
  count: number
  /** Colours the NUMBER only. A zero is always grey — it is not news. */
  tone?: 'info' | 'warning' | 'success' | 'muted'
  /** Shows a grey bar in the count's place. The row keeps its full height, so
   *  nothing shifts when the real figure arrives. */
  loading?: boolean
  onClick: () => void
  last?: boolean
}) {
  const toneClass =
    count === 0
      ? 'text-muted-foreground'
      : tone === 'info'
        ? 'text-info'
        : tone === 'warning'
          ? 'text-warning'
          : tone === 'success'
            ? 'text-success'
            : 'text-foreground'

  return (
    <button
      type="button"
      onClick={onClick}
      className="active:bg-muted relative flex min-h-[44px] w-full items-center gap-3 px-4 py-2.5 text-left"
    >
      <span className="text-muted-foreground shrink-0">{icon}</span>

      <span className="min-w-0 flex-1">
        <span className="block truncate text-[17px] leading-tight">{label}</span>
        {detail && (
          <span className="text-muted-foreground block truncate text-[13px] leading-tight">
            {detail}
          </span>
        )}
      </span>

      {loading ? (
        <span className="bg-muted h-4 w-6 shrink-0 animate-pulse rounded" aria-hidden />
      ) : (
        <span className={`shrink-0 text-[17px] font-semibold ${toneClass}`}>{count}</span>
      )}
      <ChevronRight className="text-muted-foreground size-4 shrink-0" />

      {/* Inset separator, aligned to the label rather than the card edge —
          the detail that most separates a native list from a web table. */}
      {!last && <span className="bg-border absolute right-0 bottom-0 left-12 h-px" />}
    </button>
  )
}

/** One option in a pick-one list — the Appearance rows.
 *
 *  A sibling of CountRow with the same 44px hit target, 17px label and inset
 *  hairline, so the group reads as the same list. Where CountRow ends in a
 *  number and a chevron (it is a door into a filtered list), this ends in a
 *  tick on the chosen row and nothing on the others: a choice, not a
 *  navigation, and iOS marks it exactly this way. */
function ChoiceRow({
  icon,
  label,
  selected,
  onClick,
  last,
}: {
  icon: React.ReactNode
  label: string
  selected: boolean
  onClick: () => void
  last?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className="active:bg-muted relative flex min-h-[44px] w-full items-center gap-3 px-4 py-2.5 text-left"
    >
      <span className="text-muted-foreground shrink-0">{icon}</span>
      <span className="block min-w-0 flex-1 truncate text-[17px] leading-tight">{label}</span>
      {/* The slot is always here so the label never shifts; only the tick
          comes and goes. */}
      <span className="flex size-4 shrink-0 items-center justify-center">
        {selected && <Check className="text-primary size-4" aria-hidden />}
      </span>
      {!last && <span className="bg-border absolute right-0 bottom-0 left-12 h-px" />}
    </button>
  )
}

// NotYet lived here — dashed notes saying premiums were not stored and nothing
// scheduled follow-ups. Both became FALSE: /my-day returns
// premium.written_this_month, and this app creates reminders. A screen telling
// brokers a feature does not exist while they are using it is worse than no
// note at all, so the section and the component are gone. Real rows can be
// added when there is a real figure to put in them.
