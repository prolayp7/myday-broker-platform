import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Ban,
  BadgeCheck,
  Check,
  ChevronLeft,
  CircleDashed,
  CircleDot,
  FileCheck,
  FileText,
  Handshake,
  Hourglass,
  Info,
  Layers,
  Mail,
  MessageCircle,
  MessageSquare,
  MoreHorizontal,
  Phone,
  Plus,
  Search,
  X,
} from 'lucide-react'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { LeadStageBadge } from '@/components/LeadStageBadge'
import { LEAD_STAGES, isPlaceholderContact } from '@/domain/leadStages'
import { toast } from 'sonner'
import {
  getLeadContacts,
  getLeadReminders,
  getMyLeads,
  completeReminder,
  createReminder,
  getMyLead,
  saveLeadContact,
  updateLeadStage,
  firstValidationMessage,
  type Contact,
  type MyLead,
  type Reminder,
} from '@/data/api'
import { WheelDateTimePicker } from '@/components/WheelDateTimePicker'
import { cn } from '@/lib/utils'

// WIREFRAME — the mobile companion. Placeholder data, nothing wired.
//
// NOT A CRM ON A PHONE. Four things and deliberately nothing else: see my
// leads, call one, move its stage, set a follow-up. New Policy, drivers,
// vehicles, property — all stay on the desktop, where there is room for them
// and where a broker sits down to do that work.
//
// BUILT TO iOS CONVENTIONS, not the desktop app's. These are two different
// design languages, and this screen follows the phone's: a broker holding it
// compares it to Contacts and Phone, not to our own desktop.
//
//   Grouped cards      white cards on a grey page, not bordered boxes on
//                      white. This is the single biggest reason a web screen
//                      reads as "a website on a phone" rather than an app.
//   44pt rows          Apple's minimum tap target. Everything touchable here
//                      clears it.
//   17pt body          iOS body size. The desktop app runs at
//                      html { font-size: 87.5% } for density with a mouse; at
//                      arm's length on a phone that is unreadable, so sizes
//                      here are set against the phone, not that scale.
//   Inset separators   aligned to the text, never full-bleed — the detail that
//                      most separates a native list from a web table.
//   Tint, not fill     actions are coloured text and icons, not filled
//                      buttons.
//
// Grouped by DATE rather than alphabetically: a broker's leads are a work
// queue, not a directory. "Who came in today" is the real question, and when
// they already know the name, that is what search is for.
//
// CAPPED AT A PHONE'S WIDTH, then centred. On an iPad this would otherwise
// stretch to 800px and more: eight bottom-bar cells each a hundred pixels wide
// with a 20px icon adrift in the middle, and list rows whose name sits a
// forearm away from the time beside it. iOS does the same thing — a
// phone-shaped app on a tablet stays phone-shaped rather than growing to fill
// the glass. The bar's own background still reaches both edges; only its cells
// are held in.

/**
 * The row's view model. Deliberately NOT the raw MyLead: this screen wants a
 * Date it can compare, and the API hands back an ISO string.
 */
interface MobileLead {
  id: string
  name: string
  phone: string
  /** E.164, for wa.me — which cannot route a number with no country code. */
  phone_e164: string | null
  email: string
  stage: string
  /** The label inside the stage, or null. SHOWN, NEVER SET here: the phone's
   *  stage sheet moves a lead through the pipeline, and every move clears the
   *  label — labelling one is desk work (see routes/Leads.tsx). */
  sub_stage: string | null
  /** When the lead was assigned to this broker — the moment it became theirs
   *  to call, which is what a work queue is ordered by. Falls back to
   *  created_at for older rows that predate assignment tracking. */
  at: Date
  /** Calls so far, picked up and not (0121). DISPLAY ONLY on the phone — the
   *  Auto-tab steppers on the desktop are the one place these are changed, so
   *  there is a single control rather than two that can disagree. */
  answered_count: number
  no_answer_count: number
}

function toMobileLead(l: MyLead): MobileLead {
  return {
    id: l.id,
    name: l.name,
    phone: l.phone,
    phone_e164: l.phone_e164,
    email: l.email,
    stage: l.stage,
    sub_stage: l.sub_stage,
    at: new Date(l.assigned_at ?? l.created_at),
    // MUST be mapped here as well as declared above: this function is the only
    // path from MyLead to MobileLead, so a field missing here is silently
    // undefined on every phone row while the desktop looks correct.
    answered_count: l.answered_count,
    no_answer_count: l.no_answer_count,
  }
}

/**
 * Intl formatters, built ONCE at module load.
 *
 * MEASURED, NOT ASSUMED: constructing these per call — which is what
 * toLocaleTimeString/toLocaleDateString do internally — costs 8ms per 200-row
 * render on a desktop, so roughly 32ms on a mid-range phone. That is two
 * dropped frames on EVERY KEYSTROKE in the search box, because filtering
 * re-renders the list. Hoisted, the same work is 0.14ms: about 57x faster.
 *
 * Intl object construction is the expensive part; .format() on an existing one
 * is cheap. This is the single biggest performance decision in the file.
 */
const TIME_FMT = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' })
const DATE_FMT = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' })

/**
 * A date reduced to one comparable number, e.g. 2026-07-31 -> 20260731.
 *
 * Replaces toDateString(), which built and compared a 15-character string
 * twice per row. An integer compare is free and cannot be tripped up by
 * locale — toDateString() is locale-independent in practice but not
 * guaranteed to be, and this removes the question.
 */
function dayKey(at: Date): number {
  return at.getFullYear() * 10000 + at.getMonth() * 100 + at.getDate()
}

/**
 * What a row shows in its right-hand column, the way iOS Recents does it: a
 * TIME for today, "Yesterday", then a date.
 *
 * Today gets a time because "Today" is not an answer when a broker is
 * triaging — a lead that landed at 9am has been sitting for hours, one from
 * 2:14 PM just arrived, and which to ring first depends on knowing that.
 * Beyond today the hour stops mattering and the date is the useful fact.
 *
 * Takes today/yesterday as arguments rather than calling new Date() itself:
 * the old version built two Date objects PER ROW to work out what "today"
 * was, which is the same answer every time.
 */
function whenLabel(at: Date, todayKey: number, yesterdayKey: number): string {
  const k = dayKey(at)
  if (k === todayKey) return TIME_FMT.format(at)
  if (k === yesterdayKey) return 'Yesterday'
  return DATE_FMT.format(at)
}

/**
 * A timestamp for the detail screen's rows: "Today, 2:14 PM", "Yesterday,
 * 9:15 AM", then a date. Fuller than the list's whenLabel because these rows
 * have the width for it and a call's exact time is often the point.
 */
function whenDue(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const k = dayKey(d)
  const now = new Date()
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  const time = TIME_FMT.format(d)
  if (k === dayKey(now)) return `Today, ${time}`
  if (k === dayKey(y)) return `Yesterday, ${time}`
  return `${DATE_FMT.format(d)}, ${time}`
}

/** Whether a search query is a phone search rather than a name search.
 *
 *  Two digits is the threshold: one digit matches most numbers in a book and
 *  would reveal a phone line on almost every row for no information. */
function isNumericQuery(query: string): boolean {
  return query.replace(/\D/g, '').length >= 2 && !/[a-z]/i.test(query)
}

/** Where the query matches, as a [start, end) slice of `text`. Null if it does
 *  not match.
 *
 *  DIGITS ARE MAPPED BACK ONTO THE FORMATTED STRING, and that is the whole
 *  reason this is not a one-line indexOf. The filter compares digits only, so
 *  "647" matches "(647) 531-3736" — but tinting characters 0..3 of the
 *  formatted string would land on "(64" instead of "647". The loop walks the
 *  original text counting digits, so the highlight covers exactly the
 *  characters the digits sit in, brackets and spaces included.
 */
function matchRange(text: string, query: string): [number, number] | null {
  if (!text || !query.trim()) return null

  if (isNumericQuery(query)) {
    const wanted = query.replace(/\D/g, '')
    const digitsOnly = text.replace(/\D/g, '')
    const digitStart = digitsOnly.indexOf(wanted)
    if (digitStart < 0) return null

    let seen = 0
    let start = -1
    for (let i = 0; i < text.length; i++) {
      if (!/\d/.test(text[i])) continue
      if (seen === digitStart) start = i
      seen++
      // The end is INCLUSIVE of this character, hence i + 1: a match ending on
      // a digit must include that digit.
      if (seen === digitStart + wanted.length) return [start, i + 1]
    }
    return null
  }

  const at = text.toLowerCase().indexOf(query.trim().toLowerCase())
  return at < 0 ? null : [at, at + query.trim().length]
}

/** The text, with the matched part tinted.
 *
 *  WHY THIS EXISTS. The list filters as you type and nothing on screen said
 *  WHY a row survived — the broker re-read each name and worked it out. This
 *  shows the answer.
 *
 *  <mark> rather than a span: it is what the element means, and a screen
 *  reader announces it. bg-info-muted rather than a literal yellow, because
 *  that token has a dark-mode counterpart and a hardcoded highlight becomes an
 *  unreadable block on a dark ground. */
function Highlight({ text, query }: { text: string; query: string }) {
  const range = matchRange(text, query)
  if (!range) return <>{text}</>

  const [start, end] = range
  return (
    <>
      {text.slice(0, start)}
      <mark className="bg-info-muted text-foreground rounded-[3px] px-px">
        {text.slice(start, end)}
      </mark>
      {text.slice(end)}
    </>
  )
}

/**
 * An icon per pipeline stage, so the stage row reads at a glance rather than
 * as seven words of similar length. Each is chosen for what the stage MEANS,
 * not for decoration — a broker mid-call should recognise the shape without
 * reading it.
 *
 * Shown WITHOUT labels: seven labelled cells cannot fit one line on a phone,
 * and a two-line bar eats the content above it. The risk of an unfamiliar
 * glyph is covered instead by naming the current stage twice — in the badge
 * under the customer's name, and beneath the bar itself.
 */
/**
 * The four a broker moves between all day. On a NARROW screen these are what
 * stays on the bar; the rest sit behind the ⋯.
 *
 * Submitted, Issued and Lost are END STATES — a broker sets one and stops
 * thinking about that lead — so they are the right three to push behind an
 * overflow when there is no room.
 *
 * On anything wider the overflow is unnecessary and all seven show. See
 * WIDE_BAR below for where that line falls.
 */
const PRIMARY_STAGES = ['New', 'Quote Done', 'Pending', 'Sold']

// Stages this screen cannot complete. Reaching any of them needs a complete
// applicant (name, phone, email, date of birth, gender, full address), and a
// Meta lead also needs the qualified/not-qualified answer — neither of which
// mobile can collect. Mirrors services/lead.py::_STAGES_NEEDING_APPLICANT;
// the backend refuses regardless, this just stops the broker being sent to a
// dead end. 'Lost' is absent there and here: a dead lead must stay closable
// from anywhere.
const STAGES_NEEDING_DESKTOP = new Set([
  'Quote Done',
  'Pending',
  'Sold',
  'Submitted',
  'Issued',
])

/**
 * The width at which all seven stages fit without cramping.
 *
 * ARITHMETIC, NOT A GUESS: eight cells (All + seven stages) each need about
 * 40px to hold a 20px icon above a truncated micro label. 320px covers that,
 * which is every phone still in use — an iPhone SE at 375 and a Galaxy S8 at
 * 360 both clear it comfortably.
 *
 * A tap target narrower than Apple's 44pt is the trade, and it is the right
 * one here: the cell is 48px TALL, the bar sits under the thumb, and the
 * alternative — burying three stages behind an overflow on every phone — costs
 * more than a few pixels of width. Below 320 the overflow still catches it.
 *
 * A media query rather than JavaScript measuring the bar. Measuring means a
 * ResizeObserver, a re-measure on every rotate, and a frame of flicker on
 * first paint while it decides — real machinery for seven fixed labels whose
 * widths are known in advance.
 */
const WIDE_BAR = 'min-[320px]:'

const STAGE_ICONS: Record<string, typeof Phone> = {
  New: CircleDot, // untouched, nothing done yet
  'In Progress': CircleDashed, // someone has started on it
  'Quote Done': FileText, // a quote exists
  Pending: Hourglass, // waiting on the customer
  Sold: Handshake, // they said yes
  Submitted: FileCheck, // paperwork is with the insurer
  Issued: BadgeCheck, // the policy is real
  Lost: Ban, // gone
}

export default function MobileLeads() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [selected, setSelected] = useState<MobileLead | null>(null)
  // Whether the selection came from a ?lead= deep link, which also means the
  // reminder form should open. Cleared on back, so tapping the same lead from
  // the list afterwards does not reopen it.
  const [openReminder, setOpenReminder] = useState(false)
  const [search, setSearch] = useState('')
  // Focused again after clearing, so the keyboard does not drop between a
  // mistyped name and the right one.
  const searchRef = useRef<HTMLInputElement>(null)

  // Whether anything has scrolled under the header yet. passive: true — this
  // listener never calls preventDefault, and saying so lets the browser scroll
  // without waiting to find out.
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 0)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Null means every stage. A filter that starts filtered hides leads nobody
  // asked to hide — EXCEPT when a ?stage= arrives, which is a broker tapping a
  // My Day tile and asking for exactly that stage.
  //
  // Validated against the real taxonomy rather than trusted: a hand-edited URL
  // must not leave the screen filtered to a stage that does not exist, showing
  // an empty list with no way to tell why.
  const [stageFilter, setStageFilter] = useState<string | null>(() => {
    const wanted = searchParams.get('stage')
    return LEAD_STAGES.some((s) => s.key === wanted) ? wanted : null
  })

  const [leads, setLeads] = useState<MobileLead[]>([])
  const [loading, setLoading] = useState(true)
  // A failed load is not an empty book. Distinguishing them matters here more
  // than most places: "no leads" and "we could not reach the server" look
  // identical on screen and mean opposite things to a broker.
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    // limit: the whole book in one request, since this filters locally. 200 is
    // the most GET /leads/mine accepts (Query(..., le=200)) — asking for more
    // is a 422 before the handler runs, which this screen could only render as
    // "Couldn't load your leads". Above any real broker's assignment anyway.
    getMyLeads({ limit: 200 })
      .then((page) => {
        setLeads(page.rows.map(toMobileLead))
        setFailed(false)
      })
      .catch(() => setFailed(true))
      .finally(() => setLoading(false))
  }, [])

  // ?lead=<uuid> — ARRIVING FROM A RENEWAL WHATSAPP, which is the likeliest
  // way this screen is opened at all: the broker is on their phone reading the
  // message.
  //
  // FETCHED BY ID rather than found in the list above. That request caps at
  // 200 (the endpoint's own limit), so a broker with a bigger book would have
  // the link silently land on an unopened list — the one case where "usually
  // present" is not good enough.
  const deepLinkId = searchParams.get('lead')
  useEffect(() => {
    if (!deepLinkId) return
    let cancelled = false
    getMyLead(deepLinkId)
      .then((lead) => {
        if (cancelled) return
        setSelected(toMobileLead(lead))
        // Arriving this way always means the reminder form: it is the only
        // button that links here.
        setOpenReminder(true)
      })
      .catch(() => {
        // Not this broker's lead, or gone. Quiet — the list stands, which says
        // more than an error about a link they did not type.
      })
    return () => {
      cancelled = true
    }
  }, [deepLinkId])
  const [moreOpen, setMoreOpen] = useState(false)

  if (selected) {
    // key={} REMOUNTS per lead. Without it React reuses the component and
    // its useState(lead.stage) keeps the PREVIOUS lead's stage — the wrong
    // stage shown against the wrong customer, which is the kind of bug that
    // ends with a policy moved by mistake.
    return (
      <MobileLeadDetail
        key={selected.id}
        lead={selected}
        openReminder={openReminder}
        onBack={() => {
          setSelected(null)
          setOpenReminder(false)
        }}
      />
    )
  }

  // Once per render, not once per row. Every row needs to know what "today"
  // is, and it is the same answer for all of them.
  const now = new Date()
  const todayKey = dayKey(now)
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  const yesterdayKey = dayKey(yesterday)

  // FILTERED IN THE BROWSER, deliberately. The endpoint accepts stage and
  // search, but a broker's own book is tens of leads, not thousands — fetching
  // it once and narrowing locally makes typing instant, where a request per
  // keystroke would put a network round-trip between the key and the result.
  //
  // If a broker ever carries enough leads for this to matter, the endpoint is
  // already paged and ready to take over.
  const visible = leads.filter((l) => {
    if (stageFilter && l.stage !== stageFilter) return false
    if (!search.trim()) return true

    const q = search.trim().toLowerCase()
    if (l.name.toLowerCase().includes(q)) return true

    // THE DIGITS ARE CHECKED ONLY WHEN THERE ARE DIGITS. This clause used to
    // run unconditionally, and "zzz".replace(/\D/g, '') is "" — which every
    // string .includes(). So the phone test matched EVERY lead, the name test
    // never got a say, and typing any name returned the whole book. Searching
    // by name looked like it did nothing at all, because it did nothing at
    // all; only number searches worked.
    const digits = search.replace(/\D/g, '')
    return digits.length > 0 && l.phone.replace(/\D/g, '').includes(digits)
  })

  return (
    // min-h-dvh, not min-h-screen: vh on mobile Safari means the viewport
    // with the URL bar COLLAPSED, so a 100vh page is taller than what you can
    // actually see and the screen starts with a scroll nobody asked for. dvh
    // tracks the bar as it hides and returns.
    //
    // pb-32 clears the fixed filter bar below — without it the last row
    // scrolls under it and can never be read.
    <div className="bg-background min-h-dvh pb-32">
      {/* iOS large title: big, left-aligned, above the search field — never
          centred in a bar. The stage filter is NOT here: it lives pinned to
          the bottom, where a thumb reaches without the hand moving. */}
      {/* The BAR spans the viewport; only its CONTENT is capped. A sticky
          element that is itself centred and narrow lets the page scroll past
          its sides — invisible on a phone, obviously broken on a tablet. Same
          shape as the bottom bars. */}
      {/* A HAIRLINE ONCE SCROLLED, nothing at the top. Without it the search
          field and the first row share a background and the header stops
          reading as a surface the list passes beneath — the detail that makes
          a sticky area feel like an iOS navigation bar. */}
      <header
        className={cn(
          'bg-background sticky top-0 z-10 px-4 pt-[calc(0.75rem+env(safe-area-inset-top))] pb-2.5',
          'transition-shadow duration-150 ease-out',
          scrolled && 'shadow-[0_0.5px_0_0_var(--border)]',
        )}
      >
        {/* No title. The bottom bar's lit cell already names the active
            filter, and a heading repeating it was a row spent saying the same
            word twice. On a phone the rows ARE the screen. */}

        {/* iOS back: chevron plus the previous screen's name, in the tint
            colour — never a boxed button. This is a pushed screen now rather
            than the landing one, so it gets the same treatment the lead detail
            below already uses. */}
        <div className="mx-auto flex max-w-[var(--width-mobile)] items-center pb-1">
          <button
            type="button"
            onClick={() => navigate('/m')}
            className="text-primary -ml-2 inline-flex min-h-[44px] items-center gap-0.5 px-2 text-[17px]"
          >
            <ChevronLeft className="size-6" />
            My Day
          </button>
        </div>

        {/* The iOS search field — filled and pill-shaped with the icon inside,
            not a bordered input. */}
        <div className="relative mx-auto max-w-[var(--width-mobile)]">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-[17px] -translate-y-1/2" />
          <input
            ref={searchRef}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search"
            inputMode="search"
            className="bg-muted placeholder:text-muted-foreground h-[44px] w-full rounded-[10px] pr-10 pl-9 text-[17px] outline-none"
          />
          {/* KEEPS FOCUS. Clearing is a step in searching, not the end of it —
              a broker mistypes a name and types the right one, and losing the
              keyboard between the two would mean tapping the field again.
              44px wide despite a 17px glyph: Apple's minimum target, and this
              one sits beside a text cursor it must not be mistaken for. */}
          {search && (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => {
                setSearch('')
                searchRef.current?.focus()
              }}
              className="text-muted-foreground absolute top-1/2 right-0 flex h-[44px] w-11 -translate-y-1/2 items-center justify-center transition-opacity duration-150 ease-out active:opacity-50"
            >
              <X className="size-[17px]" />
            </button>
          )}
        </div>

      </header>

      {/* FOUR STATES, told apart. "Loading", "we could not reach the server",
          "you filtered everything out" and "you have no leads" look identical
          as an empty list and mean completely different things — the second in
          particular must never read as the fourth. */}
      {failed ? (
        <div className="mx-auto max-w-[var(--width-mobile)] px-4 py-8 text-center">
          <p className="text-[17px]">Couldn't load your leads</p>
          <p className="text-muted-foreground pt-1 text-[15px]">
            Check your connection and try again.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="text-primary min-h-[44px] px-4 pt-3 text-[17px]"
          >
            Retry
          </button>
        </div>
      ) : loading ? (
        /* SKELETON ROWS, not the word "Loading". A centred word occupies a
           different shape from the list that replaces it, so the screen jumps
           when the leads land. These sit in the same card, at the same row
           height, so the real list fades in over its own outline instead of
           displacing something else.

           Five: enough to read as a list without pretending to know how many
           leads are coming. */
        <div className="mx-auto max-w-[var(--width-mobile)] px-3">
          <ul className="bg-card overflow-hidden rounded-[10px]" aria-hidden>
            {Array.from({ length: 5 }, (_, i) => (
              <li key={i} className="relative flex items-center py-3 pl-4 pr-4">
                <span className="bg-muted h-[17px] flex-1 animate-pulse rounded"
                      style={{ maxWidth: `${9 - (i % 3) * 2}rem` }} />
                <span className="bg-muted ml-2 h-[15px] w-12 shrink-0 animate-pulse rounded" />
                {i < 4 && <span className="bg-border absolute right-0 bottom-0 left-4 h-px" />}
              </li>
            ))}
          </ul>
        </div>
      ) : visible.length === 0 ? (
        <p className="text-muted-foreground mx-auto max-w-[var(--width-mobile)] px-4 py-8 text-center text-[17px]">
          {search.trim() || stageFilter ? 'No results' : 'No leads assigned to you yet'}
        </p>
      ) : (
        // No touch-action here on purpose: this list does not scroll, the PAGE
        // does. overflow-hidden is only clipping the rounded corners. Hinting
        // the gesture on a non-scroller does nothing.
        // px-3 on a wrapper rather than a margin on the <ul>: the list still
        // needs mx-auto + max-w to stay phone-shaped and centred on an iPad,
        // and a horizontal margin would fight that. The card was already
        // rounded but sat flush against both screen edges, so its corners had
        // nothing to round against and this one list read as full-bleed while
        // every other card in the app is inset — My Day uses px-3 for the same
        // job.
        <div className="mx-auto max-w-[var(--width-mobile)] px-3">
        <ul className="bg-card overflow-hidden rounded-[10px]">
          {visible.map((lead, i) => {
            // The day sits BESIDE the name rather than on a heading row of its
            // own. A standalone heading costs a full row per group, and on a
            // phone showing seven leads that was three rows spent on three
            // words.
            const last = i === visible.length - 1
            return (
              <li key={lead.id} className="relative">
                <div className="flex items-center">
                  {/* Tapping the NAME calls, exactly as it would in Contacts.
                      A broker does not dial by reading digits off a screen —
                      they tap the person.

                      The name leads the row. It is what a broker is scanning
                      for; the day is context, and context belongs after the
                      thing it qualifies. */}
                  <a
                    href={`tel:${lead.phone}`}
                    // draggable={false}: a long-press starts a drag on several
                    // mobile browsers, which fights the scroll gesture on a
                    // list meant to be flicked.
                    draggable={false}
                    className="active:bg-muted min-h-[44px] min-w-0 flex-1 py-3 pl-4 text-left text-[17px] leading-6 transition-colors duration-[110ms] ease-out"
                  >
                    <span className="block truncate">
                      <Highlight text={lead.name} query={search} />
                    </span>
                    {/* Phone number as microtext under the name, like iOS
                        Contacts — shown on every row, not only during a
                        numeric search. */}
                    <span className="text-muted-foreground flex items-center gap-2 truncate text-[13px] leading-5 tabular-nums">
                      <Highlight text={lead.phone} query={search} />
                      {/* Same tallies as the desktop list, same rule: a lead
                          nobody has rung shows nothing, so the blank rows are
                          the ones still to do. Display only — these are moved
                          on the Auto tab, not here. */}
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
                    </span>
                  </a>

                  {/* The day, on EVERY row. iOS puts the time beside every
                      entry in Recents rather than only the first of a group,
                      and it is right: a broker scanning a long list should
                      never have to trace upward to work out when a lead came
                      in. whitespace-nowrap because "Yesterday" wrapped into
                      the name when this column sat on the left. */}
                  {/* pl-2: the name truncates into this column, and without a
                      gap a long one ("Jean-Christophe Vandenberghe") ends
                      flush against the time with no space between them. */}
                  <span className="text-muted-foreground shrink-0 py-3 pr-1 pl-2 text-[13px] leading-6 whitespace-nowrap">
                    {whenLabel(lead.at, todayKey, yesterdayKey)}
                  </span>

                  {/* ⓘ opens the lead — the same split iOS draws between "call
                      this person" and "look at this person". */}
                  <button
                    type="button"
                    onClick={() => setSelected(lead)}
                    className="text-primary active:bg-muted flex min-h-[44px] shrink-0 items-center px-3 transition-colors duration-[110ms] ease-out"
                    aria-label={`Details for ${lead.name}`}
                  >
                    {/* The info glyph ALONE, as iOS Recents does it. A chevron
                        beside it said the same thing twice and made a 44px
                        target look like two controls. */}
                    <Info className="size-[22px]" />
                  </button>
                </div>

                {/* INSET SEPARATOR — starting at the text, never full-bleed,
                    which is the detail that most separates a native list from
                    a web table. Full width where a new DAY starts: that break
                    is what groups the list now that no heading does it. */}
                {!last && (
                  <div
                    className={cn(
                      'bg-border absolute right-0 bottom-0 h-px',
                      dayKey(visible[i + 1].at) !== dayKey(lead.at) ? 'left-0' : 'left-4',
                    )}
                  />
                )}
              </li>
            )
          })}
        </ul>
        </div>
      )}

      {/* ── Stage filter ─────────────────────────────────────────────────
          PINNED, the way iOS puts its tab bar at the bottom of Contacts and
          Phone. A thumb reaches the bottom of a phone without the hand moving;
          the top of a modern screen it does not.

          Deliberately the SAME shape as the stage bar on the detail screen,
          because both answer "which stage" — one narrowing a list, the other
          moving a lead. They never appear together, and the screen around them
          says which is which: a list behind it, or one customer's name.

          "All" leads, because clearing a filter is the thing a broker wants
          most often once they have finished with one. */}
      <div className="bg-card border-border fixed inset-x-0 bottom-0 transform-gpu border-t pb-[max(env(safe-area-inset-bottom),1rem)] [contain:layout_paint]">
        {/* EDGE TO EDGE, EQUAL CELLS. No gaps and no side padding: every cell
            takes an equal share of the full width, the way a phone's own tab
            bar does. Gaps and margins were spending on decoration the width
            that the targets themselves need — with eight cells on a 375px
            screen that was the difference between clearing Apple's 44pt
            minimum and not.

            All eight fit at any phone width because each is flex-1 of whatever
            there is; the icon stays 20px and the cell around it grows or
            shrinks. */}
        <div className="mx-auto flex max-w-[var(--width-mobile)] items-stretch">
          <FilterCell active={stageFilter === null} onClick={() => setStageFilter(null)} label="All">
            <Layers className="size-[20px]" />
          </FilterCell>
          {/* EVERY stage is rendered; the ones beyond the working four are
              hidden by CSS below 390px rather than left out of the tree. That
              way one markup describes both layouts, and a phone rotating from
              portrait to landscape simply reveals them — no state to keep in
              step, no re-measure. */}
          {LEAD_STAGES.map((s) => {
            const Icon = STAGE_ICONS[s.key] ?? CircleDot
            const overflow = !PRIMARY_STAGES.includes(s.key)
            return (
              <FilterCell
                key={s.key}
                active={stageFilter === s.key}
                onClick={() => setStageFilter(stageFilter === s.key ? null : s.key)}
                label={s.label}
                // Overflow stages stay in the overflow, selected or not. An
                // earlier version promoted the selected one onto the rail so
                // something was always lit — but that made the bar rearrange
                // itself under the thumb, and cells moving between taps is
                // worse than a rail with nothing highlighted. The More cell
                // lights instead.
                className={cn(overflow && `hidden ${WIDE_BAR}flex`)}
              >
                <Icon className="size-[20px]" />
              </FilterCell>
            )
          })}

          {/* The overflow. A sheet rather than a menu because that is what a
              phone does for it: slides up from the bottom, where the thumb
              already is.

              LIT when the active filter is one of the stages inside — so a
              short list always has something on the bar explaining why, without
              the rail having to reshuffle to show it. */}
          <FilterCell
            active={Boolean(stageFilter) && !PRIMARY_STAGES.includes(stageFilter as string)}
            onClick={() => setMoreOpen(true)}
            label="More"
            className={`${WIDE_BAR}hidden`}
          >
            <MoreHorizontal className="size-[20px]" />
          </FilterCell>
        </div>

      </div>

      <Sheet open={moreOpen} onOpenChange={setMoreOpen}>
        <SheetContent side="bottom" className="rounded-t-[14px] pb-8">
          <SheetHeader className="pb-2">
            <SheetTitle className="text-[17px]">Filter by stage</SheetTitle>
          </SheetHeader>
          <div className="px-4">
            {LEAD_STAGES.filter((s) => !PRIMARY_STAGES.includes(s.key)).map((s, i, arr) => {
              const Icon = STAGE_ICONS[s.key] ?? CircleDot
              const active = stageFilter === s.key
              return (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => {
                    setStageFilter(active ? null : s.key)
                    setMoreOpen(false)
                  }}
                  className={cn(
                    'relative flex min-h-[52px] w-full items-center gap-3 text-[17px]',
                    active ? 'text-primary font-medium' : 'text-foreground',
                  )}
                >
                  <Icon className="size-[22px] shrink-0" />
                  {s.label}
                  {active && <Check className="text-primary ml-auto size-5" />}
                  {i < arr.length - 1 && (
                    <span className="bg-border absolute right-0 bottom-0 left-9 h-px" />
                  )}
                </button>
              )
            })}
          </div>
        </SheetContent>
      </Sheet>

    </div>
  )
}

/**
 * One cell of a bottom bar. Equal share of the full width, no gap and no
 * rounding — the shape of a phone's own tab bar, where cells butt against each
 * other and the bar itself is the boundary.
 *
 * The selected state is a tinted PILL inside the cell rather than the whole
 * cell filling: a full-bleed fill against its neighbours reads as a block of
 * colour rather than a selection, and iOS never does it.
 */
function FilterCell({
  active,
  onClick,
  label,
  className,
  children,
}: {
  active: boolean
  onClick: () => void
  /** Shown as micro text under the icon, and used as the accessible name. */
  label?: string
  /** Visibility, so the bar can drop its overflow stages on a narrow screen. */
  className?: string
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      className={cn(
        'flex min-h-[48px] flex-1 flex-col items-center justify-center gap-0.5 py-1.5 transition-colors',
        active ? 'text-primary' : 'text-muted-foreground',
        className,
      )}
    >
      <span
        className={cn(
          'flex h-7 w-full max-w-[52px] items-center justify-center rounded-full transition-colors',
          active && 'bg-primary text-primary-foreground',
        )}
      >
        {children}
      </span>
      {/* Micro label, the way a phone's tab bar names every icon. An icon
          alone is a memory test, and "Sold" against "Submitted" against
          "Issued" are three close ideas no glyph separates on its own.
          truncate rather than wrap: a two-line cell would make the bar taller
          than the content it sits under deserves. */}
      {label && (
        <span
          className={cn(
            'w-full truncate px-0.5 text-center text-[10px] leading-none',
            active && 'font-medium',
          )}
        >
          {label}
        </span>
      )}
    </button>
  )
}

/**
 * One lead, ordered the way iOS orders a contact card: who they are, the ways
 * to reach them, then everything else in grouped sections.
 */
function MobileLeadDetail({
  lead,
  onBack,
  openReminder = false,
}: {
  lead: MobileLead
  onBack: () => void
  /** Arrived from the renewal WhatsApp's "Set Up Reminder" button — open the
   *  add-reminder row straight away rather than leaving the broker to find it.
   *  Read once at mount; the parent remounts per lead (key=), so it cannot
   *  reopen for a different customer. */
  openReminder?: boolean
}) {
  const [stage, setStage] = useState(lead.stage)

  /**
   * OPTIMISTIC, then reconciled. The bar lights the new stage immediately —
   * a broker taps it as they hang up and should not watch a spinner — but a
   * failed write is rolled back and said out loud. Silently keeping a stage
   * the server rejected is the worst outcome: the broker believes the lead
   * moved and it did not.
   */
  const moveStage = (next: string) => {
    // STOPPED HERE RATHER THAN AT THE SERVER. Quote Done and beyond need a
    // complete applicant, and Meta leads need a qualified/not-qualified
    // answer — and this screen can supply NEITHER: there is no applicant form
    // and no qualification dialog on mobile. The backend refuses with a 422
    // either way, so without this the broker taps, sees an error naming
    // fields they cannot reach from here, and taps again.
    //
    // Deliberately not a port of the desktop dialog: answering qualification
    // on a phone would still leave the applicant unfillable, so the lead
    // would stop one step later for a reason just as opaque.
    if (STAGES_NEEDING_DESKTOP.has(next)) {
      toast.error(`Open ${lead.name} on desktop to move it to ${next}`)
      return
    }

    const previous = stage
    setStage(next)
    updateLeadStage(lead.id, next).catch((err) => {
      setStage(previous)
      // Show the server's own message when it has one — "Enter the new
      // premium before marking this lead Sold" tells the broker what to do
      // next, where the generic line only says it failed.
      toast.error(firstValidationMessage(err) ?? `Couldn't move ${lead.name} to ${next}`)
    })
  }

  // A pushed screen starts at the top. Without this, opening a lead from
  // the bottom of a long list lands mid-page — the two screens are one
  // document and share its scroll position, where on iOS they would be
  // separate views on a stack.
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [lead.id])

  // Separate from moreOpen: that sheet holds the three overflow stages for the
  // bottom bar, this one holds all seven. Sharing a single flag would make the
  // badge open a sheet missing the four stages most likely to be wanted.
  const [stagePickerOpen, setStagePickerOpen] = useState(false)
  // Opens already true when the broker came from the renewal WhatsApp — the
  // button named a form, so it should be on screen.
  const [addingReminder, setAddingReminder] = useState(openReminder)
  const [reminderNote, setReminderNote] = useState('')
  const [reminderDue, setReminderDue] = useState('')
  // The wheel picker's own visibility, separate from addingReminder:
  // the form stays on screen behind the sheet.
  const [pickingDue, setPickingDue] = useState(false)
  const [savingReminder, setSavingReminder] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)

  const [reminders, setReminders] = useState<Reminder[]>([])
  const [calls, setCalls] = useState<Contact[]>([])
  const [callsFailed, setCallsFailed] = useState(false)
  const [loadingDetail, setLoadingDetail] = useState(true)
  const [savingContact, setSavingContact] = useState(false)

  /** Hand this customer to the phone's own address book, so the next call
   *  from them arrives with a name on it rather than bare digits.
   *
   *  The card is fetched, not linked: the route needs the auth header. A
   *  failure is toasted with the server's own message — a lead with no phone
   *  number comes back 409 saying exactly that, which is something the broker
   *  can act on. */
  async function saveContact() {
    setSavingContact(true)
    try {
      await saveLeadContact(lead.id)
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not save that contact')
    } finally {
      setSavingContact(false)
    }
  }

  // SEPARATELY, not in one Promise.all. They were loaded together and shared a
  // single .catch, so a failure in EITHER cleared BOTH — and the screen then
  // said "No calls yet" and "Nothing outstanding", which is a statement about
  // the customer rather than about a failed request. A broker would have read
  // it as "nobody has rung this person".
  const loadCalls = useCallback(() => {
    getLeadContacts(lead.id)
      .then((c) => {
        setCalls(c)
        setCallsFailed(false)
      })
      .catch(() => setCallsFailed(true))
      .finally(() => setLoadingDetail(false))
  }, [lead.id])

  useEffect(() => {
    getLeadReminders(lead.id)
      .then((r) => {
        // Open ones only. A completed follow-up is history, and this screen is
        // about what still needs doing.
        setReminders(r.filter((x) => x.status === 'Open'))
      })
      .catch(() => setReminders([]))
  }, [lead.id])

  useEffect(loadCalls, [loadCalls])

  // BACK FROM THE PHONE APP. tel: hands off to the OS dialer and this tab
  // goes background for the call; nothing on this screen asked again when it
  // comes back to the foreground, so a call logged elsewhere in the meantime
  // would be missing until the lead was reopened. Refetching on visibility is
  // the one moment we know the broker is looking again.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') loadCalls()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [loadCalls])

  // `digits` lived here — lead.phone stripped of punctuation, for wa.me. It
  // produced a number WhatsApp cannot route: no country code. phone_e164
  // (0058) is the form that works, and it comes down with the lead.

  return (
    // min-h-dvh — see the note on the list screen. pb-32 clears the fixed
    // stage bar below.
    <div className="bg-background min-h-dvh pb-32">
      {/* iOS back: chevron plus the previous screen's title, in the tint
          colour — never a boxed button. */}
      <header className="bg-background sticky top-0 z-10 px-2 pt-[calc(0.5rem+env(safe-area-inset-top))] pb-2">
        <button
          type="button"
          onClick={onBack}
          className="text-primary mx-auto -ml-1 flex min-h-[44px] w-full max-w-[var(--width-mobile)] items-center gap-0.5 px-2 text-[17px]"
        >
          <ChevronLeft className="size-6" />
          Leads
        </button>
      </header>

      {/* Identity, centred with room around it — a contact card, not a page
          header. */}
      <div className="mx-auto flex max-w-[var(--width-mobile)] flex-col items-center gap-2 px-4 pt-2 pb-6">
        <h1 className="text-center text-[26px] leading-tight font-semibold">{lead.name}</h1>
        <p className="text-muted-foreground text-[17px]">{lead.phone}</p>
        {/* THE STAGE, READ ONLY HERE. This was a button that opened a stage
            sheet — a SECOND way to move a lead, on a screen that already has
            the bar at the bottom for exactly that. Two controls for one idea,
            and the badge is where a broker looks to READ the stage before they
            ring. It reads now, and nothing more. */}
        {/* The label shows only while the stage is still the one it was
            fetched with. `stage` is local state and moves on tap; sub_stage is
            what the server last sent. Every move CLEARS the label, so once the
            two disagree the label would name something the lead no longer
            has. */}
        <LeadStageBadge stage={stage} subStage={stage === lead.stage ? lead.sub_stage : null} />
      </div>

      {/* The three channels — equal-width tinted squares, icon over label. iOS
          weights these equally because a broker picks by CHANNEL, not by
          importance: some customers answer WhatsApp and never a call. */}
      <div className="mx-auto grid max-w-[var(--width-mobile)] grid-cols-3 gap-2 px-3">
        <ContactAction
          href={`tel:${lead.phone}`}
          icon={<Phone className="size-[22px]" />}
          label="call"
        />
        {/* ONE WhatsApp, not two. "message" and "whatsapp" pointed at the
            IDENTICAL wa.me URL — the second wearing a phone-call icon, which
            promised a voice call and opened a chat. Three channels, three
            destinations.

            wa.me needs a full international number: phone_e164 (0058), not
            digits stripped off the display form. "4168274934" without the
            country code is a number WhatsApp cannot route. */}
        <ContactAction
          href={
            lead.phone_e164
              ? `https://wa.me/${lead.phone_e164.replace(/\D/g, '')}`
              : undefined
          }
          disabled={!lead.phone_e164}
          icon={<MessageCircle className="size-[22px]" />}
          label="whatsapp"
        />
        <ContactAction
          href={isPlaceholderContact(lead.email) ? undefined : `mailto:${lead.email}`}
          disabled={isPlaceholderContact(lead.email)}
          icon={<Mail className="size-[22px]" />}
          label="email"
        />
      </div>
      <p className="text-muted-foreground mx-auto max-w-[var(--width-mobile)] px-5 pt-2 text-[13px]">
        Calls open your phone's dialer.
      </p>

      {/* ── Follow-ups ─────────────────────────────────────────────────── */}
      <Group
        title="Follow-ups"
        action={
          !addingReminder && (
            <button
              type="button"
              onClick={() => setAddingReminder(true)}
              // min-h-44 + negative margin: the TARGET grows to Apple's
              // minimum while the text stays optically aligned with the
              // section heading beside it. Measured at 23px before this.
              className="text-primary -my-2 inline-flex min-h-[44px] items-center gap-1 px-2 text-[15px]"
            >
              <Plus className="size-4" />
              Add
            </button>
          )
        }
      >
        {loadingDetail ? (
          <p className="text-muted-foreground px-4 py-3.5 text-[17px]">Loading…</p>
        ) : reminders.length === 0 && !addingReminder ? (
          <p className="text-muted-foreground px-4 py-3.5 text-[17px]">Nothing outstanding</p>
        ) : (
          reminders.map((r, i) => (
            <Row key={r.id} last={i === reminders.length - 1 && !addingReminder}>
              <div className="min-w-0 flex-1 py-3">
                <p className="text-[17px] leading-snug">{r.note}</p>
                <p className="text-muted-foreground text-[15px]">{whenDue(r.due_at)}</p>
              </div>
              <button
                type="button"
                onClick={() => {
                  // Removed from the list at once — a broker ticking one off
                  // wants it gone, not a spinner. Put back if the write fails,
                  // because a follow-up that silently survives being completed
                  // is one a broker chases twice.
                  setReminders((cur) => cur.filter((x) => x.id !== r.id))
                  completeReminder(r.id).catch(() => {
                    setReminders((cur) => [...cur, r])
                    toast.error("Couldn't complete that follow-up")
                  })
                }}
                // min-w-11 as well as min-h: Apple's target is 44x44, and
                // "Done" at 15px is narrower than that on its own.
                className="text-primary min-h-[44px] min-w-11 shrink-0 px-1 py-3 text-[15px] transition-opacity duration-[120ms] ease-out active:opacity-50"
              >
                Done
              </button>
            </Row>
          ))
        )}

        {addingReminder && (
          <div className="border-border space-y-2 border-t p-4">
            {/* No autoFocus. On iOS it throws the keyboard up the instant Add
                is tapped and scrolls the page under it — the form should
                appear first and let the broker choose to type. */}
            <input
              value={reminderNote}
              onChange={(e) => setReminderNote(e.target.value)}
              placeholder="What to do"
              className="bg-muted h-[44px] w-full rounded-[10px] px-3 text-[17px] outline-none"
            />
            {/* A BUTTON, not an input. `datetime-local` renders a different
                control on every platform — a wheel on iOS, Material's dialog
                on Android — so a broker met a different picker depending on
                the phone in their hand. This opens one we own. */}
            <button
              type="button"
              onClick={() => setPickingDue(true)}
              className={cn(
                'bg-muted flex h-[44px] w-full items-center rounded-[10px] px-3 text-left text-[17px]',
                !reminderDue && 'text-muted-foreground',
              )}
            >
              {reminderDue ? whenDue(reminderDue) : 'When to follow up'}
            </button>
            <WheelDateTimePicker
              open={pickingDue}
              onOpenChange={setPickingDue}
              value={reminderDue}
              onChange={setReminderDue}
            />
            <div className="flex gap-2 pt-1">
              <button
                type="button"
                // Both fields required: a follow-up with no date never surfaces
                // again, and one with no text says nothing when it does.
                disabled={!reminderNote.trim() || !reminderDue || savingReminder}
                onClick={() => {
                  setSavingReminder(true)
                  createReminder(lead.id, {
                    // Callback is the overwhelming majority of what a broker
                    // sets from a phone. The other kinds live on the desktop,
                    // where there is room to choose.
                    kind: 'Callback',
                    due_at: new Date(reminderDue).toISOString(),
                    note: reminderNote.trim(),
                  })
                    .then((created) => {
                      setReminders((cur) => [...cur, created])
                      setAddingReminder(false)
                      setReminderNote('')
                      setReminderDue('')
                    })
                    .catch(() => toast.error("Couldn't save that follow-up"))
                    .finally(() => setSavingReminder(false))
                }}
                className="bg-primary text-primary-foreground h-[44px] flex-1 rounded-[10px] text-[17px] font-medium disabled:opacity-50"
              >
                {savingReminder ? 'Saving…' : 'Set follow-up'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setAddingReminder(false)
                  setReminderNote('')
                  setReminderDue('')
                }}
                className="text-primary h-[44px] px-4 text-[17px]"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </Group>

      {/* ── Previous calls ───────────────────────────────────────────────
          THE SHAPE OF iOS RECENTS. One line per call: what kind of call it
          was, and when. No pill, no badge, no second row of metadata — iOS
          carries the outcome in the COLOUR of the line and a small arrow
          glyph, which is read at a glance where a coloured pill has to be
          parsed.

          Missed is red because it is the only outcome that is a to-do. */}
      <Group title="Contact history">
        {loadingDetail ? (
          <p className="text-muted-foreground px-4 py-3.5 text-[17px]">Loading…</p>
        ) : callsFailed ? (
          /* A FAILED LOAD IS NOT AN EMPTY HISTORY. "No calls yet" is a
             statement about the customer; this is a statement about the
             network, and a broker must never read one as the other. */
          <button
            type="button"
            onClick={loadCalls}
            className="active:bg-muted flex w-full items-center gap-2 px-4 py-3.5 text-left text-[17px]"
          >
            <span className="text-muted-foreground flex-1">Couldn't load contact history</span>
            <span className="text-primary">Retry</span>
          </button>
        ) : calls.length === 0 ? (
          <p className="text-muted-foreground px-4 py-3.5 text-[17px]">No contact yet</p>
        ) : (
          calls.map((c, i) => {
            const missed = c.outcome !== 'Answered'
            return (
              <Row key={c.id} last={i === calls.length - 1}>
                <div className="flex min-w-0 flex-1 items-center gap-2.5 py-3">
                  <MessageSquare
                    className={cn('size-4 shrink-0', missed ? 'text-destructive' : 'text-muted-foreground')}
                  />

                  <div className="min-w-0 flex-1">
                    <p className={cn('truncate text-[17px] leading-tight', missed && 'text-destructive')}>
                      {c.outcome}
                    </p>
                  </div>

                  {/* 13px, matching the duration below the outcome. iOS runs
                      its Recents timestamp at Footnote, the same size as the
                      secondary line — the 17px Body weight belongs to the
                      outcome alone, which is the one thing being scanned. */}
                  <span className="text-muted-foreground shrink-0 text-[13px]">
                    {whenDue(c.occurred_at)}
                  </span>
                </div>
              </Row>
            )
          })
        )}
      </Group>

      {/* ── Save to the phone ────────────────────────────────────────────
          BELOW CONTACT HISTORY, which is where iOS puts "Create New Contact"
          on a Recents entry — under the calls, not up beside the channels. A
          broker reaches for it once per customer, after they have spoken;
          the three squares at the top are the things they reach for daily.

          NO HEADING AND NO ICON, unlike every other section on this screen.
          iOS prints this as a bare line of tint-coloured text on its own card,
          and it is right to: a heading would name a section holding one row,
          and the label already says exactly what the row does. This is the one
          place the screen deliberately does NOT use Group.

          It exists because the CRM cannot name a ringing phone. The call
          screen belongs to the OS, and the OS only knows the names in its own
          address book — so putting the customer there is the only way a
          callback arrives as a name rather than ten digits. */}
      <section className="mx-auto mt-6 max-w-[var(--width-mobile)]">
        <div className="bg-card mx-3 overflow-hidden rounded-[10px]">
          <button
            type="button"
            onClick={saveContact}
            disabled={savingContact}
            className="text-primary active:bg-muted flex min-h-[44px] w-full items-center px-4 py-3 text-left text-[17px] disabled:opacity-50"
          >
            {savingContact ? 'Saving…' : 'Create New Contact'}
          </button>
        </div>
      </section>

      {/* ── Stage ────────────────────────────────────────────────────────
          PINNED TO THE BOTTOM, not a section in the scroll. Moving the stage is
          what a broker does the moment they hang up, and a section further down
          the page would mean scrolling past reminders and call history to reach
          it. Here it is always in reach of a thumb, which is where the one
          frequent action belongs on a phone.

          ICONS ONLY. Seven labelled cells could not fit one line without
          wrapping to two, and a bar that tall eats the content above it. The
          current stage is named in the badge under the customer's name and
          again in the label beneath this bar, so nothing depends on
          recognising a glyph cold. */}
      <div className="bg-card border-border fixed inset-x-0 bottom-0 transform-gpu border-t pb-[max(env(safe-area-inset-bottom),1rem)] [contain:layout_paint]">
        {/* Same edge-to-edge treatment as the list's filter bar — see the note
            there. Identical shape on purpose: both answer "which stage", one
            narrowing a list and one moving a lead, and a broker should not
            have to learn two controls for one idea. */}
        <div className="mx-auto flex max-w-[var(--width-mobile)] items-stretch">
          {LEAD_STAGES.map((s) => {
            const Icon = STAGE_ICONS[s.key] ?? CircleDot
            const overflow = !PRIMARY_STAGES.includes(s.key)
            return (
              <FilterCell
                key={s.key}
                active={stage === s.key}
                onClick={() => moveStage(s.key)}
                label={s.label}
                // Overflow stages stay in the overflow, current or not — see
                // the note on the filter bar. The More cell lights instead, and
                // the badge under the customer's name says which stage it is.
                className={cn(overflow && `hidden ${WIDE_BAR}flex`)}
              >
                <Icon className="size-[20px]" />
              </FilterCell>
            )
          })}

          <FilterCell
            active={!PRIMARY_STAGES.includes(stage)}
            onClick={() => setMoreOpen(true)}
            label="More"
            className={`${WIDE_BAR}hidden`}
          >
            <MoreHorizontal className="size-[20px]" />
          </FilterCell>
        </div>
      </div>

      <Sheet open={moreOpen} onOpenChange={setMoreOpen}>
        <SheetContent side="bottom" className="rounded-t-[14px] pb-8">
          <SheetHeader className="pb-2">
            <SheetTitle className="text-[17px]">Move to stage</SheetTitle>
          </SheetHeader>
          <div className="px-4">
            {LEAD_STAGES.filter((s) => !PRIMARY_STAGES.includes(s.key)).map((s, i, arr) => {
              const Icon = STAGE_ICONS[s.key] ?? CircleDot
              const active = stage === s.key
              return (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => {
                    moveStage(s.key)
                    setMoreOpen(false)
                  }}
                  className={cn(
                    'relative flex min-h-[52px] w-full items-center gap-3 text-[17px]',
                    active ? 'text-primary font-medium' : 'text-foreground',
                  )}
                >
                  <Icon className="size-[22px] shrink-0" />
                  {s.label}
                  {active && <Check className="text-primary ml-auto size-5" />}
                  {i < arr.length - 1 && (
                    <span className="bg-border absolute right-0 bottom-0 left-9 h-px" />
                  )}
                </button>
              )
            })}
          </div>
        </SheetContent>
      </Sheet>

      {/* The full stage list, opened from the badge. Same row shape as the
          overflow sheet above — one way to pick a stage, shown in two places
          rather than two different controls to learn. */}
      <Sheet open={stagePickerOpen} onOpenChange={setStagePickerOpen}>
        <SheetContent side="bottom" className="rounded-t-[14px] pb-8">
          <SheetHeader className="pb-2">
            <SheetTitle className="text-[17px]">Move to stage</SheetTitle>
          </SheetHeader>
          <div className="px-4">
            {LEAD_STAGES.map((s, i, arr) => {
              const Icon = STAGE_ICONS[s.key] ?? CircleDot
              const active = stage === s.key
              return (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => {
                    moveStage(s.key)
                    setStagePickerOpen(false)
                  }}
                  className={cn(
                    'relative flex min-h-[52px] w-full items-center gap-3 text-[17px]',
                    active ? 'text-primary font-medium' : 'text-foreground',
                  )}
                >
                  <Icon className="size-[22px] shrink-0" />
                  {s.label}
                  {active && <Check className="text-primary ml-auto size-5" />}
                  {i < arr.length - 1 && (
                    <span className="bg-border absolute right-0 bottom-0 left-9 h-px" />
                  )}
                </button>
              )
            })}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  )
}

/**
 * A grouped section: grey caps label in the margin, white rounded card beneath.
 * The core iOS Settings/Contacts shape, and most of why this reads as an app
 * rather than a web page squeezed onto a phone.
 */
function Group({
  title,
  action,
  children,
}: {
  title: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="mx-auto mt-6 max-w-[var(--width-mobile)]">
      <div className="flex items-baseline justify-between px-5 pb-1.5">
        <h2 className="text-muted-foreground text-[13px] uppercase">{title}</h2>
        {action}
      </div>
      <div className="bg-card mx-3 overflow-hidden rounded-[10px]">{children}</div>
    </section>
  )
}

/**
 * One row inside a Group, carrying the inset separator iOS uses — starting at
 * the content rather than the card edge, and absent on the last row.
 */
function Row({ children, last }: { children: React.ReactNode; last?: boolean }) {
  return (
    <div className="relative flex items-start gap-3 px-4">
      {children}
      {!last && <div className="bg-border absolute right-0 bottom-0 left-4 h-px" />}
    </div>
  )
}

/**
 * One of the three contact channels. Tinted square, icon over a lowercase
 * label — the shape iOS puts under a contact's name.
 */
/** One of the three channel squares.
 *
 *  HREF OR ONCLICK. Some channels are plain links; others need a handler.
 *  Both render identically — the styling lives here once rather than being
 *  copied to make room for a button. */
function ContactAction({
  href,
  onClick,
  icon,
  label,
  disabled,
}: {
  href?: string
  onClick?: () => void
  icon: React.ReactNode
  label: string
  /** No number, or a "(not provided)" email. Dimmed and inert rather than
   *  hidden: a gap where a square used to be looks like a layout bug, and a
   *  live-looking button that does nothing is worse than a dim one. */
  disabled?: boolean
}) {
  const className =
    // active:scale-[0.97] at 120ms — the press confirms itself before the
    // handoff to the dialer or WhatsApp, which is the moment a broker needs to
    // know the tap registered. transform-gpu so the scale does not repaint the
    // card's text on a mid-range phone.
    'bg-card text-primary active:bg-muted flex flex-col items-center justify-center gap-1 rounded-[10px] py-3 text-[11px] ' +
    'transform-gpu transition-[transform,background-color] duration-[120ms] ease-out active:scale-[0.97]'

  if (disabled) {
    return (
      <span className={cn(className, 'text-muted-foreground opacity-50')} aria-disabled>
        {icon}
        {label}
      </span>
    )
  }

  if (href) {
    return (
      <a href={href} className={className}>
        {icon}
        {label}
      </a>
    )
  }
  return (
    <button type="button" onClick={onClick} className={className}>
      {icon}
      {label}
    </button>
  )
}
