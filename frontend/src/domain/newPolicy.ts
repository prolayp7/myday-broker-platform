// The policy being OFFERED — the quote the broker is building from a call
// with the customer. Nothing here is auto-imported; every value is typed in
// by the broker, including the dates below, which only fall back to a
// sensible default (tomorrow, one year) when nothing has been entered yet.

// An insurer list used to live here as a hardcoded array, then as a real
// table. Both are gone (0083): carriers only ever existed to hold commission
// rates, and there is no commission module to hold them for
// that exists only in the browser — and because the endpoint filters to the
// carriers that can actually write the policy type being quoted.

const DAY_MS = 86_400_000

/** Today at midnight, so date-only comparisons don't trip over the clock. */
function today(): Date {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d
}

function toIso(d: Date): string {
  return d.toISOString().slice(0, 10)
}

/**
 * When the new policy starts.
 *
 * A replacement policy should begin the day the current one ends, so cover is
 * continuous with no gap and no overlap. But an expiry that has already passed
 * can't be used — back-dating cover isn't something we can offer — so the
 * earliest honest start is tomorrow.
 *
 * @param currentExpiry the most recent policy's expiry (ISO), if any
 */
export function proposedEffectiveDate(currentExpiry: string | null | undefined): string {
  const tomorrow = toIso(new Date(today().getTime() + DAY_MS))
  if (!currentExpiry) return tomorrow
  return currentExpiry > toIso(today()) ? currentExpiry : tomorrow
}

/** One year of cover from the effective date — the standard auto term. */
export function proposedExpiryDate(effective: string): string {
  const d = new Date(effective)
  d.setFullYear(d.getFullYear() + 1)
  return toIso(d)
}

/** How far out a renewal can plausibly be. Past this, the date is far more
 *  likely to be a slipped digit than a real policy.
 *
 *  60, NOT 90. A renewal further out than two months is not something a broker
 *  is working now, so a date beyond it is much more often a typed year or month
 *  than a real policy. The wider window let those through. */
export const RENEWAL_PLAUSIBLE_DAYS = 60

/**
 * Why this renewal date looks wrong, or null when it looks fine.
 *
 * THE MESSAGE, NOT A BOOLEAN. The same words are needed twice — the triangle's
 * tooltip and the screen-reader description on the input — and returning them
 * from one place is what stops the two drifting apart.
 *
 * Blank is not wrong. Most of the book has no renewal date at all, and a field
 * nobody has filled in yet is unanswered rather than mistaken.
 *
 * NOT `new Date(iso)`: a bare yyyy-mm-dd parses as UTC midnight, which in
 * Ontario is the previous day — so a renewal dated today would read as past.
 * The components are split out and rebuilt in LOCAL time instead, matching
 * today() above.
 *
 * NOT (a - b) / DAY_MS EITHER, which is the subtler trap. A DST change makes
 * some days 23 or 25 hours, so subtracting two local midnights gives 90.0416…
 * for a span that is exactly 90 calendar days — measured, 213 of 400
 * consecutive 90-day spans in America/Toronto are non-integer, so a `> 90`
 * test would warn falsely on over half the year. Date.UTC on the already-local
 * components gives a stable integer day index with no zone in play.
 */
export function renewalWarning(iso: string | null | undefined): string | null {
  if (!iso) return null

  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!match) return null
  const [y, m, d] = [Number(match[1]), Number(match[2]), Number(match[3])]

  // new Date(2026, 1, 31) silently becomes March 3. An impossible date must
  // fall through to DateField's own red invalid treatment, not collect an
  // amber triangle as well — one field cannot say two contradictory things.
  const local = new Date(y, m - 1, d)
  if (local.getFullYear() !== y || local.getMonth() !== m - 1 || local.getDate() !== d) {
    return null
  }

  const now = today()
  const delta =
    Date.UTC(y, m - 1, d) / DAY_MS -
    Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) / DAY_MS

  // A PAST DATE IS NOT A MISTAKE, and warning on one was wrong.
  //
  // It reads as an error on the commonest thing in the book: a renewal that
  // has simply come and gone. The broker knows — they were there — so the
  // triangle told them nothing and appeared on more rows than any real problem
  // did. A warning that is usually noise stops being read when it matters.
  //
  // Only a date too far AHEAD is checked now, because that is the one a broker
  // cannot see is wrong: a slipped year looks like an ordinary date.
  if (delta > RENEWAL_PLAUSIBLE_DAYS) {
    // MONTHS, NOT DAYS. "194 days away" is a number the reader has to divide
    // before it means anything; "about 6 months away" is the unit a policy term
    // is actually thought in, and the wrongness is obvious at a glance.
    //
    // ROUNDED, and "about" says so. A renewal 194 days out is 6.4 months, and
    // reporting 6.4 would imply a precision the warning does not have — the
    // point is the order of magnitude, not the arithmetic.
    //
    // 30.44 is the mean month length (365.25 / 12). Using 30 would drift by
    // nearly a week across a year, which is visible once the figure is in
    // months rather than days.
    const months = Math.round(delta / 30.44)

    // A YEAR OR MORE IS SAID AS A YEAR. "about 12 months away" is arithmetically
    // right and rhetorically useless — a slipped year is the commonest way this
    // date goes wrong, and naming the unit is what makes it obvious. At 13+
    // months "over a year" beats "13 months" for the same reason.
    const span =
      months >= 12 ? (months === 12 ? 'about a year' : 'over a year') : `about ${months} months`

    // A FULL SENTENCE, because this is a tooltip rather than inline text: on
    // hover there is room to say what is wrong AND what to do, and "re-check
    // it" is the part a broker acts on.
    //
    // THE THRESHOLD IS NAMED because the number alone does not justify itself:
    // "about 2 months away" sounds perfectly reasonable, and without "more than
    // 60 days" a broker reads the warning as the system being wrong rather than
    // the date. Just past the line is exactly where that matters most.
    return `This renewal is ${span} away — more than ${RENEWAL_PLAUSIBLE_DAYS} days. Please re-check it.`
  }
  return null
}
