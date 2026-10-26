/** When "Tomorrow" actually is, in the timezone the backend reads.
 *
 * WHY THIS FILE EXISTS. The obvious `new Date(value).toISOString()` — which is
 * what AddReminderDialog does — resolves a wall-clock time in the BROWSER's
 * timezone. Every backend read is pinned to America/Toronto
 * (services/reminder.py::_TZ), so a broker on a laptop set to another zone
 * would set a 9am reminder that fires at some other hour. The offset is
 * computed here instead of assumed, so it stays right across DST.
 */

const TZ = 'America/Toronto'

/** Ontario's UTC offset in minutes on a given instant (-240 in EDT, -300 in
 *  EST). Derived by formatting the same instant in both zones and taking the
 *  difference — no hardcoded offset to go stale twice a year. */
function ontarioOffsetMinutes(at: Date): number {
  const asUtc = new Date(at.toLocaleString('en-US', { timeZone: 'UTC' }))
  const asOntario = new Date(at.toLocaleString('en-US', { timeZone: TZ }))
  return (asOntario.getTime() - asUtc.getTime()) / 60_000
}

/** An ISO instant for `hour:minute` Ontario time, `daysAhead` days from today.
 *
 * Built from Ontario's OWN calendar day, not the browser's: a broker in
 * Vancouver at 10pm is already on tomorrow's date in Toronto, and "tomorrow"
 * has to mean tomorrow to the person reading it.
 */
export function ontarioAt(daysAhead: number, hour = 9, minute = 0): Date {
  const now = new Date()
  const wall = new Date(now.toLocaleString('en-US', { timeZone: TZ }))

  wall.setDate(wall.getDate() + daysAhead)
  wall.setHours(hour, minute, 0, 0)

  // `wall` reads as if those wall-clock numbers were local time. Shifting by
  // the difference between the two zones turns it into the real instant that
  // clock shows in Toronto.
  const localOffset = -now.getTimezoneOffset()
  const toInstant = (offset: number) =>
    new Date(wall.getTime() - (offset - localOffset) * 60_000)

  // TWO PASSES, FOR DST. Ontario's offset on the TARGET day is what matters,
  // and it can differ from today's: "next week" set on the Sunday before the
  // clocks change would otherwise land an hour off. A first guess using
  // today's offset gets us to the right day; re-reading the offset on that
  // day and shifting again gets the hour right.
  const guess = toInstant(ontarioOffsetMinutes(now))
  return toInstant(ontarioOffsetMinutes(guess))
}

/** Whole calendar days from today to `day`, by the calendar the broker is
 *  looking at. The picker shows the browser's local days, so the count has
 *  to come from the same calendar — the day THEY clicked, not a UTC one. */
export function daysFromToday(day: Date): number {
  const start = new Date(day)
  start.setHours(0, 0, 0, 0)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((start.getTime() - today.getTime()) / 86_400_000)
}

/** The instant for a picked day plus an `HH:MM` string from a time input.
 *
 * NOT `hour || 9`: midnight is hour 0, and `0 || 9` would silently move a
 * 00:30 reminder to 9:30. Only an unparseable field falls back. */
export function ontarioAtTime(day: Date, time: string): Date {
  const [h, m] = time.split(':').map(Number)
  const hour = Number.isFinite(h) ? h : 9
  const minute = Number.isFinite(m) ? m : 0
  return ontarioAt(daysFromToday(day), hour, minute)
}

/** "Mon 21 Sep, 9:00am" — the resolved date shown beside each preset.
 *
 * SPELLED OUT because "next week" is ambiguous, and a broker booking around a
 * renewal needs the actual day without doing the arithmetic. Rendered in
 * Ontario time for the same reason the instant is built in it. */
export function presetLabel(at: Date): string {
  const day = at.toLocaleDateString('en-GB', {
    timeZone: TZ,
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
  const time = at
    .toLocaleTimeString('en-US', {
      timeZone: TZ,
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    })
    .replace(' ', '')
    .toLowerCase()
  return `${day}, ${time}`
}

/** Days from Ontario's today to the next Monday — always at least 1, so
 *  clicking it on a Monday means NEXT Monday rather than this morning. */
function daysToNextMonday(): number {
  const ontarioToday = new Date(
    new Date().toLocaleString('en-US', { timeZone: TZ }),
  )
  // getDay(): 0 Sun .. 6 Sat. ((8 - day) % 7) || 7 lands on the next Monday.
  return ((8 - ontarioToday.getDay()) % 7) || 7
}

export type CallbackPreset = { label: string; at: Date; when: string }

/** The three offers, resolved against today.
 *
 * Computed per render rather than at module load: a tab left open overnight
 * would otherwise offer yesterday's "tomorrow".
 */
export function callbackPresets(): CallbackPreset[] {
  return [
    { label: 'Tomorrow', days: 1 },
    { label: 'In 2 days', days: 2 },
    // NEXT MONDAY, not today+7. A plain week ahead lands on whatever weekday
    // today is — offer it on a Sunday and the broker is reminded to ring
    // someone on a Sunday. "Next week" means the start of the working week.
    { label: 'Next week', days: daysToNextMonday() },
  ].map(({ label, days }) => {
    const at = ontarioAt(days)
    return { label, at, when: presetLabel(at) }
  })
}
