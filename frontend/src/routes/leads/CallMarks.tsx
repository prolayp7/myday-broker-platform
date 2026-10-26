import { BellPlus, ChevronLeft, Minus, Phone, PhoneMissed, Plus } from 'lucide-react'
import { useState } from 'react'

import { Calendar } from '@/components/ui/calendar'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

import { callbackPresets, ontarioAtTime, presetLabel } from './callbackPresets'

/** Has this customer been reached, and when should we try again.
 *
 * Two counters — picked up, and no answer — plus a follow-up reminder. The
 * counters answer "who have I not rung yet?" when scanning the whole lead
 * list; here they are the place a broker records the call they just made.
 *
 * NOT CALL HISTORY. contact_log (migration 0060) stores one row per contact
 * with a richer outcome vocabulary and is untouched by this; these are two
 * integers on the lead, kept cheap because the list paints them on every row.
 *
 * All styling lives in the `.call-counter*` classes in index.css, not in
 * utility strings here — see the comment above that block for why.
 *
 * Was CallMarkWireframe while it was inert. It is not inert any more.
 */

function Counter({
  count,
  label,
  icon: Icon,
  busy,
  onAdjust,
}: {
  count: number
  label: string
  icon: typeof Phone
  busy: boolean
  onAdjust: (delta: 1 | -1) => void
}) {
  // One bordered pill per outcome: + icon count −. Boxed so the four glyphs
  // read as one control with a number in it — the quantity stepper every cart
  // uses — rather than four loose buttons. Grey only, like the strip it sits
  // on: the colour lives in the lead list, where the scan happens; here the
  // two are told apart by icon shape, not tint.
  //
  // WEIGHT IS MATCHED TO Parse/DASH BESIDE IT, deliberately: everything here
  // stays at muted-foreground, and the pill carries ONE outline with no
  // internal dividers. At foreground weight with two dividers each, the pair
  // read as the heaviest thing on a row whose other controls are borderless
  // text — a stepper out-shouting the button that parses the report.
  return (
    <span className="call-counter" aria-label={label}>
      <button
        type="button"
        aria-label={`${label}: add one`}
        className="call-counter-step"
        // Disabled WHILE SAVING, not just at zero: a double-click would
        // otherwise fire two requests, and both would count.
        disabled={busy}
        onClick={() => onAdjust(1)}
      >
        <Plus className="call-counter-icon" />
      </button>
      <span className="call-counter-value">
        <Icon className="call-counter-icon" />
        <span className="call-counter-number">{count}</span>
      </span>
      <button
        type="button"
        aria-label={`${label}: remove one`}
        className="call-counter-step"
        // Nothing to take back at zero. The server clamps too (greatest(0,...)
        // in 0121), so this is the honest disabled state rather than the guard.
        disabled={busy || count === 0}
        onClick={() => onAdjust(-1)}
      >
        <Minus className="call-counter-icon" />
      </button>
    </span>
  )
}

export function CallMarks({
  answeredCount,
  noAnswerCount,
  busy = false,
  onAdjust,
  onRemind,
}: {
  answeredCount: number
  noAnswerCount: number
  busy?: boolean
  onAdjust: (outcome: 'answered' | 'no_answer', delta: 1 | -1) => void
  /** Create the callback reminder. Resolves when saved; rejects on failure,
   *  which keeps the popover open so the broker can retry. */
  onRemind: (at: Date) => Promise<void>
}) {
  // Which view the reminder popover is showing. Reset on close so it always
  // reopens on the presets.
  const [picking, setPicking] = useState(false)
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [day, setDay] = useState<Date | undefined>()
  const [time, setTime] = useState('09:00')

  // Resolved per render, not at module load: a tab left open overnight would
  // otherwise still be offering yesterday's "tomorrow".
  const presets = callbackPresets()

  async function save(at: Date) {
    // Guards the double-click. Each press creates a SEPARATE reminder — there
    // is no dedupe in the reminder table by design (0061) — so two clicks
    // would mean two WhatsApps.
    if (saving) return
    setSaving(true)
    try {
      await onRemind(at)
      setOpen(false)
      setPicking(false)
      setDay(undefined)
    } catch {
      // Left OPEN on purpose. The existing AddReminderDialog closes before its
      // request resolves and loses what the broker typed; the toast is raised
      // by the caller, and the popover stays put so they can try again.
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex items-center gap-2" aria-label="Call marks">
      <Counter
        count={answeredCount}
        label="Picked up"
        icon={Phone}
        busy={busy}
        onAdjust={(d) => onAdjust('answered', d)}
      />
      <Counter
        count={noAnswerCount}
        label="Not picked up"
        icon={PhoneMissed}
        busy={busy}
        onAdjust={(d) => onAdjust('no_answer', d)}
      />

      {/* A POPOVER OF PRESETS, NOT THE FULL DIALOG. Clicked from the call
          strip the intent is already known — they did not pick up, try again
          later — so the kind is Callback and the only open question is when.
          One click answers it. The full AddReminderDialog is still one row
          away for renewal chases and document waits, which do need its
          fields. */}
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          if (!next) {
            // Back to a clean slate: a day picked and then abandoned must not
            // be waiting, pre-selected, the next time this opens.
            setPicking(false)
            setDay(undefined)
            setTime('09:00')
          }
        }}
      >
        <PopoverTrigger className="call-reminder">
          <BellPlus className="call-counter-icon" />
          Remind
        </PopoverTrigger>

        {/* One width for both views, so switching does not lurch the left
            edge (the popover is anchored on the right). */}
        <PopoverContent align="end" className="w-64 p-1">
          <p className="text-muted-foreground px-2 py-1.5 text-2xs">Remind me to call</p>
          {picking ? (
            <>
              <Calendar
                mode="single"
                selected={day}
                onSelect={setDay}
                // A reminder is always in the future; offering yesterday is
                // offering a mistake. The server does NOT enforce this — due_at
                // has no future validation — so this is the only guard.
                startMonth={new Date()}
                endMonth={new Date(new Date().getFullYear() + 2, 11)}
                disabled={{ before: new Date() }}
              />
              {/* Time sits UNDER the calendar rather than beside it: the date
                  is the decision, the time is usually left at 9am and only
                  touched for "after 2pm" cases. Defaulted, not required. */}
              <div className="call-reminder-time">
                <label htmlFor="call-reminder-at">Time</label>
                <input
                  id="call-reminder-at"
                  type="time"
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                  className="bg-background h-(--height-control) rounded border px-1.5 text-xs"
                />
              </div>
              {/* The choice, read back in the SAME words the presets use, so
                  the two views agree about what was picked. */}
              {day && (
                <p className="text-muted-foreground px-2.5 pb-1.5 text-2xs">
                  {presetLabel(ontarioAtTime(day, time))}
                </p>
              )}
              {/* Footer: the way back on the left, away from the calendar's
                  own month arrows; the one action on the right. */}
              <div className="flex items-center justify-between border-t px-2 py-1.5">
                <button
                  type="button"
                  onClick={() => setPicking(false)}
                  className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-2xs"
                >
                  <ChevronLeft className="size-3" />
                  Presets
                </button>
                <button
                  type="button"
                  className="call-reminder"
                  // Nothing to set until a day is picked, and never twice.
                  disabled={!day || saving}
                  onClick={() => day && save(ontarioAtTime(day, time))}
                >
                  {saving ? 'Setting…' : 'Set'}
                </button>
              </div>
            </>
          ) : (
            <>
              {presets.map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  className="call-reminder-option"
                  disabled={saving}
                  onClick={() => save(preset.at)}
                >
                  {preset.label}
                  <span className="call-reminder-when">{preset.when}</span>
                </button>
              ))}
              <div className="my-1 border-t" />
              <button
                type="button"
                onClick={() => setPicking(true)}
                className="call-reminder-option"
              >
                Pick a date…
              </button>
            </>
          )}
        </PopoverContent>
      </Popover>
    </div>
  )
}
