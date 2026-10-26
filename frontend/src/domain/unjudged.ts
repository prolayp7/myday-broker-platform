/** Which leads have been sitting unjudged long enough to say so on screen.
 *
 *  THE GAP THIS CLOSES. A lead's conversion fires on the broker's qualified
 *  answer, not on a stage move (migration 0124). That is what stopped
 *  conversions being lost to a broker who never touched the stage dropdown —
 *  but it also means a lead nobody answers is simply never reported, quietly
 *  and forever, because Meta rejects events older than 7 days.
 *
 *  So something has to notice. This is that something.
 *
 *  A FLAG, NOT A BLOCK, and the distinction is the whole design. Nothing here
 *  prevents a broker doing anything: no modal, no refused stage move, no
 *  disabled control. A prompt that cannot be dismissed teaches people to click
 *  whichever button makes it disappear fastest — and that answer feeds an ad
 *  model that spends money on it. A lead that turns red after a day is a
 *  reminder; a dialog that traps you is a tax on working.
 *
 *  DERIVED, NOT STORED. Everything below is already on the lead payload, so
 *  there is no clock column, no timer, no sweep, and nothing that can drift out
 *  of step with the lead it describes.
 */

import type { MyLead } from '@/data/api'

/** The applicant details a broker collects on the call.
 *
 *  THE SAME NINE update_stage demands for Quote Done
 *  (services/lead.py::_REQUIRED_APPLICANT_FIELDS). Deliberately the same list
 *  rather than a shorter one of this file's own: a second definition would
 *  drift, and "ready to be judged" and "ready to be quoted" are the same
 *  moment in practice — the broker has finished the call.
 */
const APPLICANT_FIELDS = [
  'first_name',
  'last_name',
  'phone',
  'email',
  'date_of_birth',
  'gender',
  'street_address',
  'city',
  'postal_code',
] as const

/** Leads from an ad platform. A manual walk-in has no conversion to lose, and
 *  the backend refuses to judge one at all
 *  (services/lead.py::set_qualification). Website leads are out for the same
 *  reason — they are organic, so reporting them would tell Meta an advert
 *  produced a customer it did not.
 *
 *  TIKTOK IS HERE THOUGH ITS INGESTION IS NOT BUILT. When it arrives, those
 *  leads need the same prompt, and a list that only knew about Meta would go
 *  quietly wrong rather than loudly. */
const AD_SOURCES = new Set(['meta', 'tiktok'])

/** How long a broker gets before the lead says something.
 *
 *  24 HOURS, not three days. The lead is already assigned and the conversion is
 *  already owed — the only thing missing is one tap. Waiting longer does not
 *  make the answer better, it just delays what Meta is told, and the 7-day
 *  window is the hard edge behind that.
 */
export const UNJUDGED_HOURS = 24

/** Has this lead been waiting on its qualified answer for too long?
 *
 *  FOUR CONDITIONS, and each one rules out a lead that would be wrong to flag:
 *
 *    from an ad      — meta or tiktok. A manual or website lead has no
 *                      conversion to lose (see AD_SOURCES)
 *    never answered  — `false` is an answer. Only null, meaning nobody has been
 *                      asked, counts as waiting
 *    has been assigned — assigned_at is null until a broker holds it
 *    past the window — measured from assigned_at, which RESETS on reassignment
 *                      (assign_lead, migrations 0019/0024/0030), so a new
 *                      holder gets their own 24 hours rather than inheriting
 *                      someone else's expired clock
 *    READY TO ANSWER — the applicant's details are in. This is the condition
 *                      that makes the flag mean something: without it the rule
 *                      fires on any old unjudged lead, which on production was
 *                      145 of 200 — a red wall that says nothing. With it, 4.
 *
 *                      A flag the broker cannot act on is worse than none: it
 *                      asks for an answer while the thing needed to give it is
 *                      still missing, and a prompt that is usually wrong gets
 *                      ignored when it is right.
 *
 *  NO EXPLICIT "is it assigned" CHECK beyond assigned_at being present: this
 *  runs over the broker's OWN list, so every lead here is already theirs, and
 *  assigned_at is null exactly when nobody has held it.
 */
export function isUnjudgedTooLong(lead: MyLead, now: number = Date.now()): boolean {
  if (!AD_SOURCES.has(lead.source)) return false
  // A DEAD LEAD NEEDS NO CHASING. Lost means the broker already decided this
  // one is going nowhere, and asking them to judge it for Meta's benefit is
  // work with no outcome — an unqualified answer and a lost answer say the
  // same thing about the conversion. Measured: 3 of the first 4 leads this
  // rule caught were Lost, which is what a prompt nobody should act on looks
  // like.
  if (lead.stage === 'Lost') return false
  if (lead.is_qualified !== null) return false
  if (!lead.assigned_at) return false
  if (!hasApplicantDetails(lead)) return false

  // NaN from an unparseable date fails this comparison, which is the safe
  // direction: a lead is not flagged because its timestamp was unreadable.
  const assigned = new Date(lead.assigned_at).getTime()
  return now - assigned >= UNJUDGED_HOURS * 60 * 60 * 1000
}

/** Has the broker got everything they need to answer?
 *
 *  THE PRINCIPAL DRIVER ONLY. A spouse's record is not the applicant, and
 *  build_user_data reads the principal alone — so a lead whose spouse is filled
 *  in and whose applicant is not is not ready.
 *
 *  NO DRIVER AT ALL MEANS NOT READY, which is the common case for a lead nobody
 *  has opened. Those are the leads the flag would be loudest about and least
 *  useful on.
 */
function hasApplicantDetails(lead: MyLead): boolean {
  const applicant = lead.driver?.find((d) => d.is_principal)
  if (!applicant) return false
  // Trimmed, because an empty string is a field a broker cleared rather than
  // one they filled — the same distinction the backend's own check makes.
  return APPLICANT_FIELDS.every((f) => String(applicant[f] ?? '').trim() !== '')
}

/** How many of these leads are waiting. For the My Day tile. */
export function countUnjudgedTooLong(leads: MyLead[], now: number = Date.now()): number {
  return leads.reduce((n, lead) => (isUnjudgedTooLong(lead, now) ? n + 1 : n), 0)
}
