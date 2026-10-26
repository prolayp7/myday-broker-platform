import { useEffect, useId, useRef, useState } from 'react'
import { CalendarDays } from 'lucide-react'
import { Calendar } from '@/components/ui/calendar'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'

// A date field you can actually TYPE into.
//
// `<input type="date">` looks like the obvious choice and is miserable in
// practice: it splits into yyyy/mm/dd segments so you cannot type a date
// straight through, Backspace clears a whole segment instead of a character,
// the year segment accepts six digits, and the calendar only opens from a tiny
// icon. Brokers enter dates from a customer reading them aloud, at speed.
//
// The implementation detail that matters: a date is a FIXED-WIDTH format, so
// the text is treated as a fixed grid of digit slots (yyyy-mm-dd) rather than a
// string that reflows. Editing the month therefore changes only the month —
// with a reflowing string, replacing "02" with "1" slides the day left and
// "2026-02-16" becomes "2026-11-6". The grid also keeps the caret honest,
// because slot positions never move.

const SLOTS = [0, 1, 2, 3, 5, 6, 8, 9] as const // digit indices in yyyy-mm-dd
const BLANK = 'yyyy-mm-dd'

/** Pull the 8 digit slots out of a display string, '_' where not yet typed. */
function toSlots(display: string): string[] {
  return SLOTS.map((i) => {
    const ch = display[i]
    return ch && /\d/.test(ch) ? ch : '_'
  })
}

/**
 * Render 8 slots back to yyyy-mm-dd.
 *
 * Unfilled slots keep their place as a space rather than collapsing, because
 * toSlots() reads by fixed character index — a string that shrinks would shift
 * every slot after the gap and corrupt the next edit. Clearing the month of
 * "2026-02-16" has to give "2026-  -16", not "2026--16".
 *
 * The tail IS trimmed, so an empty field is '' and a half-typed year is "202"
 * rather than a row of placeholder spaces.
 */
function fromSlots(slots: string[]): string {
  const lastFilled = slots.reduce((last, c, i) => (c === '_' ? last : i), -1)
  if (lastFilled === -1) return ''

  const ch = slots.map((c) => (c === '_' ? ' ' : c))
  const full = `${ch.slice(0, 4).join('')}-${ch.slice(4, 6).join('')}-${ch.slice(6, 8).join('')}`
  // Cut after the last real digit, using its position in the formatted string.
  return full.slice(0, SLOTS[lastFilled] + 1)
}

/** True only for a real calendar date — rejects 2026-02-31 and 2026-13-01. */
function isValidIso(v: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(v)) return false
  const [y, m, d] = v.split('-').map(Number)
  if (m < 1 || m > 12 || d < 1) return false
  // Day 0 of the next month is the last day of this one.
  return d <= new Date(y, m, 0).getDate()
}

/** Character index -> which digit slot the caret sits before. */
function slotAt(charIndex: number): number {
  const i = SLOTS.findIndex((s) => s >= charIndex)
  return i === -1 ? SLOTS.length : i
}

export function DateField({
  value,
  onChange,
  label,
  className,
  warning,
}: {
  /** ISO yyyy-mm-dd, or '' when unset. */
  value: string
  onChange: (v: string) => void
  label: string
  className?: string
  /** Why this date looks wrong, when it does. A real date the caller doubts —
   *  NOT the same thing as `invalid` below, which is a date that cannot exist.
   *  The MESSAGE rather than a flag because aria-describedby needs text with an
   *  id to point at; an svg <title> is not a valid target. */
  warning?: string | null
}) {
  // What the input shows. Tracked separately from `value` so a half-typed date
  // can sit on screen without being pushed upstream as a broken value.
  const [text, setText] = useState(value)
  const inputRef = useRef<HTMLInputElement>(null)
  // Where to put the caret after the next render, when we moved it ourselves.
  const nextCaret = useRef<number | null>(null)
  const [open, setOpen] = useState(false)

  // The calendar's selection, parsed from local parts so it never drifts a day
  // across a timezone the way new Date('2026-02-16') does.
  const selected = (() => {
    if (!isValidIso(text)) return undefined
    const [y, m, d] = text.split('-').map(Number)
    return new Date(y, m - 1, d)
  })()

  // Follow the value when it changes from outside (seeding, or the term dates
  // moving together). Skipped while focused so it never yanks text mid-type.
  useEffect(() => {
    if (document.activeElement !== inputRef.current) setText(value)
  }, [value])

  useEffect(() => {
    if (nextCaret.current !== null && inputRef.current) {
      inputRef.current.setSelectionRange(nextCaret.current, nextCaret.current)
      nextCaret.current = null
    }
  })

  function commit(next: string, caret?: number) {
    setText(next)
    if (caret !== undefined) nextCaret.current = caret
    // Only publish complete, real dates. A partial entry stays local until it
    // becomes one, so downstream never sees "2026-0" or "2026-02-31".
    if (next === '') onChange('')
    else if (isValidIso(next)) onChange(next)
  }

  /**
   * Typing and deleting are handled here rather than in onChange, because a
   * fixed grid needs to know WHICH slot an edit lands in — information the
   * browser's post-edit value has already lost.
   */
  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    const el = e.currentTarget
    const start = el.selectionStart ?? 0
    const end = el.selectionEnd ?? start
    const slots = toSlots(text)

    // A digit overwrites the slot at the caret and steps to the next one.
    if (/^\d$/.test(e.key)) {
      e.preventDefault()
      const slot = slotAt(start)
      if (slot >= SLOTS.length) return
      slots[slot] = e.key
      const nextSlot = Math.min(slot + 1, SLOTS.length - 1)
      // Sit before the following slot so the next digit lands where expected.
      const caret = slot + 1 < SLOTS.length ? SLOTS[nextSlot] : SLOTS[slot] + 1
      commit(fromSlots(slots), caret)
      return
    }

    if (e.key === 'Backspace') {
      e.preventDefault()
      // A selection clears every slot it covers; otherwise clear the one behind.
      if (end > start) {
        SLOTS.forEach((charIdx, i) => {
          if (charIdx >= start && charIdx < end) slots[i] = '_'
        })
        commit(fromSlots(slots), start)
        return
      }
      const slot = slotAt(start) - 1
      if (slot < 0) return
      slots[slot] = '_'
      commit(fromSlots(slots), SLOTS[slot])
      return
    }

    if (e.key === 'Delete') {
      e.preventDefault()
      const slot = slotAt(start)
      if (slot >= SLOTS.length) return
      slots[slot] = '_'
      commit(fromSlots(slots), start)
    }
  }

  /** Paste a whole date at once — the fast path when copying from elsewhere. */
  function handlePaste(e: React.ClipboardEvent<HTMLInputElement>) {
    e.preventDefault()
    const digits = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 8)
    if (!digits) return
    const slots = toSlots('').map((_, i) => digits[i] ?? '_')
    commit(fromSlots(slots))
  }

  // Only a COMPLETE but impossible date is an error (2026-02-31). A field with
  // a gap in it — "2026-  -16" while the broker is still typing — is
  // unfinished, not wrong, and flagging it would cry wolf mid-entry.
  const complete = !toSlots(text).includes('_')
  const invalid = complete && !isValidIso(text)

  // INVALID WINS. Red says the date cannot exist; amber says it exists but
  // looks wrong. Both borders on one field would be saying two things at once,
  // and "2026-02-31" is the harder fact of the two.
  const warned = Boolean(warning) && !invalid
  const warningId = useId()

  return (
    <div className={cn('relative', className)}>
      <Input
        ref={inputRef}
        value={text}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        // Every edit path is handled above; this exists so React does not warn
        // about a controlled input without a change handler.
        onChange={() => {}}
        onBlur={() => {
          // Leaving a half-finished date on screen would be a lie.
          if (text !== '' && !isValidIso(text)) setText(value)
        }}
        aria-label={label}
        aria-invalid={invalid || undefined}
        // NOT aria-invalid: the value is real and will save. It is being
        // questioned, not rejected.
        aria-describedby={warned ? warningId : undefined}
        // THE SAME REASON, ON THE FIELD ITSELF. The triangle beside the label
        // carries it too, but that is a 14px target — a broker looking at a
        // date that is already outlined amber hovers the DATE, not the small
        // icon several inches away. Both now answer.
        //
        // No aria-label conflict: title is ignored for the accessible name when
        // aria-label is present, and the sentence still reaches screen readers
        // through aria-describedby below.
        title={warned ? warning ?? undefined : undefined}
        inputMode="numeric"
        placeholder={BLANK}
        // 10 chars + room for the calendar button.
        className={cn(
          'h-(--height-control) w-full pr-7 text-xs tabular-nums transition-[color,box-shadow,border-color] hover:border-ring/60',
          warned && 'border-warning ring-warning/20 ring-2',
        )}
      />

      {/* The accessible half of the warning. Sighted brokers get the reason
          from either tooltip — the triangle beside the label, or the input
          above — but a title is not reliably announced by screen readers, so
          the sentence also lives here as real text with an id for
          aria-describedby to reach. */}
      {warned && (
        <span id={warningId} className="sr-only">
          {warning}
        </span>
      )}

      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={`${label} — open calendar`}
            className="text-muted-foreground hover:text-foreground focus-visible:ring-ring absolute top-1/2 right-1.5 -translate-y-1/2 rounded-sm p-0.5 transition-colors focus-visible:ring-2 focus-visible:outline-none"
          >
            <CalendarDays className="size-3.5" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-auto p-0">
          <Calendar
            mode="single"
            captionLayout="dropdown"
            // Bounded relative to today rather than to fixed years, so the
            // range never quietly ages out of usefulness.
            //
            // 100 years back covers a date of birth for any licensed driver —
            // Ontario licenses people well into their 90s, and a hard 1940
            // floor left the oldest of them unable to use the picker at all.
            // 10 years ahead covers any policy term without offering a scroll
            // through the rest of the century.
            startMonth={new Date(new Date().getFullYear() - 100, 0)}
            endMonth={new Date(new Date().getFullYear() + 10, 11)}
            selected={selected}
            defaultMonth={selected}
            onSelect={(d) => {
              if (!d) return
              // Local parts, not toISOString(): that converts to UTC and can
              // shift the date by a day for anyone west of Greenwich.
              const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
                d.getDate(),
              ).padStart(2, '0')}`
              commit(iso)
              setOpen(false)
            }}
            autoFocus
          />
        </PopoverContent>
      </Popover>
    </div>
  )
}
