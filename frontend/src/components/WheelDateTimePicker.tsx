import { useEffect, useMemo, useRef, useState } from 'react'
import { Sheet, SheetContent } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'

// A DATE-AND-TIME PICKER THAT LOOKS THE SAME ON EVERY PHONE.
//
// `<input type="datetime-local">` renders three different controls: iOS gives a
// wheel, Android gives Material's dialog, desktop Chrome gives a dropdown. A
// broker moving between an iPhone and an Android tablet met a different picker
// each time, and neither matched the rest of the app.
//
// THE COST, recorded because it is real: on iOS this REPLACES a native control
// that already looked like this and came with accessibility, haptics and OS
// theming for free. We now own that behaviour. What we buy is one picker
// everywhere and a two-step flow — date, then time — which is how a broker
// actually thinks about a follow-up ("Thursday… afternoon").
//
// SCROLL-SNAP, NOT A DRAG LIBRARY. Native momentum scrolling with
// `snap-mandatory` gives the wheel feel with no gesture handling of our own,
// which is what keeps this a component rather than a project.

/** Rows are 40px; the window shows five, so the centre sits one row from top. */
const ROW_H = 40
const VISIBLE = 5
const PAD = ((VISIBLE - 1) / 2) * ROW_H

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** Days in a month, so 31 February can never be scrolled to. */
function daysIn(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate()
}

/**
 * One scrollable column.
 *
 * The selected value is whatever row is centred — derived from scrollTop on
 * settle rather than tracked per-frame, so a fast flick does not fire a
 * hundred state updates on the way past.
 */
function Wheel({
  values,
  index,
  onIndex,
  label,
  width,
}: {
  values: string[]
  index: number
  onIndex: (i: number) => void
  label: string
  width: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const settle = useRef<ReturnType<typeof setTimeout> | null>(null)
  // True while WE are scrolling the element, so the handler below does not
  // treat a programmatic correction as the user picking something.
  const syncing = useRef(false)

  // Keep the wheel on the selected row when the value changes from outside —
  // clamping a 31st down to a 30th when the month changes, for instance.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const target = index * ROW_H
    if (Math.abs(el.scrollTop - target) < 2) return
    syncing.current = true
    el.scrollTo({ top: target, behavior: 'smooth' })
    // Long enough for the smooth scroll to finish; a scroll event arriving
    // after this is genuinely the user's.
    const t = setTimeout(() => { syncing.current = false }, 300)
    return () => clearTimeout(t)
  }, [index])

  return (
    <div className="flex min-w-0 flex-1 flex-col items-center" style={{ maxWidth: width }}>
      <span className="text-muted-foreground pb-1 text-[11px] tracking-wide uppercase">
        {label}
      </span>
      <div
        ref={ref}
        role="listbox"
        aria-label={label}
        onScroll={() => {
          if (syncing.current) return
          if (settle.current) clearTimeout(settle.current)
          // Fires once the flick has stopped, not on every frame of it.
          settle.current = setTimeout(() => {
            const el = ref.current
            if (!el) return
            const i = Math.round(el.scrollTop / ROW_H)
            const clamped = Math.max(0, Math.min(values.length - 1, i))
            if (clamped !== index) onIndex(clamped)
          }, 120)
        }}
        className="snap-y snap-mandatory overflow-y-auto overscroll-contain scrollbar-none"
        style={{ height: VISIBLE * ROW_H, scrollbarWidth: 'none' }}
      >
        {/* Padding rows so the FIRST and LAST values can reach the centre —
            without them, January could never be selected. */}
        <div style={{ height: PAD }} aria-hidden />
        {values.map((v, i) => (
          <div
            key={v}
            role="option"
            aria-selected={i === index}
            onClick={() => onIndex(i)}
            className={cn(
              'flex snap-center items-center justify-center text-[17px] tabular-nums transition-colors',
              i === index
                ? 'text-foreground font-semibold'
                : 'text-muted-foreground/60',
            )}
            style={{ height: ROW_H }}
          >
            {v}
          </div>
        ))}
        <div style={{ height: PAD }} aria-hidden />
      </div>
    </div>
  )
}

/**
 * Pick a date, then a time. Returns the value `datetime-local` would have —
 * `YYYY-MM-DDTHH:mm` in LOCAL wall-clock, which is what the caller already
 * passes to `new Date(...)` before sending.
 *
 * TWO STEPS, not one screen of five wheels. A follow-up is thought of as
 * "Thursday, afternoon" — the date narrows the question, and asking both at
 * once puts five columns on a phone where each is too narrow to read.
 */
export function WheelDateTimePicker({
  open,
  onOpenChange,
  value,
  onChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Current `YYYY-MM-DDTHH:mm`, or '' for "nothing chosen yet". */
  value: string
  onChange: (value: string) => void
}) {
  const [step, setStep] = useState<'date' | 'time'>('date')

  // Seeded from the current value, or from a sensible default: tomorrow at
  // 9am. NOT "now" — a reminder set for the moment you are looking at the
  // screen has already passed by the time you save it.
  const initial = useMemo(() => {
    if (value) {
      const d = new Date(value)
      if (!Number.isNaN(d.getTime())) return d
    }
    const d = new Date()
    d.setDate(d.getDate() + 1)
    d.setHours(9, 0, 0, 0)
    return d
  }, [value])

  const [year, setYear] = useState(initial.getFullYear())
  const [month, setMonth] = useState(initial.getMonth())
  const [day, setDay] = useState(initial.getDate())
  const [minutes, setMinutes] = useState(initial.getHours() * 60 + initial.getMinutes())

  // Re-seed each time the sheet opens, so reopening after a cancel does not
  // show last time's half-made choice.
  useEffect(() => {
    if (!open) return
    setStep('date')
    setYear(initial.getFullYear())
    setMonth(initial.getMonth())
    setDay(initial.getDate())
    setMinutes(initial.getHours() * 60 + initial.getMinutes())
  }, [open, initial])

  // This year and the next two. A follow-up is never set for 2041, and a
  // hundred-row year wheel makes the useful ones harder to reach.
  const thisYear = new Date().getFullYear()
  const years = useMemo(
    () => [thisYear, thisYear + 1, thisYear + 2].map(String),
    [thisYear],
  )

  const dayCount = daysIn(year, month)
  const days = useMemo(
    () => Array.from({ length: dayCount }, (_, i) => String(i + 1)),
    [dayCount],
  )

  // A 31st that survives a month change would produce an invalid date, so it
  // is clamped the moment the month can no longer hold it.
  useEffect(() => {
    if (day > dayCount) setDay(dayCount)
  }, [day, dayCount])

  // Every half hour, 07:00 to 21:30. NOT the full 24 hours the screenshot
  // shows: a broker does not set a customer callback for 3am, and 48 rows of
  // which 20 are unusable makes the real ones slower to reach.
  const times = useMemo(() => {
    const out: { label: string; minutes: number }[] = []
    for (let m = 7 * 60; m <= 21 * 60 + 30; m += 30) {
      const h = Math.floor(m / 60)
      const mm = m % 60
      const suffix = h < 12 ? 'AM' : 'PM'
      const h12 = h % 12 === 0 ? 12 : h % 12
      out.push({ label: `${h12}:${String(mm).padStart(2, '0')} ${suffix}`, minutes: m })
    }
    return out
  }, [])

  const timeIndex = Math.max(
    0,
    times.findIndex((t) => t.minutes >= minutes),
  )

  function commit() {
    const chosen = times[timeIndex] ?? times[0]
    const d = new Date(year, month, day, Math.floor(chosen.minutes / 60), chosen.minutes % 60)
    // LOCAL wall-clock, matching what datetime-local produced — the caller
    // converts to ISO. Building the string by hand rather than via
    // toISOString(), which would shift it by the UTC offset.
    const pad = (n: number) => String(n).padStart(2, '0')
    onChange(
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
      `T${pad(d.getHours())}:${pad(d.getMinutes())}`,
    )
    onOpenChange(false)
  }

  const dateLabel = `${MONTHS[month]} ${day}, ${year}`

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className="rounded-t-[14px] px-4 pb-8">
        {/* The header doubles as the back affordance on step two — the title
            says which question is being asked, so a separate breadcrumb would
            be the same word twice. */}
        <div className="flex items-center justify-between py-3">
          <button
            type="button"
            onClick={() => (step === 'time' ? setStep('date') : onOpenChange(false))}
            className="text-muted-foreground min-h-[44px] px-1 text-[15px] active:opacity-50"
          >
            {step === 'time' ? 'Back' : 'Cancel'}
          </button>
          <span className="text-[17px] font-semibold">
            {step === 'date' ? 'Pick a date' : dateLabel}
          </span>
          <button
            type="button"
            onClick={() => (step === 'date' ? setStep('time') : commit())}
            className="text-primary min-h-[44px] px-1 text-[15px] font-medium active:opacity-50"
          >
            {step === 'date' ? 'Next' : 'Done'}
          </button>
        </div>

        <div className="relative">
          {/* The selection band. Sits BEHIND the wheels (-z-0 + pointer-events
              none) so it marks the centre row without intercepting the scroll
              that moves it. */}
          <div
            aria-hidden
            className="bg-muted/60 pointer-events-none absolute inset-x-0 rounded-[10px]"
            style={{ top: PAD + 22, height: ROW_H }}
          />

          {step === 'date' ? (
            <div className="relative flex gap-2">
              <Wheel
                label="Day"
                values={days}
                index={day - 1}
                onIndex={(i) => setDay(i + 1)}
                width="26%"
              />
              <Wheel
                label="Month"
                values={MONTHS}
                index={month}
                onIndex={setMonth}
                width="38%"
              />
              <Wheel
                label="Year"
                values={years}
                index={Math.max(0, years.indexOf(String(year)))}
                onIndex={(i) => setYear(Number(years[i]))}
                width="36%"
              />
            </div>
          ) : (
            <div className="relative flex">
              <Wheel
                label="Time"
                values={times.map((t) => t.label)}
                index={timeIndex}
                onIndex={(i) => setMinutes(times[i].minutes)}
                width="100%"
              />
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
