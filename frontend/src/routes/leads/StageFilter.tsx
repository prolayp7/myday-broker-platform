import { cn } from '@/lib/utils'
import { RAIL_CHIPS, chipParentStage, leadStageState, type LeadStageState } from '@/domain/leadStages'
import type { MyLeadCounts } from '@/data/api'

// Filtering the lead list by stage, as a strip across the top.
//
// Replaces the left rail (StageRail), which spent 224px of every screen on
// eight stacked rows — and a broker's book is nearly all New and Quote Done,
// so six of those eight read 0 on a normal day. Horizontally the same eight
// fit in one line of chrome, and the pane below gets the width back.
//
// A STRIP, NOT A DROPDOWN. Both would reclaim the space, but a dropdown hides
// the counts behind a click: "how many are Pending" stops being something a
// broker can see and becomes something they have to ask. The counts were the
// rail's one real advantage and they survive the move.
//
// Left to right IS the pipeline — New, Quote Done, Pending, Sold, Submitted,
// Issued, Lost — so the order teaches the workflow the way the rail's vertical
// order did.
//
// This is a FILTER, not a stage setter. Moving a lead through the pipeline
// happens on the quote screen, on the lead's own header, where the broker is
// working on it.

const STATE_DOT: Record<LeadStageState, string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  error: 'bg-error',
  info: 'bg-info',
  neutral: 'bg-muted-foreground',
}

// The hollow version, for label chips — same hue, ring instead of fill.
const RING_DOT: Record<LeadStageState, string> = {
  success: 'ring-success',
  warning: 'ring-warning',
  error: 'ring-error',
  info: 'ring-info',
  neutral: 'ring-muted-foreground',
}

export function StageFilter({
  active,
  counts,
  onSelect,
}: {
  /** A real stage key, or '' for "All leads". */
  active: string
  counts: MyLeadCounts
  onSelect: (stageKey: string) => void
}) {
  // The seven stages plus the two labels that read as stages — In Progress
  // straight after New, Junk after Lost. See RAIL_CHIPS: they are chips here
  // and a column in the database, which is what keeps the pipeline at seven.
  const items = [{ key: '', label: 'All leads', isSubStage: false }, ...RAIL_CHIPS]

  return (
    // Scrolls rather than wraps: a second row would push the list down and
    // change the page height as the window narrows, which is worse than a
    // strip you can nudge sideways on the rare narrow window.
    <nav
      aria-label="Filter leads by stage"
      className="flex h-(--height-toolbar) shrink-0 items-center gap-1 overflow-x-auto border-b bg-card px-(--space-edge)"
    >
      {items.map((it) => {
        const count = it.key === '' ? counts.all : counts[it.key]
        const isActive = active === it.key
        return (
          <button
            key={it.key || 'all'}
            type="button"
            onClick={() => onSelect(it.key)}
            aria-current={isActive}
            className={cn(
              'inline-flex h-(--height-control) shrink-0 items-center gap-1.5 rounded-md px-2.5 text-xs transition',
              isActive
                ? 'bg-accent text-accent-foreground font-medium'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted',
            )}
          >
            {/* A coloured dot rather than the rail's per-stage icons: at this
                size an icon reads as decoration, while the dot carries the
                same state (won / at risk / lost) in a fraction of the width. */}
            {/* A label chip has no palette entry of its own, so it borrows
                its parent stage's colour — In Progress reads as a shade of
                New, Junk as a shade of Lost. Hollow rather than filled, which
                is the one visual cue that it is a label and not a stage; the
                broker does not need to know the difference, but the rail
                should not actively lie about it. */}
            {it.key !== '' && (
              <span
                className={cn(
                  'size-1.5 shrink-0 rounded-full',
                  it.isSubStage
                    ? `ring-1 ring-inset ${RING_DOT[leadStageState(chipParentStage(it.key))]}`
                    : STATE_DOT[leadStageState(it.key)],
                )}
                aria-hidden
              />
            )}
            <span>{it.label}</span>
            {/* Dimmed at zero so a stage with work in it stands out from the
                several that usually do not — the rail's weakness was that
                every row looked equally important. */}
            <span
              className={cn(
                'tabular-nums',
                isActive
                  ? 'text-accent-foreground'
                  : count
                    ? 'text-foreground'
                    : 'text-muted-foreground/50',
              )}
            >
              {count ?? 0}
            </span>
          </button>
        )
      })}
    </nav>
  )
}
