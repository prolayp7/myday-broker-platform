import { Mail, Megaphone, Phone } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { WhatsAppIcon } from '@/components/WhatsAppIcon'
import { isPlaceholderContact } from '@/domain/leadStages'
import type { MyLead } from '@/data/api'

// The selected lead's identity, stage and facts — rendered INTO the app title
// bar (AppShell's `context` slot), not as a row of its own. Keeping it here
// rather than in AppShell keeps the shell generic: it knows it has a context
// slot, not what a lead is.

/** Meta sends an option-list answer as a slug — '2021_or_later'. A
 *  database-shaped string on the title bar reads like a bug to the person
 *  using it, and this is the only place these answers are shown.
 *
 *  Left alone when it does not look like a slug (no underscore), so a free-text
 *  answer someone typed is shown exactly as they wrote it. */
function humanizeAnswer(value: string): string {
  if (!value.includes('_')) return value
  return value.replace(/_/g, ' ')
}

// Meta echoes these back in extra_data and they are already on this line, so
// repeating them would be pure noise.
const ALREADY_SHOWN = new Set(['full_name', 'name', 'email', 'phone', 'phone_number'])

/**
 * One fact on the title bar, introduced by an icon rather than a word.
 *
 * The bar is the tightest space in the app and these three labels — Phone,
 * Email, Source — describe values that already announce what they are: a
 * number, an address, a channel name. Spending characters to say so twice
 * cost width the values themselves needed. The label survives as the
 * accessible name, so a screen reader still hears "Phone: 416...".
 */
function Fact({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Phone
  label: string
  value: React.ReactNode
}) {
  return (
    <div className="flex min-w-0 items-center gap-1.5 text-sm" title={`${label}: ${value}`}>
      <Icon className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
      <span className="sr-only">{label}:</span>
      <span className="truncate font-medium">{value}</span>
    </div>
  )
}

/** A Meta answer, shown WITHOUT its question.
 *
 *  The questions are full sentences — "When were you first licensed in
 *  canada?" — and printing one in front of every answer ate the bar, which is
 *  the tightest space in the app. The answers say what they are: a broker
 *  reading "2021 or later" on a lead does not need to be told it is about
 *  licensing. Same reasoning as Fact above, which drops its labels to icons
 *  for exactly this reason.
 *
 *  The question survives as the accessible name and the hover title, so it is
 *  recoverable for anyone who needs it. */
function TextFact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex min-w-0 text-sm" title={`${label}: ${value}`}>
      <span className="sr-only">{label}:</span>
      <span className="truncate font-medium">{value}</span>
    </div>
  )
}

export function LeadHeader({ lead }: { lead: MyLead }) {
  const answers = Object.entries(lead.extra_data ?? {}).filter(
    ([key]) => !ALREADY_SHOWN.has(key.toLowerCase()),
  )

  return (
    /* TWO GROUPS, pushed apart. What Meta told us about the lead sits on the
       left; how to reach them sits hard against the right edge — so the
       actions are always in the same place regardless of how many answers the
       form happened to carry.

       NO NAME HERE. It used to occupy a --width-lead-list column at the front
       of this bar, which repeated the name already highlighted in the list row
       below AND pushed everything a pane-width right. Dropping it fixed the
       duplication and the alignment together.

       NO PANE OFFSET. AppShell's own nav block is already exactly
       --width-lead-list wide, so this slot begins where the list pane ends —
       which is where the detail panel begins. Adding a second
       pl-(--width-lead-list) here (as this did) doubled it and pushed
       everything a whole pane past where it belonged. The padding below is
       --space-edge, the same gutter the list rows and panes use.

       The width is capped to --width-form because the panel's form is capped
       to it too (NewPolicyPage). Uncapped, the bar ran the full window while
       the form stopped at 52rem, so ml-auto pinned the actions to the WINDOW
       edge rather than the panel's. */
    <div className="flex min-w-0 max-w-(--width-form) flex-1 items-center gap-3 px-(--space-edge)">
      {/* Meta's own answers. Hidden below xl: on a narrow window the number
          and its actions are what a broker needs, and these would crowd them
          out. */}
      <div className="hidden min-w-0 shrink items-center gap-x-4 xl:flex">
        {answers.map(([key, value]) => (
          <TextFact key={key} label={humanizeAnswer(key)} value={humanizeAnswer(String(value))} />
        ))}
        {lead.source && <Fact icon={Megaphone} label="Source" value={lead.source} />}
        {!isPlaceholderContact(lead.email) && (
          <Fact icon={Mail} label="Email" value={lead.email} />
        )}
      </div>

      {/* ml-auto pins this to the right edge. Shown only when there is a real
          number — a "(not provided)" placeholder occupies the same space a
          real value would and still reads as content. */}
      {!isPlaceholderContact(lead.phone) && (
        <div className="ml-auto flex shrink-0 items-center gap-1">
          {/* Separates WHAT WE KNOW about the lead from HOW TO REACH THEM.
              Without it the whole bar reads as one undivided run of text, and
              the number — the thing a broker actually acts on — has nothing
              marking it as different in kind from an answer to a form
              question. Same rule AppShell uses between its own controls, so
              this is the established divider rather than a new idea. */}
          <div
            className="bg-border mr-2 hidden h-(--height-control) w-px xl:block"
            aria-hidden
          />
          {/* The number leads, its actions follow. No phone glyph in front of
              it: the Call button IS one, so introducing the number with a
              second printed the same symbol twice. */}
          <span className="truncate text-sm font-medium" title={`Phone: ${lead.phone}`}>
            <span className="sr-only">Phone:</span>
            {lead.phone}
          </span>

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-(--height-control)"
                aria-label={`Call ${lead.name}`}
                asChild
              >
                <a href={`tel:${lead.phone}`}>
                  <Phone className="size-3.5" />
                </a>
              </Button>
            </TooltipTrigger>
            <TooltipContent>Call {lead.phone}</TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="text-success hover:text-success size-(--height-control)"
                aria-label={`WhatsApp ${lead.name}`}
                asChild
              >
                <a
                  href={`https://wa.me/${lead.phone.replace(/[^\d]/g, '')}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <WhatsAppIcon />
                </a>
              </Button>
            </TooltipTrigger>
            <TooltipContent>WhatsApp {lead.phone}</TooltipContent>
          </Tooltip>
        </div>
      )}
    </div>
  )
}
