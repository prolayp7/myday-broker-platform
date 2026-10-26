import { RotateCw, Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { LeadStageBadge } from '@/components/LeadStageBadge'
import { AddLeadDialog } from './AddLeadDialog'
import type { MyLead } from '@/data/api'
import { isPlaceholderContact } from '@/domain/leadStages'
import { isUnjudgedTooLong } from '@/domain/unjudged'

// The list pane of a Fiori List Report: a single filter row, then compact
// standard list items. Deliberately NOT carrying a title — the stage rail to
// its left already names the active filter and its count, and Fiori doesn't
// repeat the filter name over the list it produced.

// "2h" / "3d" / a date past a week — from assigned_at (when it landed with
// this broker), falling back to created_at. Terse by design: it sits inline
// with the phone number as a secondary line, not as its own sentence.
function timeOfDay(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

/**
 * Which day-bucket a lead belongs to.
 *
 * Leads are grouped under these headings instead of every row carrying its own
 * date. A date repeated down forty rows is forty copies of one fact — as a
 * heading it is stated once, and the gap between headings becomes visible in
 * itself: a broker can see at a glance that nothing came in yesterday.
 */
function dayBucket(iso: string | null): string {
  if (!iso) return 'No date'

  const d = new Date(iso)
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const daysAgo = Math.round((startOfDay(new Date()) - startOfDay(d)) / 86_400_000)

  if (daysAgo <= 0) return 'Today'
  if (daysAgo === 1) return 'Yesterday'
  if (daysAgo < 7) return 'Earlier this week'
  if (daysAgo < 30) return 'Earlier this month'
  // Older than a month, the actual month is more use than "58 days ago".
  return d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
}

/** Group leads into buckets, preserving the order they arrived in. */
function groupByDay(leads: MyLead[]): { label: string; leads: MyLead[] }[] {
  const groups: { label: string; leads: MyLead[] }[] = []
  for (const lead of leads) {
    const label = dayBucket(lead.assigned_at ?? lead.created_at)
    const last = groups[groups.length - 1]
    if (last?.label === label) last.leads.push(lead)
    else groups.push({ label, leads: [lead] })
  }
  return groups
}

export function LeadList({
  leads,
  selectedId,
  onSelect,
  heading,
  search,
  onSearch,
  loading,
  loadFailed,
  onRetry,
  onLeadAdded,
}: {
  leads: MyLead[]
  selectedId: string | null
  onSelect: (lead: MyLead) => void
  heading: string
  search: string
  onSearch: (q: string) => void
  loading: boolean
  /** The last load errored — distinct from "this broker has no leads". */
  loadFailed?: boolean
  onRetry?: () => void
  /** A lead was just created here. Carries the new lead so the caller can
   *  open it as well as refetch — a broker who has just typed a customer's
   *  details expects to land on them, not back on the list. */
  onLeadAdded?: (lead: MyLead) => void
}) {
  return (
    <div className="flex h-full w-(--width-lead-list) shrink-0 flex-col border-r bg-card">
      {/* Filter row — search only. The heading is passed for the empty state's
          wording rather than printed above the list. */}
      <div className="flex h-(--height-toolbar) shrink-0 items-center gap-2 border-b px-(--space-edge)">
        <div className="relative min-w-0 flex-1">
          <Search className="text-muted-foreground absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
          <Input
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search name, phone, or email"
            className="h-(--height-control) pl-7 text-xs"
          />
        </div>
        {/* Capturing a lead that did not come through an ad — a referral, a
            walk-in, a phone-in. Sits with the search because both are things
            you do TO the list, not to a lead. */}
        <AddLeadDialog onCreate={onLeadAdded} />
      </div>

      {/* The transition is UNCONDITIONAL, on both states. Attached only to the
          dimmed one, the fade ran on the way in and the list snapped back at
          full brightness when the rows landed — the half of the change a
          broker is actually waiting for. */}
      <div
        className={cn(
          'flex-1 overflow-auto transition-opacity duration-200',
          loading && 'opacity-50',
        )}
      >
        {loadFailed ? (
          /* An error state, not an empty one: the difference between "you have
             no leads" and "we could not load them" is the difference between a
             broker relaxing and a broker calling support. */
          <div className="space-y-2 p-4 text-center">
            <p className="text-muted-foreground text-xs">Could not load your leads.</p>
            {onRetry && (
              <Button variant="outline" size="sm" className="h-(--height-control)" onClick={onRetry}>
                <RotateCw /> Try again
              </Button>
            )}
          </div>
        ) : leads.length === 0 ? (
          <p className="text-muted-foreground p-4 text-center text-xs">
            {search ? 'No leads match your search' : `No leads in ${heading}`}
          </p>
        ) : (
          groupByDay(leads).map((group) => (
            <div key={group.label}>
              {/* Sticky so the bucket you are scrolled into stays named. */}
              <div className="bg-muted/60 text-muted-foreground sticky top-0 z-10 border-b px-(--space-edge) py-1 text-2xs font-semibold backdrop-blur-sm">
                {group.label}
              </div>
              {group.leads.map((lead) => {
                const isSelected = selectedId === lead.id
                // A DAY OLD AND STILL UNJUDGED. The conversion is owed and one
                // tap away, and Meta stops accepting it after 7 days — so the
                // row says so rather than waiting to be noticed.
                const unjudged = isUnjudgedTooLong(lead)

                return (
                  <button
                    key={lead.id}
                    onClick={() => onSelect(lead)}
                    aria-current={isSelected}
                    className={cn(
                      // Selection is marked with a left accent bar rather than
                      // a full-bleed fill, so it reads without drowning the
                      // row's own content. min-h keeps rows even whether or not
                      // the lead has a phone number to show.
                      // transition-COLORS, not bare `transition`: only the
                      // background and the left accent change here, and the
                      // bare version animates layout properties too — so a
                      // row re-flowing mid-poll would visibly slide. Matches
                      // the tab strip in LeadDetail, which already does this.
                      'flex min-h-(--height-row) w-full items-center gap-2 border-b border-l-2 px-(--space-edge) py-1 text-left transition-colors duration-150',
                      isSelected
                        ? 'border-l-primary bg-accent'
                        : 'hover:bg-muted border-l-transparent',
                      // A FULL BORDER, not a hairline ring. This row is asking
                      // for something, and a subtle treatment reads as
                      // decoration — the first version used a 1px 60%-opacity
                      // ring and was invisible in a list of twelve.
                      //
                      // The tint carries it as much as the border does: a red
                      // outline alone still competes with the row's own content,
                      // while a wash makes the row itself look different from
                      // its neighbours at a glance down the column.
                      //
                      // OVERRIDES THE LEFT ACCENT TOO (border-l-destructive),
                      // because a half-red outline with a blue edge reads as two
                      // unrelated states. Selection still shows through the
                      // background, which is the stronger signal of the two.
                      //
                      // 1px, NOT 2px, even though thicker reads louder. The
                      // base row is `border-b border-l-2`, so a 2px box adds a
                      // pixel top and bottom — enough to nudge every row below
                      // it as the flag appears mid-poll. The tint is what makes
                      // it unmissable; the border only draws the boundary.
                      unjudged &&
                        'border border-destructive border-l-destructive bg-destructive/10',
                    )}
                  >
                    <div className="min-w-0 flex-1">
                      {/* text-sm, matching the stage rail's labels. The name is
                          what a broker scans this pane FOR, and at text-xs it
                          was rendering smaller than the navigation beside it —
                          the primary text of the pane reading as subordinate to
                          the menu next to it. */}
                      <div className="truncate text-sm font-semibold">{lead.name}</div>
                      {/* Phone only when there IS one. A "(not provided)"
                          placeholder reads as data at a glance and costs the
                          same line a real number would. */}
                      {!isPlaceholderContact(lead.phone) && (
                        <div className="text-muted-foreground truncate text-xs">{lead.phone}</div>
                      )}
                      {/* THE BORDER SAYS "SOMETHING", THIS SAYS WHAT. A red row
                          with no words is a puzzle: the broker has to guess, and
                          a guess they get wrong once teaches them to ignore the
                          colour. One line naming the action costs a row that is
                          already flagged nothing, and appears on no other. */}
                      {unjudged && (
                        <div className="text-destructive truncate text-xs font-medium">
                          Mark as qualified
                        </div>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {/* text-xs, same step as the phone: both are real data.
                          text-2xs is the micro-badge step (see --text-2xs) and
                          stays on the stage chip below, which is one. */}
                      <span className="text-muted-foreground text-xs tabular-nums">
                        {timeOfDay(lead.assigned_at ?? lead.created_at)}
                      </span>
                      {/* The label rides on the chip rather than beside it:
                          the row is already tight, and a second chip would
                          read as a second stage. Junk and In Progress are
                          narrow enough to fit. */}
                      <LeadStageBadge
                        stage={lead.stage}
                        subStage={lead.sub_stage}
                        className="text-2xs"
                      />
                      {/* The calls so far, coloured by outcome (0121): green
                          picked up, red no answer. A lead nobody has rung shows
                          NOTHING — the blank rows are what a broker scans for,
                          and a column of grey zeros would bury them. */}
                      {lead.answered_count > 0 && (
                        <span
                          aria-label={`Picked up ${lead.answered_count} times`}
                          className="call-tally call-tally-answered"
                        >
                          {lead.answered_count}
                        </span>
                      )}
                      {lead.no_answer_count > 0 && (
                        <span
                          aria-label={`No answer ${lead.no_answer_count} times`}
                          className="call-tally call-tally-missed"
                        >
                          {lead.no_answer_count}
                        </span>
                      )}
                    </div>
                  </button>
                )
              })}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
