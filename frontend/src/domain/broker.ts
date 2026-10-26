// Fixed vocabularies for the broker forms (onboarding + edit). Match the
// `broker` table's real enums and schemas/broker.py so the two never drift.

/**
 * How long a broker has had their own leads stopped, for the roster badge
 * ("Paused · 6d") and the banner on their detail screen.
 *
 * NULL IN, NULL OUT. self_paused_at is null for anyone who was already paused
 * when the column was added (migration 0105 deliberately did not invent a date
 * it did not have), and no duration is better than a wrong one — both callers
 * render the paused state without a suffix when this returns null.
 *
 * Shared rather than written twice because both screens must agree: an admin
 * comparing the roster against a profile should never see two different
 * numbers for the same absence.
 *
 * Whole days, floored, counted from the instant — "6d" means six full days
 * have passed, so the day a broker pauses reads "today" rather than "0d".
 */
export function pausedForLabel(pausedAt: string | null | undefined): string | null {
  if (!pausedAt) return null
  const days = Math.floor((Date.now() - new Date(pausedAt).getTime()) / 86_400_000)
  return days < 1 ? 'today' : `${days}d`
}

export type LineOfAuthority = 'Auto' | 'Travel' | 'Business' | 'Life'

// Two tiers, not three. 'manager' was dropped in backend migration 0066 — it
// granted exactly what 'admin' granted, so it was a permission tier that could
// only mislead. The backend's Role Literal and the broker.role check
// constraint were narrowed to match; sending 'manager' is now a 422.
export const ROLE_ITEMS = [
  { k: 'broker', t: 'Broker' },
  { k: 'admin', t: 'Admin' },
]

export const LINE_OF_AUTHORITY_ITEMS = [
  { k: 'Auto', t: 'Auto' },
  { k: 'Travel', t: 'Travel' },
  { k: 'Business', t: 'Business' },
  { k: 'Life', t: 'Life' },
]

// Canada's 13 provinces & territories — the only valid values for a broker's
// resident_province. A closed list (not free text) so "Ontari0"/"Texas" can't
// be saved. Keys are the full names, matching what the backend stores.
export const CANADA_PROVINCE_ITEMS = [
  'Alberta',
  'British Columbia',
  'Manitoba',
  'New Brunswick',
  'Newfoundland and Labrador',
  'Northwest Territories',
  'Nova Scotia',
  'Nunavut',
  'Ontario',
  'Prince Edward Island',
  'Quebec',
  'Saskatchewan',
  'Yukon',
].map((p) => ({ k: p, t: p }))
