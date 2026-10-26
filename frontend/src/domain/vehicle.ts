// The broker's own vehicle record — what is actually being quoted. Every
// field, including VIN and make/model, is typed in by hand: the broker reads
// DASH's own website during the call and enters what they see. Nothing here
// is auto-imported.

export const CONDITIONS = ['New', 'Used'] as const
export type Condition = (typeof CONDITIONS)[number]

export const OWNERSHIPS = ['Paid Off', 'Financed', 'Leased'] as const
export type Ownership = (typeof OWNERSHIPS)[number]

export const PARKING_AT_NIGHT = [
  'Secure Condo/Apt Garage',
  'Private Garage',
  'Driveway',
  'Street',
  'Parking Lot',
  'Other',
] as const
export type ParkingAtNight = (typeof PARKING_AT_NIGHT)[number]

export const COVERAGE_TYPES = [
  'Full Coverage',
  'Liability Only',
  'Liability + Comprehensive',
] as const

/**
 * A vehicle on the quote. Every field is optional: a quote is built over a
 * phone call and has to be usable half-finished.
 */
export interface QuoteVehicle {
  /** Local id — the DB assigns the real one. */
  id: string

  vin: string
  yearMakeModel: string

  condition: Condition | ''
  purchaseDate: string
  kmAtPurchase: string
  ownership: Ownership | ''
  financeCompany: string

  annualKm: string
  oneDayDistanceKm: string
  parkingAtNight: ParkingAtNight | ''

  winterTires: boolean | null
  antiTheft: boolean | null

  coverageType: string
}

/**
 * Premiums, held at policy level rather than per vehicle.
 *
 * Two sides: what the customer pays NOW (their expiring policy) and what we
 * are OFFERING. Showing them together is the whole point — the broker's case
 * is the difference between the two columns, so they have to be readable side
 * by side rather than on separate screens.
 *
 * THREE PRODUCTS: auto, homeowner and tenant. Property is two fields rather
 * than one blended figure because each is quoted separately — a
 * single "home" number would have to consult the policy type to know which
 * rate applied. Condo policies use the HOMEOWNER field; a condo is
 * owner-occupied property and a fourth field would be a distinction nothing
 * uses.
 *
 * Combined is derived on read, never stored: a third number that must always
 * equal the other two is a way for them to disagree, and nothing else can make
 * them agree again. The server derives its own totals as generated columns for
 * the same reason.
 *
 * THESE ARE ANNUAL, since 0099. The broker types the yearly premium, which is
 * what the six writable columns hold, what Meta receives and what commission
 * multiplies. Only the totals and the savings are computed by Postgres.
 *
 * This said MONTHLY until 2026-08-25, describing a schema 0099 had already
 * replaced. Three separate premium bugs came from reasoning about units, so a
 * comment claiming the wrong one is not a cosmetic problem here.
 *
 * Amounts are strings because this is a form — an empty field means "not asked
 * yet", which a number cannot represent. They become numeric on save.
 */
export interface Premiums {
  auto: string
  homeowner: string
  tenant: string
}

export const EMPTY_PREMIUMS: Premiums = { auto: '', homeowner: '', tenant: '' }

/** The typed-in rows of the premium table; Combined is computed below them. */
export const PREMIUM_ROWS = [
  { key: 'auto', label: 'Auto' },
  { key: 'homeowner', label: 'Home' },
  { key: 'tenant', label: 'Tenant' },
] as const satisfies readonly { key: keyof Premiums; label: string }[]

/**
 * Auto + Home + Tenant, formatted for display.
 *
 * Returns '' when none has been entered, so an untouched row stays blank
 * rather than claiming a premium of zero. A single figure still totals, which
 * is what a broker quoting auto-only or tenant-only needs to see.
 */
export function combinedPremium({ auto, homeowner, tenant }: Premiums): string {
  const parts = [auto, homeowner, tenant]
    .map((v) => parseFloat(v))
    .filter((n) => !Number.isNaN(n))
  if (parts.length === 0) return ''
  const sum = parts.reduce((x, y) => x + y, 0)
  // Money: two decimals unless it lands on a whole number of dollars.
  return Number.isInteger(sum) ? String(sum) : sum.toFixed(2)
}

/**
 * What the customer saves per YEAR by switching: current minus proposed.
 * Positive = we are cheaper (the pitch), negative = we cost more (the broker
 * should see that coming before the customer does). null until BOTH sides
 * have a figure — a saving against an unknown is not a number.
 *
 * ANNUAL, because that is what the broker enters and what Premiums holds.
 * SavingsChip divides by twelve for the monthly half it shows alongside — a
 * customer asks "how much a month", a broker's target is measured in annual
 * premium.
 *
 * This was documented as returning a MONTHLY figure while its only caller
 * labelled the same number /yr. The code was right; the comment was not.
 */
export function premiumSavings(current: Premiums, proposed: Premiums): number | null {
  const c = parseFloat(combinedPremium(current))
  const p = parseFloat(combinedPremium(proposed))
  if (Number.isNaN(c) || Number.isNaN(p)) return null
  return c - p
}

/** A freshly added vehicle — every field blank, ready for the broker to type
 * in from the call. */
export function blankVehicle(id: string): QuoteVehicle {
  return {
    id,
    vin: '',
    yearMakeModel: '',
    condition: '',
    purchaseDate: '',
    kmAtPurchase: '',
    ownership: '',
    financeCompany: '',
    annualKm: '',
    oneDayDistanceKm: '',
    parkingAtNight: '',
    winterTires: null,
    antiTheft: null,
    coverageType: '',
  }
}

// ── Stored row <-> form shape ────────────────────────────────────────────
//
// The API speaks snake_case with nulls and real integers; the form speaks
// camelCase with strings, because '' is the only way an input can say "not
// asked yet" and 0 is a legitimate answer that must not read as blank.
// Converting in one place keeps that difference out of every component.

/**
 * The stored shape these convert to and from. Declared structurally rather
 * than imported from data/api so the domain layer stays free of the transport
 * layer — the API's VehicleRow satisfies this.
 */
export interface VehicleRowShape {
  id: string
  vin: string | null
  year_make_model: string | null
  condition: string | null
  purchase_date: string | null
  km_at_purchase: number | null
  ownership: string | null
  finance_company: string | null
  annual_km: number | null
  one_day_distance_km: number | null
  parking_at_night: string | null
  winter_tires: boolean | null
  anti_theft: boolean | null
  coverage_type: string | null
}

/** A stored vehicle row, as the form wants it. */
export function fromRow(row: VehicleRowShape): QuoteVehicle {
  return {
    id: row.id,
    vin: row.vin ?? '',
    yearMakeModel: row.year_make_model ?? '',
    condition: (row.condition as Condition | null) ?? '',
    purchaseDate: row.purchase_date ?? '',
    // Numbers via String() rather than `|| ''` — 0 km is a real answer (a car
    // that is never driven), and `||` would blank it.
    kmAtPurchase: row.km_at_purchase === null ? '' : String(row.km_at_purchase),
    ownership: (row.ownership as Ownership | null) ?? '',
    financeCompany: row.finance_company ?? '',
    annualKm: row.annual_km === null ? '' : String(row.annual_km),
    oneDayDistanceKm:
      row.one_day_distance_km === null ? '' : String(row.one_day_distance_km),
    parkingAtNight: (row.parking_at_night as ParkingAtNight | null) ?? '',
    // Straight through: false is "no, they have none", which is a different
    // answer from null ("not asked yet").
    winterTires: row.winter_tires,
    antiTheft: row.anti_theft,
    coverageType: row.coverage_type ?? '',
  }
}

/**
 * One edited field, as the API wants it.
 *
 * Converts exactly the keys given rather than the whole vehicle: each field
 * saves on its own, and sending the lot would overwrite answers the broker
 * never touched.
 *
 * '' becomes null, not '': the columns are typed (date, integer, check-
 * constrained text) and would reject an empty string. null is how "not asked
 * yet" is stored, and how a broker clears an answer they set by mistake.
 */
export function toPatch(patch: Partial<QuoteVehicle>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  const text = (v: string) => v.trim() || null
  const num = (v: string) => {
    const n = parseInt(v, 10)
    return Number.isNaN(n) ? null : n
  }

  if ('vin' in patch) out.vin = text(patch.vin!)
  if ('yearMakeModel' in patch) out.year_make_model = text(patch.yearMakeModel!)
  if ('condition' in patch) out.condition = patch.condition || null
  if ('purchaseDate' in patch) out.purchase_date = patch.purchaseDate || null
  if ('kmAtPurchase' in patch) out.km_at_purchase = num(patch.kmAtPurchase!)
  if ('ownership' in patch) out.ownership = patch.ownership || null
  if ('financeCompany' in patch) out.finance_company = text(patch.financeCompany!)
  if ('annualKm' in patch) out.annual_km = num(patch.annualKm!)
  if ('oneDayDistanceKm' in patch) out.one_day_distance_km = num(patch.oneDayDistanceKm!)
  if ('parkingAtNight' in patch) out.parking_at_night = patch.parkingAtNight || null
  if ('winterTires' in patch) out.winter_tires = patch.winterTires
  if ('antiTheft' in patch) out.anti_theft = patch.antiTheft
  if ('coverageType' in patch) out.coverage_type = text(patch.coverageType!)

  return out
}

/**
 * Whether the auto policy stands alone or is bundled with a property policy.
 * Mirrors lead.combined_policy_type — bundling is a discount, so it belongs on
 * the quote, not just on the lead record.
 */
export const POLICY_TYPES = [
  'Auto Only',
  'Homeowner Only',
  'Tenant Only',
  'Auto and Homeowner',
  'Auto and Tenant',
  'Auto and Condo',
] as const
export type PolicyType = (typeof POLICY_TYPES)[number]

/**
 * Which premium fields a policy type actually uses.
 *
 * Drives which rows the premium table shows: a Tenant Only quote has no auto
 * premium to ask for, and offering the box invites a figure that belongs to no
 * policy. Mirrors PRODUCTS_FOR in the backend's schemas/lead.py, which
 * decides which carriers can write the business — the two lists answer
 * different questions about the same fact and must not drift.
 *
 * Condo uses the HOMEOWNER field, matching the rate it earns.
 */
export const PREMIUM_FIELDS_FOR_POLICY: Record<PolicyType, (keyof Premiums)[]> = {
  'Auto Only': ['auto'],
  'Homeowner Only': ['homeowner'],
  'Tenant Only': ['tenant'],
  'Auto and Homeowner': ['auto', 'homeowner'],
  'Auto and Condo': ['auto', 'homeowner'],
  'Auto and Tenant': ['auto', 'tenant'],
}

/**
 * Does this policy insure a car at all?
 *
 * DERIVED from the map above rather than a second list of names. They are one
 * fact — "an Auto and Tenant policy has an auto side" — and a hand-maintained
 * copy would drift the moment a policy type is added, in the silent direction:
 * a type missing from it would hide the vehicles block on a policy that needs
 * one.
 *
 * NULL IS TRUE. A broker who has not chosen a policy type yet is mid-quote,
 * and hiding the cars until they answer would block the ordinary order of
 * work — most add the vehicle while still talking about it. The backend takes
 * the same position (services/vehicle.py).
 */
export function policyHasAuto(policyType: string | null): boolean {
  if (!policyType) return true
  const fields = PREMIUM_FIELDS_FOR_POLICY[policyType as PolicyType]
  return !fields || fields.includes('auto')
}

/**
 * Form field -> API column, in ONE place.
 *
 * The form calls them auto/homeowner/tenant and splits current from proposed
 * by which object they sit in; the API has twelve flat columns whose names
 * encode the side, the product and the unit. Mapping at each call site is how
 * the two drift — and a mismatched name used to save nothing at all silently,
 * which is why this is one function and why the backend schema forbids
 * unknown fields (a wrong name is now a 422, not a quiet no-op).
 *
 * THE UNIT PICKS THE COLUMN, and this is the whole point of 0106. A figure
 * typed under Month goes to the _monthly column; under Year, to _annual. The
 * database derives the other half of the pair. Nothing here multiplies or
 * divides — before 0106 this always returned the _annual name whatever the
 * broker picked, so a monthly figure landed in a column called annual and
 * every reader downstream had to consult a flag to know what it meant. Four
 * places wrote that ×12 rule by hand and one of them forgot.
 *
 * Defaults to the annual column when no unit is set, which cannot normally
 * happen — the premium boxes are disabled until a unit is picked — but a
 * default that stores a figure somewhere real beats one that builds
 * `..._premium_` and 422s.
 */
export function premiumApiField(
  side: 'current' | 'new',
  field: keyof Premiums,
  unit: 'year' | 'month' | '',
): string {
  return `${side}_${field}_premium_${unit === 'month' ? 'monthly' : 'annual'}`
}
