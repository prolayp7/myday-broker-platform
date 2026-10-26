import { useRef, useState } from 'react'
import { Download, Loader2, Paperclip } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  uploadDashReport,
  parseDashReportUrl,
  dashReportUrl,
  firstValidationMessage,
  type DashPrefillResult,
} from '@/data/api'

// Fill the applicant from the DASH report the broker already pulled.
//
// INLINE, not behind a dialog. A dialog would put two clicks between the
// broker and a field they are going to paste into anyway — the link is
// usually already on their clipboard when they reach this panel.
//
// OPTIONAL, and that word is doing work. Migration 0048 deleted an earlier
// version of this on the instruction "no auto-import of any kind" — the broker
// reads DASH's own site and types what they see. This returns as a CHOICE
// beside that: nothing about typing changes, and nothing already typed is
// overwritten.

/** Column names read back as something a broker recognises. */
const FIELD_LABEL: Record<string, string> = {
  name: 'name',
  license_number: 'licence number',
  license_province: 'licence province',
  gender: 'gender',
  marital_status: 'marital status',
  street_address: 'address',
  city: 'city',
  province: 'province',
  postal_code: 'postal code',
}

function describe(result: DashPrefillResult): string {
  const filled = result.driver_filled.length
  const cars = result.vehicles_added
  const people = result.drivers_added

  if (!filled && !cars && !people) {
    // Not a failure. A report whose fields are all already typed is the normal
    // outcome for a broker who got there first, and saying "nothing to fill"
    // is more honest than an error.
    return result.driver_skipped.length
      ? 'Everything in that report was already filled in'
      : 'Nothing could be read from that report'
  }

  const parts: string[] = []
  if (filled) parts.push(`${filled} ${filled === 1 ? 'field' : 'fields'}`)
  // Before the vehicles: a driver appearing on the panel is the bigger
  // surprise, and the one a broker most needs to look at — they arrive with
  // no relationship set.
  if (people) parts.push(`${people} ${people === 1 ? 'driver' : 'drivers'}`)
  if (cars) parts.push(`${cars} ${cars === 1 ? 'vehicle' : 'vehicles'}`)

  // "6 fields, 1 driver and 2 vehicles" — Oxford-less, as a broker would say
  // it out loud.
  const last = parts.pop()
  const summary = parts.length ? `${parts.join(', ')} and ${last}` : last
  return `Filled ${summary}`
}

/** Who the report was about, for the line beside the control.
 *
 *  WHY THIS IS SHOWN AT ALL. Nothing refuses a report for the wrong person —
 *  the broker decides that, and a system that argued with them would turn an
 *  optional convenience into a step in the way. But a report for the spouse,
 *  or for the customer still open in another tab, fills exactly the blank
 *  fields it should not, and "fill blanks only" is no protection when the
 *  fields are blank. So the subject is stated, and the broker can see it.
 *
 *  The name keeps DASH's own LAST,FIRST spelling. The broker is comparing this
 *  against the document in front of them, and tidying it would make that
 *  harder — which is the entire purpose of the line.
 *
 *  THE NAME ALONE, AND THE SHORTEST POSSIBLE LABEL. The licence number was
 *  here too and earned nothing: a broker recognises the wrong PERSON at a
 *  glance, whereas checking a 15-character licence against the PDF is work —
 *  and the licence is on the form itself, which is where it can be verified.
 *  "Report read for" went the same way: this sits in a header strip beside a
 *  224px input, so every word spent on the prefix is a word taken off the name
 *  that follows it. */
function describeSubject(result: DashPrefillResult): string | null {
  return result.subject_name ? `Report: ${result.subject_name}` : null
}

export function DashPrefill({
  driverId,
  hasReport,
  onFilled,
}: {
  driverId: string
  /** Whether a report has already been stored for this driver — decides
   *  whether Download is live. A BOOLEAN, not the driver: this component
   *  needs to know a report exists, not everything about the person. */
  hasReport: boolean
  /** Refetch after a fill — the fields are already on screen and will not show
   *  the new values on their own.
   *
   *  MAY REJECT, and the caller here reports it. A save that worked followed
   *  by a refetch that did not is the one case where a success toast over a
   *  blank form reads as "nothing happened". */
  onFilled: () => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [url, setUrl] = useState('')
  /** Whose report was just read. See the note where it renders. */
  const [subject, setSubject] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)

  /** Open the stored copy in a new tab.
   *
   *  NOT swallowed, unlike the storing side: a broker who clicks Download and
   *  gets silence cannot tell whether the report exists or the link failed. */
  async function download() {
    setDownloading(true)
    try {
      window.open(await dashReportUrl(driverId), '_blank', 'noopener')
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not open that report')
    } finally {
      setDownloading(false)
    }
  }
  const fileInput = useRef<HTMLInputElement>(null)

  async function run(work: () => Promise<DashPrefillResult>) {
    setBusy(true)
    try {
      const result = await work()
      toast.success(describe(result))

      // Said out loud rather than left for the broker to notice. A report
      // that "did nothing" to a field they typed is the correct behaviour,
      // but it looks like a failure unless it is named.
      if (result.driver_skipped.length) {
        const names = result.driver_skipped.map((f) => FIELD_LABEL[f] ?? f)
        toast(`Left as you typed it: ${names.join(', ')}`)
      }

      setSubject(describeSubject(result))
      setUrl('')

      // Separate try: everything above SAVED. A refetch failure after this
      // point is a display problem, and telling the broker "that report could
      // not be read" would be false — the report was read, and their fields
      // are in the database waiting for a refresh.
      try {
        await onFilled()
      } catch {
        toast.warning('Saved — refresh the page to see the new details')
      }
      return
    } catch (err) {
      // Cleared on failure: a line naming the PREVIOUS report, left standing
      // beside a failed one, is worse than no line at all.
      setSubject(null)
      toast.error(firstValidationMessage(err) ?? 'That report could not be read')

      // THE COPY IS ALREADY STORED even though the parse failed — it is
      // written before Azure is called, which is the whole point of keeping
      // one. Without this refetch the path sits in the database and Download
      // stays disabled until the broker reloads the page, on exactly the
      // failure this feature exists to soften.
      //
      // Its own try: the parse error above is what the broker needs to see,
      // and a refetch failure must not replace it with something vaguer.
      try {
        await onFilled()
      } catch {
        // Already reported the real problem.
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    // flex-1, not just shrink: this sits on the quote header beside the
    // renewal date and the stage select, and it has to ABSORB the width they
    // leave rather than hold a size of its own — otherwise the whole DASH
    // group wraps to a second line.
    <div className="flex min-w-0 flex-1 items-center gap-1.5">
      {/* PERSISTENT, not a toast. A toast is gone in four seconds; this is the
          line a broker re-reads when a field looks wrong, which is minutes
          later. It stays until the next report replaces it.

          ON THE SAME LINE, before the input. Stacked underneath it grew this
          control taller than the Section header strip it sits in, and the
          overflow printed across the first row of fields. The header has width
          to spare and none to give vertically, so the line goes sideways —
          truncating if it must, since the name's first few characters are
          what a broker checks. */}
      {subject && (
        <span className="text-muted-foreground min-w-0 truncate text-2xs" title={subject}>
          {subject}
        </span>
      )}

      {/* TAKES WHAT THE ROW HAS LEFT, rather than a fixed 320px. A DASH link
          is a long URL and deserves the width, but this now shares a line with
          the renewal date, the stage select and three buttons — a rigid width
          pushed the whole DASH group onto a second line. flex-1 with a small
          basis lets it absorb the remainder and shrink first when the window
          narrows, which is the right thing to sacrifice: the other controls
          are unreadable at any less than their size, and this one is still
          usable at half.

          RELATIVE, so the working line below can hang off its bottom edge. */}
      <div className="relative min-w-0 flex-1 basis-40">
        <Input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder={busy ? '' : 'Paste a DASH link'}
          disabled={busy}
          aria-label="DASH report link"
          /* WHITE ON THE GREY STRIP. This row sits on bg-muted, and a
             transparent input on a grey band reads as part of the band — the
             field a broker uses most on this screen was the hardest one to
             see. White gives it an edge without introducing a colour: the
             palette here is white and grey only. */
          className="bg-background h-(--height-control) w-full min-w-0 text-xs"
          onKeyDown={(e) => {
            // Enter still works, and is what a broker who has just pasted will
            // reach for — their hands are already on the keyboard. The button
            // beside it is for everyone who does not know that.
            if (e.key === 'Enter' && url.trim() && !busy) {
              run(() => parseDashReportUrl(driverId, url.trim()))
            }
          }}
        />

        {/* THE WHOLE POINT OF THIS COMPONENT'S FEEDBACK. Azure takes several
            seconds on a real report and the request returns only once, at the
            end — so without this the screen simply stops: the input greys, the
            buttons grey, and nothing moves. Brokers read that as hung, and a
            feature believed broken stops being used.

            ABSOLUTELY POSITIONED, and that is not decoration. This component
            lives in Section's `action` slot, whose contract is that it "costs
            no vertical space" (NewPolicyPage's Section). An earlier version of
            this panel grew taller than the header strip and printed across the
            first row of fields — see the note on the subject line above. An
            overlay cannot repeat that: it takes the input's own footprint.

            ONE MESSAGE, NOT STAGES. The backend uploads, calls Azure, parses
            and writes inside a single request that reports nothing until it
            finishes, so a staged "Uploading… → Reading…" would be a timer
            pretending to know something. This says only what is true. */}
        {busy && (
          <div className="pointer-events-none absolute inset-0 flex items-center gap-2 px-2.5">
            <span className="bg-primary/70 h-0.5 w-6 shrink-0 animate-pulse rounded-full" />
            <span className="text-muted-foreground truncate text-xs">
              Reading the DASH report…
            </span>
          </div>
        )}
      </div>

      {/* ALWAYS PRESENT, disabled until there is a link. A control that
          appears and disappears as you type moves everything beside it and
          leaves the broker unsure whether it exists at all — worse than one
          that is simply there, greyed, saying what it needs. */}
      <Button
        type="button"
        size="sm"
        variant="secondary"
        disabled={busy || !url.trim()}
        onClick={() => run(() => parseDashReportUrl(driverId, url.trim()))}
        className="h-(--height-control) shrink-0 text-xs"
      >
        Parse
      </Button>

      <input
        ref={fileInput}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          // Cleared immediately so choosing the SAME file twice fires a change
          // event the second time — otherwise a retry after a failure silently
          // does nothing.
          e.target.value = ''
          if (file) run(() => uploadDashReport(driverId, file))
        }}
      />
      <button
        type="button"
        disabled={busy}
        onClick={() => fileInput.current?.click()}
        title="Upload the DASH PDF instead"
        aria-label="Upload the DASH PDF instead"
        className="text-muted-foreground hover:text-foreground flex shrink-0 items-center transition-colors disabled:opacity-50"
      >
        {busy ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <Paperclip className="size-3.5" />
        )}
      </button>

      {/* DOWNLOAD THE STORED REPORT.
          Disabled until one exists, rather than hidden, for the same reason
          the Parse button is: a control that appears only once it works
          leaves the broker unsure it exists at all. The tooltip says what it
          is waiting for.

          Opens a fresh signed link per click. The bucket is private and the
          URL expires in fifteen minutes, so a link held in state would stop
          working while the page was still open. */}
      <Button
        type="button"
        size="sm"
        variant="secondary"
        disabled={!hasReport || downloading}
        onClick={download}
        title={
          hasReport
            ? 'Open the stored DASH report'
            : 'Available once a DASH report has been uploaded'
        }
        className="h-(--height-control) shrink-0 gap-1 text-xs"
      >
        {downloading ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <Download className="size-3.5" />
        )}
        DASH
      </Button>
    </div>
  )
}
