import axios from 'axios'
import type { Lead } from '@/domain/types'
import { MOCK_LEADS } from './mock/leads'

// ── Data seam ──────────────────────────────────────────────────────────
// Every screen reads through these functions, never the mock modules
// directly. To point at a real backend, set VITE_API_BASE_URL and replace
// each function body's mock resolution with the commented axios call.

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api',
  headers: { 'Content-Type': 'application/json' },
})

const USE_MOCK = !import.meta.env.VITE_API_BASE_URL

// Pull a human message out of a FastAPI error response. Pydantic 422 bodies
// are { detail: [{ msg, loc, ... }] } (an array), while our own HTTPExceptions
// use a plain-string detail — this handles both, stripping pydantic's
// "Value error, " prefix so the raw rule message shows through. Passing the
// raw array to a toast would make React try to render an object and crash.
export function firstValidationMessage(err: unknown): string | null {
  if (!axios.isAxiosError(err)) return null
  const detail = err.response?.data?.detail
  if (Array.isArray(detail) && detail.length > 0 && typeof detail[0]?.msg === 'string') {
    const { msg, loc } = detail[0] as { msg: string; loc?: unknown }
    const message = msg.replace(/^Value error,\s*/, '')
    // Name the offending field. "Field required" alone doesn't say WHICH one,
    // which is the difference between a broker knowing what to fix and filing
    // a bug. loc[0] is the source ("body"/"query") and is dropped as noise.
    const field = Array.isArray(loc) ? loc.slice(1).join('.') : ''
    return field ? `${field}: ${message}` : message
  }
  if (typeof detail === 'string') return detail
  return null
}

// small helper so mock calls are async like the real ones will be
function resolve<T>(value: T): Promise<T> {
  return Promise.resolve(structuredClone(value))
}

export async function getLeads(): Promise<Lead[]> {
  if (USE_MOCK) return resolve(MOCK_LEADS)
  const { data } = await http.get<Lead[]>('/leads')
  return data
}

/**
 * The signed-in broker's own dashboard (GET /my-day).
 *
 * On adminHttp, not http: `http` reads VITE_API_BASE_URL, which is unset —
 * every call through it falls back to '/api' and 404s. adminHttp is the
 * client pointed at the real backend and carrying the session token, and is
 * what every other live screen already uses. (The `http`/USE_MOCK seam above
 * is the older mock path; this endpoint has no mock.)
 *
 * Returns only what the database can actually answer — no premium and no
 * follow-up figures. See services/broker.py::get_my_day for why.
 */
/** `month` ('2026-10') picks the month the premium and commission cards
 *  show; omitted, the current one. */
export async function getMyDay(month?: string): Promise<MyDay> {
  const { data } = await adminHttp.get<MyDay>('/my-day', { params: month ? { month } : undefined })
  return data
}

// Persist a full lead (capture form save). Mock is a no-op echo.
export async function saveLead(lead: Lead): Promise<Lead> {
  if (USE_MOCK) return resolve(lead)
  const { data } = await http.put<Lead>(`/leads/${lead.id}`, lead)
  return data
}

// ── Contact history ────────────────────────────────────────────────────
// One timeline per lead: every row is a contact a broker logged by hand.

export interface Contact {
  id: string
  lead_id: string
  broker_id: string
  outcome: string
  /** The lead's stage AT THE TIME, not now — what makes an old row readable. */
  stage_at_contact: string
  notes: string | null
  occurred_at: string
  broker: { id: string; name: string } | null
  created_at: string
  updated_at: string
}

export async function getLeadContacts(leadId: string): Promise<Contact[]> {
  const { data } = await adminHttp.get<Contact[]>(`/leads/${leadId}/contacts`)
  return data
}

/**
 * Record a contact the broker made themselves — WhatsApp, their own mobile,
 * in person.
 *
 * No stage field: the backend reads it off the lead. A client that could set
 * it could file a call against a stage the lead was never in.
 */
export async function logContact(
  leadId: string,
  entry: { outcome: string; notes?: string | null; occurred_at?: string | null },
): Promise<Contact> {
  const { data } = await adminHttp.post<Contact>(`/leads/${leadId}/contacts`, entry)
  return data
}

/** Add or edit the note on a contact. */
export async function updateContactNotes(
  contactId: string,
  notes: string | null,
): Promise<Contact> {
  const { data } = await adminHttp.patch<Contact>(`/contacts/${contactId}/notes`, { notes })
  return data
}

// ── Phone numbers, and the one way to place a call ─────────────────────

export interface LeadPhone {
  phone: string
  /** Null when the number will not normalise — an extension, an international
   *  number, a placeholder. Shown, but nothing can match a call against it. */
  phone_e164: string | null
  source: 'lead' | 'driver' | 'secondary'
  /** Only secondary numbers have a row of their own. */
  phone_id: string | null
  deletable: boolean
}

/** Every number this lead can be reached on, already deduplicated.
 *
 *  The merge happens on the SERVER. A customer's numbers live in three tables
 *  and no screen holds all three — this page has the lead's, mobile has the
 *  lead's, neither has the drivers'. Merging here would also mean two screens
 *  with two sets of rules, which would eventually disagree. */
export async function getLeadPhones(leadId: string): Promise<LeadPhone[]> {
  const { data } = await adminHttp.get<LeadPhone[]>(`/leads/${leadId}/phones`)
  return data
}

/** Forget a secondary number. */
export async function deleteLeadPhone(leadId: string, phoneId: string): Promise<void> {
  await adminHttp.delete(`/leads/${leadId}/phones/${phoneId}`)
}


// ── Lead cost ──────────────────────────────────────────────────────────
//
// What a broker owes for the leads the SYSTEM gave them. A lead entered by
// hand has no delivery row and is therefore never charged — which is why
// nothing here has a "manual" flag to check.
//
// Money arrives as STRINGS, like every other figure crossing this boundary:
// a Decimal survives the trip that way and a float does not. Render with
// money(); never do arithmetic on these without parsing first.

export interface LeadCostSettings {
  cost_per_lead: string
}

export interface BrokerCostSummary {
  broker_id: string
  name: string
  leads: number
  allocated: string
  credited: string
  charged: string
}

export interface GroupCost {
  month: string
  /** Whether the month still accepts credits. False from the 6th of the
   *  following month — see services/lead_cost.py. */
  is_open: boolean
  /** Deliveries with no price stamped: a configuration failure, counted
   *  separately so it can never be read as "these leads were free". */
  unpriced_count: number
  brokers: BrokerCostSummary[]
  total_allocated: string
  total_credited: string
  total_charged: string
}

export interface LedgerLine {
  lead_id: string
  name: string | null
  phone: string | null
  delivered_at: string
  cost: string
  unpriced: boolean
  /** Null when the lead is charged. A reason means it was written off. */
  credit_reason: string | null
}

export interface Ledger {
  month: string
  is_open: boolean
  /** YYYY-MM, already converted to Ontario time by the backend. The month list
   *  stops here — a broker is not offered months that ended before they were
   *  onboarded. Never re-parse it: a broker created 31 Jul 21:00 Ontario is
   *  1 Aug in UTC, which is the wrong join month. */
  broker_since_month: string
  broker_id: string
  unpriced_count: number
  lines: LedgerLine[]
  allocated: string
  credited: string
  charged: string
}

export type CreditReason = 'Wrong number' | 'Duplicate' | 'Test lead' | 'Other'

export async function getLeadCostSettings(): Promise<LeadCostSettings> {
  const { data } = await adminHttp.get<LeadCostSettings>('/lead-cost-settings')
  return data
}

/** Change the price. Affects FUTURE deliveries only — the cost is stamped onto
 *  each delivery as it happens, so a month already billed cannot move. */
export async function saveLeadCostSettings(
  costPerLead: string,
): Promise<LeadCostSettings> {
  const { data } = await adminHttp.put<LeadCostSettings>('/lead-cost-settings', {
    cost_per_lead: costPerLead,
  })
  return data
}

export async function getGroupCost(groupId: string, month: string): Promise<GroupCost> {
  const { data } = await adminHttp.get<GroupCost>(`/lead-cost/groups/${groupId}`, {
    params: { month },
  })
  return data
}

export async function getBrokerLedger(brokerId: string, month: string): Promise<Ledger> {
  const { data } = await adminHttp.get<Ledger>(`/lead-cost/brokers/${brokerId}`, {
    params: { month },
  })
  return data
}

/** Take a lead off the bill.
 *
 *  NO AMOUNT AND NO BROKER are sent: the backend credits exactly that
 *  delivery's own cost and works out whose month it belongs to. A client able
 *  to name either could bill the wrong person the wrong figure.
 *
 *  409 when the month is closed, and 409 when it is already credited. */
export async function addLeadCostCredit(
  leadId: string,
  reason: CreditReason,
): Promise<void> {
  await adminHttp.post('/lead-cost/credits', { lead_id: leadId, reason })
}

export async function removeLeadCostCredit(leadId: string): Promise<void> {
  await adminHttp.delete(`/lead-cost/credits/${leadId}`)
}

/** The signed-in broker's own statement. Takes no broker id — it always
 *  describes the caller. ANY month — an open one answers too, and the
 *  response's `is_open` says which kind it is. */
export async function getMyLeadCost(month: string): Promise<Ledger> {
  const { data } = await adminHttp.get<Ledger>('/my-lead-cost', { params: { month } })
  return data
}

// ── Follow-ups ─────────────────────────────────────────────────────────

export type ReminderKind = 'Callback' | 'Renewal' | 'Document' | 'Other'

export interface Reminder {
  id: string
  lead_id: string
  broker_id: string
  kind: ReminderKind
  due_at: string
  note: string
  /** Dismissed is deliberately not Done — an abandoned follow-up is not work
   *  that happened, and counting it as such flatters every report. */
  status: 'Open' | 'Done' | 'Dismissed'
  completed_at: string | null
  completed_by: string | null
  source_contact_log_id: string | null
  /** How many times this same promise has been chased and pushed. */
  attempt_count: number
  /** Which automatic rule created this, or null when a broker set it by hand
   *  (migration 0104). */
  auto_rule: 'renewal_pending' | 'renewal_lost' | null
  auto_for_renewal_date: string | null
  /** stage and renewal_date are what the Reminders page triages on — a Lost
   *  lead renewing next week is a different job from a Quote Done chasing a
   *  document. They come from different tables (lead, and the principal
   *  driver) and the backend flattens them onto here. */
  lead: {
    id: string
    name: string
    phone: string | null
    stage: string | null
    renewal_date: string | null
  } | null
  created_at: string
  updated_at: string
}

/**
 * Every open reminder this broker holds, soonest first — the Reminders page.
 *
 * NOT /my-day/reminders, which stops at end-of-today because it answers "what
 * is due now". This is the worklist: a follow-up set for next month is still
 * owed.
 */
export async function getMyReminders(): Promise<Reminder[]> {
  const { data } = await adminHttp.get<Reminder[]>('/reminders/mine')
  return data
}

// ── Premiums ────────────────────────────────────────────────────────────
// What a policy is worth, and what the customer was paying before it.
//
// THE BROKER TYPES ANNUAL, since 0099. The six *_annual product columns are
// written directly; the totals and both savings figures are GENERATED —
// Postgres computes those and this client reads them back. Recomputing any of
// them here would risk showing a broker one number while Meta receives
// another.
//
// This said MONTHLY and cited migration 0067, which 0099 reversed.

/**
 * A lead's premium row. Money arrives as STRINGS, not numbers — the backend
 * serialises Decimal that way, and parsing to a JS number would reintroduce
 * the float rounding the Decimal exists to avoid.
 */
export interface LeadPremium {
  id: string
  lead_id: string

  /** BOTH READINGS OF EVERY PRODUCT, always populated (0106). One half of
   *  each pair is what the broker typed and the other is what Postgres
   *  derived; the row does not distinguish them, because by the time it is
   *  read they are equally true. Show the one the unit names — never divide
   *  the other. */
  current_auto_premium_annual: string | null
  current_auto_premium_monthly: string | null
  new_auto_premium_annual: string | null
  new_auto_premium_monthly: string | null
  current_homeowner_premium_annual: string | null
  current_homeowner_premium_monthly: string | null
  new_homeowner_premium_annual: string | null
  new_homeowner_premium_monthly: string | null
  current_tenant_premium_annual: string | null
  current_tenant_premium_monthly: string | null
  new_tenant_premium_annual: string | null
  new_tenant_premium_monthly: string | null

  /** WHICH BOX THE BROKER TYPED IN, so the form reopens on the right one.
   *
   *  Its job shrank at 0106: it no longer tells a reader how to interpret a
   *  stored figure, because both readings are stored and both are correct. It
   *  says which column the next save writes to, and which way the database
   *  derives the other.
   *
   *  NEVER RESET. An earlier version reset both to 'year' at Quote Done, when
   *  the backend converted the figures itself. */
  current_premium_unit: 'year' | 'month' | null
  new_premium_unit: 'year' | 'month' | null

  /** COMPUTED BY POSTGRES, in both readings. Plain sums since 0106 — the
   *  annual columns are genuinely annual, so no unit multiplier is applied
   *  here or anywhere else. Nothing is ever divided on the client. */
  total_current_premium_annual: string | null
  total_current_premium_monthly: string | null
  total_new_premium_annual: string | null
  total_new_premium_monthly: string | null
  savings_annual: string | null
  savings_monthly: string | null

  policy_number: string | null
  issued_at: string | null
  effective_date: string | null
}

/** The six ANNUAL figures and the quote facts — everything writable.
 *
 *  The backend refuses any other key outright (extra="forbid"), so a stale
 *  browser tab sending the old *_monthly names gets a 422 rather than a 200
 *  that saves nothing. */
export type SavePremiumInput = Partial<{
  // ONLY THE TYPED HALF IS EVER SENT. premiumApiField picks the column from
  // the broker's unit, and the database derives the other one — sending both
  // is accepted but pointless, since the derived half is overwritten.
  current_auto_premium_annual: string | null
  current_auto_premium_monthly: string | null
  new_auto_premium_annual: string | null
  new_auto_premium_monthly: string | null
  current_homeowner_premium_annual: string | null
  current_homeowner_premium_monthly: string | null
  new_homeowner_premium_annual: string | null
  new_homeowner_premium_monthly: string | null
  current_tenant_premium_annual: string | null
  current_tenant_premium_monthly: string | null
  new_tenant_premium_annual: string | null
  new_tenant_premium_monthly: string | null
  current_premium_unit: 'year' | 'month'
  new_premium_unit: 'year' | 'month'
  policy_number: string | null
  effective_date: string | null
}>

/** Null when the broker has not entered any premium yet — an ordinary state,
 *  not an error, which is why the endpoint does not 404. */
export async function getLeadPremium(leadId: string): Promise<LeadPremium | null> {
  const { data } = await adminHttp.get<LeadPremium | null>(`/leads/${leadId}/premium`)
  return data
}

/**
 * Save what has been typed so far. Upsert: one row per lead, and a broker
 * filling these in across several calls saves repeatedly.
 *
 * SEND ONLY WHAT CHANGED. The backend patches with exclude_unset, so an
 * omitted field is left alone — sending the whole object every time would
 * overwrite a figure a colleague entered between two of these calls.
 */
export async function saveLeadPremium(
  leadId: string,
  input: SavePremiumInput,
): Promise<LeadPremium> {
  const { data } = await adminHttp.put<LeadPremium>(`/leads/${leadId}/premium`, input)
  return data
}

export async function getLeadReminders(leadId: string): Promise<Reminder[]> {
  const { data } = await adminHttp.get<Reminder[]>(`/leads/${leadId}/reminders`)
  return data
}

export async function createReminder(
  leadId: string,
  input: {
    due_at: string
    note: string
    kind?: ReminderKind
    source_contact_log_id?: string | null
  },
): Promise<Reminder> {
  const { data } = await adminHttp.post<Reminder>(`/leads/${leadId}/reminders`, input)
  return data
}

/** Move one of a lead's call counters by one.
 *
 * A DELTA, NOT A TOTAL: the server does the arithmetic (and the zero clamp) in
 * SQL, so two quick presses both count instead of the second overwriting the
 * first. Returns BOTH counters, which is what the caller should store — never
 * its own guess.
 *
 * 403 if the broker no longer holds this lead.
 */
export async function adjustCallCount(
  leadId: string,
  outcome: 'answered' | 'no_answer',
  delta: 1 | -1,
): Promise<{ answered_count: number; no_answer_count: number }> {
  const { data } = await adminHttp.patch<{ answered_count: number; no_answer_count: number }>(
    `/leads/${leadId}/call-count`,
    { outcome, delta },
  )
  return data
}

export async function completeReminder(reminderId: string): Promise<Reminder> {
  const { data } = await adminHttp.patch<Reminder>(`/reminders/${reminderId}/complete`)
  return data
}

export async function dismissReminder(reminderId: string): Promise<Reminder> {
  const { data } = await adminHttp.patch<Reminder>(`/reminders/${reminderId}/dismiss`)
  return data
}

/** Snoozing MOVES the due date — there is no snoozed status, or the row would
 *  keep counting as overdue against a date already passed. */
export async function snoozeReminder(reminderId: string, dueAt: string): Promise<Reminder> {
  const { data } = await adminHttp.patch<Reminder>(`/reminders/${reminderId}/snooze`, {
    due_at: dueAt,
  })
  return data
}

/** The Add-to-calendar link. Served as a file, so it is opened, not fetched. */
export function reminderCalendarUrl(reminderId: string): string {
  return `${adminHttp.defaults.baseURL}/reminders/${reminderId}/calendar.ics`
}

/** Save this lead to the phone's own address book.
 *
 *  WHY A BLOB AND NOT AN <a href>, which is what reminderCalendarUrl above
 *  hands back: the route is behind get_current_broker, and a browser
 *  navigating to a URL sends no Authorization header. Fetching it through
 *  adminHttp keeps the bearer token on the request; the object URL below is
 *  what turns the response into a file the OS opens.
 *
 *  The card itself is built server-side (backend/vcard.py) rather than here.
 *  vCard 3.0 has four rules that fail silently — escaping order, CRLF, folding
 *  by octet without splitting UTF-8, and N's five components — and that module
 *  is the one place they are implemented and tested.
 *
 *  Throws on failure so the caller can toast; a 409 means the lead has no
 *  phone number, which firstValidationMessage surfaces verbatim.
 */
export async function saveLeadContact(leadId: string): Promise<void> {
  const response = await adminHttp.get(`/leads/${leadId}/contact.vcf`, {
    responseType: 'blob',
  })

  // The filename the backend chose, so the saved file is named after the
  // customer rather than "contact.vcf" for everyone. Parsed leniently: a
  // missing or unexpected header is not a reason to fail a download that
  // otherwise worked.
  const disposition = String(response.headers['content-disposition'] ?? '')
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? 'contact.vcf'

  // text/vcard, not whatever the blob defaulted to: iOS decides whether to
  // offer the Add Contact sheet from the type, and an octet-stream is saved
  // as a file instead.
  const url = URL.createObjectURL(
    new Blob([response.data], { type: 'text/vcard;charset=utf-8' }),
  )
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
  } finally {
    // REVOKED, or the blob is held for the life of the document. Deferred by a
    // tick because Safari reads the href after the click returns, and revoking
    // synchronously cancels the download it just started.
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
}

// ── Admin data seam ────────────────────────────────────────────────────
// Admin screens (community, broker_group, ...) have no mock — they always
// talk to the real FastAPI backend directly, since this is genuinely new
// data with nothing to fake against. Separate client so the rest of the
// app's mock/real toggle (VITE_API_BASE_URL) is untouched.
export const adminHttp = axios.create({
  baseURL: import.meta.env.VITE_ADMIN_API_BASE_URL ?? 'http://127.0.0.1:8000',
  headers: { 'Content-Type': 'application/json' },
})

// ── Auth ───────────────────────────────────────────────────────────────
// Two-step login (password, then TOTP) against the real backend. login,
// verifyLogin, and refreshAccessToken never carry a Bearer token themselves
// (authInterceptor.ts skips them) — /auth/login and /auth/login/verify
// happen BEFORE a session exists, and /auth/refresh trades a refresh_token
// for a new access_token by design, not by being "authenticated" in the
// normal sense. changePassword is deliberately NOT excluded from the
// interceptor — it needs whatever Bearer token (if any) is currently
// stored, so the SAME call works for both the forced first-login change
// (no session yet, identified by pending_token in the body instead) and a
// voluntary later change (identified by the real access token header).

export interface LoginResponse {
  status: 'password_change_required' | 'enrollment_required' | 'totp_required'
  pending_token: string
  totp_uri: string | null
}

export interface SessionResponse {
  access_token: string
  refresh_token: string
  token_type: 'bearer'
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  const { data } = await adminHttp.post<LoginResponse>('/auth/login', { email, password })
  return data
}

export async function verifyLogin(pendingToken: string, code: string): Promise<SessionResponse> {
  const { data } = await adminHttp.post<SessionResponse>('/auth/login/verify', {
    pending_token: pendingToken,
    code,
  })
  return data
}

export async function refreshAccessToken(refreshToken: string): Promise<{ access_token: string }> {
  const { data } = await adminHttp.post<{ access_token: string }>('/auth/refresh', {
    refresh_token: refreshToken,
  })
  return data
}

// pendingToken: pass it for the forced first-login change (no session
// exists yet). Omit it for a voluntary change by an already-logged-in
// broker — the interceptor attaches their real access token instead.
export async function changePassword(
  currentPassword: string,
  newPassword: string,
  pendingToken?: string,
): Promise<void> {
  await adminHttp.post('/auth/change-password', {
    pending_token: pendingToken ?? null,
    current_password: currentPassword,
    new_password: newPassword,
  })
}

export interface Country {
  id: string
  name: string
  created_at: string
}

export interface Community {
  id: string
  name: string
  created_at: string
  updated_at: string
  community_country?: { country: Country }[]
}

export interface BrokerGroup {
  id: string
  community_id: string
  group_number: number
  name: string
  created_at: string
  updated_at: string
  community?: { name: string }
  broker_count: number
  has_form_mapped: boolean
  // The Meta-confirmed name/id of the FIRST form routed to this group, with
  // form_count for how many there are in total — a group may have several
  // (a split test, seasonal variants). null only for legacy groups created
  // before "no form, no group".
  form_name: string | null
  form_platform_id: string | null
  form_count: number
  is_recycle_bin: boolean
}

export async function getCountries(): Promise<Country[]> {
  const { data } = await adminHttp.get<Country[]>('/countries')
  return data
}

export async function getCommunities(): Promise<Community[]> {
  const { data } = await adminHttp.get<Community[]>('/communities')
  return data
}

export async function createCommunity(name: string, countryIds: string[]): Promise<Community[]> {
  const { data } = await adminHttp.post<Community[]>('/communities', {
    name,
    country_ids: countryIds,
  })
  return data
}

/** Rename a community. 409 if the new name is already taken. */
export async function renameCommunity(communityId: string, name: string): Promise<Community[]> {
  const { data } = await adminHttp.patch<Community[]>(`/communities/${communityId}`, { name })
  return data
}

/** Delete a community. 409 if any broker group still belongs to it. */
export async function deleteCommunity(communityId: string): Promise<void> {
  await adminHttp.delete(`/communities/${communityId}`)
}

export async function setCommunityCountries(communityId: string, countryIds: string[]): Promise<void> {
  await adminHttp.put(`/communities/${communityId}/countries`, { country_ids: countryIds })
}

export async function getBrokerGroups(): Promise<BrokerGroup[]> {
  const { data } = await adminHttp.get<BrokerGroup[]>('/broker-groups')
  return data
}

// A broker group only exists once it can receive leads — the backend
// validates platform_form_id with Meta and creates the group + its lead_form
// mapping together, or neither at all. No separate "map a form later" step.
// The form's display name comes from Meta (fetched during validation), not the
// admin, so only the form id is sent.
export async function createBrokerGroup(
  communityId: string,
  name: string,
  platformFormId: string,
): Promise<BrokerGroup[]> {
  const { data } = await adminHttp.post<BrokerGroup[]>('/broker-groups', {
    community_id: communityId,
    name,
    platform_form_id: platformFormId,
  })
  return data
}

export async function deleteBrokerGroup(groupId: string): Promise<void> {
  await adminHttp.delete(`/broker-groups/${groupId}`)
}

export async function renameBrokerGroup(groupId: string, name: string): Promise<BrokerGroup> {
  const { data } = await adminHttp.patch<BrokerGroup[]>(`/broker-groups/${groupId}`, { name })
  return data[0]
}

// Note: the API never returns password_hash — it's excluded server-side.
export interface Broker {
  id: string
  group_id: string
  name: string
  email: string
  phone: string
  // Two tiers — 'manager' dropped in backend migration 0066.
  role: 'broker' | 'admin'
  date_of_birth: string | null
  address: string | null
  ribo_license_number: string | null
  lines_of_authority: string[]
  resident_province: string
  license_expiry_date: string | null
  eo_start_date: string | null
  eo_expiry_date: string | null
  is_active: boolean
  // daily_cap is system-managed once round-robin allocation is live: only
  // the daily reset job writes it. Editing an existing broker's cap goes
  // through setBrokerDailyCap (below), which schedules a change for the
  // NEXT reset via pending_daily_cap — never a same-day write. See
  // backend/docs/round-robin-allocation-design.md §6.
  daily_cap: number
  // Group-2 overtime ceiling — same system-managed/deferred pattern as
  // daily_cap, via setBrokerMaxCap.
  max_cap: number
  // Today's Group-2 opt-in ("ready for overtime leads today"). Instant,
  // cleared automatically at the next reset — see setBrokerReadyForMore.
  ready_for_more: boolean
  // The broker has stopped their own leads (they're away, ill, overloaded).
  // Separate from is_blocked_by_admin below: this one is theirs to set and
  // clear, and it says nothing about conduct.
  //
  // PERSISTS. It is not cleared overnight — a broker on vacation stays off
  // until they, or an admin via resumeBrokerLeads, switch them back on.
  self_paused: boolean
  // When the pause started, for "Paused · 6d". Null whenever self_paused is
  // false — and also for anyone who was already paused when the column was
  // added, since that migration would not invent a date it did not have.
  // Render no duration rather than a guessed one.
  self_paused_at: string | null
  // Non-null while a cap change is scheduled but hasn't taken effect yet.
  pending_daily_cap: number | null
  pending_daily_cap_effective_at: string | null
  pending_max_cap: number | null
  pending_max_cap_effective_at: string | null
  availability_status: string
  is_blocked_by_admin: boolean
  blocked_reason: string | null
  blocked_at: string | null
  // Non-null while a pause is scheduled to lift, but hasn't yet — the
  // broker stays blocked (is_blocked_by_admin stays true) until then. See
  // scheduleBrokerUnpause.
  scheduled_unpause_at: string | null
  // Non-null while the broker hasn't started receiving leads yet (set at
  // onboarding to the next Ontario day; also used for any future deferred
  // activation).
  eligible_from: string | null
  is_archived: boolean
  archived_at: string | null
  created_at: string
  updated_at: string
}

// No password — the system generates a one-time temp password on the
// backend and returns it in CreateBrokerResult.temporary_password.
export interface CreateBrokerInput {
  name: string
  email: string
  phone: string
  role: string
  date_of_birth?: string | null
  address?: string | null
  ribo_license_number?: string | null
  lines_of_authority?: string[]
  resident_province?: string
  license_expiry_date?: string | null
  eo_start_date?: string | null
  eo_expiry_date?: string | null
  // Safe to set directly at onboarding (unlike an existing broker's cap,
  // which must go through the deferred endpoints) — a new broker isn't
  // eligible for leads until tomorrow anyway.
  daily_cap?: number
  max_cap?: number
}

// NO PASSWORD. Onboarding creates the broker; they cannot log in until an
// admin calls generatePassword below — the same action used later if they
// ever forget it, so there is one path rather than two doing the same work.
export interface CreateBrokerResult {
  broker: Broker
}

/**
 * Issue a temporary password for a broker. Returned ONCE — it is not stored
 * in readable form anywhere, so an admin who loses it presses this again.
 *
 * Also clears the broker's 2FA (covering a lost phone) and their
 * failed-attempt lockout, so a broker who locked themselves out can use the
 * new password immediately rather than waiting out fifteen minutes.
 */
export async function generatePassword(brokerId: string): Promise<string> {
  const { data } = await adminHttp.post<{ temporary_password: string }>(
    `/brokers/${brokerId}/generate-password`,
  )
  return data.temporary_password
}

export async function getBrokers(groupId: string): Promise<Broker[]> {
  const { data } = await adminHttp.get<Broker[]>(`/broker-groups/${groupId}/brokers`)
  return data
}

// ─── Broker documents ────────────────────────────────────────────────────
// The proof behind the compliance fields an admin types at onboarding. All
// three are optional, and each broker has at most one of each kind — a
// re-upload REPLACES, it does not add a version.

export type DocumentKind = 'photo' | 'ribo_license' | 'eo_certificate'

export interface BrokerDocument {
  id: string
  kind: DocumentKind
  file_name: string
  content_type: string
  size_bytes: number
  uploaded_at: string
}

export async function listBrokerDocuments(brokerId: string): Promise<BrokerDocument[]> {
  const { data } = await adminHttp.get<BrokerDocument[]>(`/brokers/${brokerId}/documents`)
  return data
}

/**
 * Attach a document, replacing any the broker already had of that kind.
 *
 * Content-Type: undefined IS THE FIX, not an oversight. adminHttp sets a
 * default of application/json for every request, and axios does NOT override
 * an explicitly-configured default for a FormData body — it sends the JSON
 * content type with no multipart boundary, so the server parses nothing and
 * reports `file: Field required` on a request that plainly carried a file.
 * Setting it to undefined removes the default for this one call and lets axios
 * derive the real header, boundary included.
 *
 * This is the first multipart request in the codebase, so nothing had exercised
 * that default before. authInterceptor.ts handles the same hazard on a 401
 * retry, by deleting the header so it is re-derived.
 *
 * Throws 422 on the wrong file type for the kind, 413 when over 10MB.
 */
export async function uploadBrokerDocument(
  brokerId: string,
  kind: DocumentKind,
  file: File,
): Promise<BrokerDocument> {
  const body = new FormData()
  body.append('file', file)
  const { data } = await adminHttp.post<BrokerDocument>(
    `/brokers/${brokerId}/documents/${kind}`,
    body,
    { headers: { 'Content-Type': undefined } },
  )
  return data
}

/**
 * A short-lived link to view or download a document.
 *
 * Signed per request rather than stored — the bucket blocks public access, so
 * there is no durable URL to keep. Throws 404 when the broker has nothing of
 * that kind, which is ordinary: every document is optional.
 */
export async function brokerDocumentUrl(
  brokerId: string,
  kind: DocumentKind,
): Promise<string> {
  const { data } = await adminHttp.get<{ url: string }>(
    `/brokers/${brokerId}/documents/${kind}/url`,
  )
  return data.url
}

/** Remove a document — deletes the stored file, not just the record. */
export async function deleteBrokerDocument(
  brokerId: string,
  kind: DocumentKind,
): Promise<void> {
  await adminHttp.delete(`/brokers/${brokerId}/documents/${kind}`)
}

export async function createBroker(
  groupId: string,
  input: CreateBrokerInput,
): Promise<CreateBrokerResult> {
  const { data } = await adminHttp.post<CreateBrokerResult>(
    `/broker-groups/${groupId}/brokers`,
    input,
  )
  return data
}

export async function moveBroker(brokerId: string, groupId: string): Promise<Broker> {
  const { data } = await adminHttp.patch<Broker>(`/brokers/${brokerId}/move`, { group_id: groupId })
  return data
}

export async function blockBroker(brokerId: string, reason: string): Promise<Broker> {
  const { data } = await adminHttp.patch<Broker>(`/brokers/${brokerId}/block`, { reason })
  return data
}

// Pausing (blockBroker) is instant; unpausing is NOT — it schedules the
// unblock for a future date (effectiveAt), applied by the next daily reset.
// The broker stays blocked (is_blocked_by_admin stays true) until then, so
// leads resume the day AFTER, same regime as a brand-new broker's first day.
// See backend/docs/round-robin-allocation-design.md §5.
export async function scheduleBrokerUnpause(brokerId: string, effectiveAt: string): Promise<Broker> {
  const { data } = await adminHttp.patch<Broker>(`/brokers/${brokerId}/schedule-unpause`, {
    effective_at: effectiveAt,
  })
  return data
}

// Schedules a daily_cap change for the NEXT reset — never takes effect the
// same day. daily_cap itself is system-managed from here on; only the reset
// job writes it. See §6.
export async function setBrokerDailyCap(brokerId: string, dailyCap: number, effectiveAt: string): Promise<Broker> {
  const { data } = await adminHttp.patch<Broker>(`/brokers/${brokerId}/daily-cap`, {
    daily_cap: dailyCap,
    effective_at: effectiveAt,
  })
  return data
}

// Same deferred pattern as setBrokerDailyCap, for the Group-2 overtime ceiling.
export async function setBrokerMaxCap(brokerId: string, maxCap: number, effectiveAt: string): Promise<Broker> {
  const { data } = await adminHttp.patch<Broker>(`/brokers/${brokerId}/max-cap`, {
    max_cap: maxCap,
    effective_at: effectiveAt,
  })
  return data
}

// Today's Group-2 opt-in — takes effect immediately, cleared automatically
// by tomorrow's reset (the broker/admin re-opts-in each day they want
// overtime leads).
export async function setBrokerReadyForMore(brokerId: string, readyForMore: boolean): Promise<Broker> {
  const { data } = await adminHttp.patch<Broker>(`/brokers/${brokerId}/ready-for-more`, {
    ready_for_more: readyForMore,
  })
  return data
}

/**
 * The signed-in broker opting THEMSELVES in for more leads today.
 *
 * Distinct from setBrokerReadyForMore above, which is the ADMIN doing it to
 * someone: that one names a broker in the URL, and naming a broker is exactly
 * the capability a broker must not have. This takes no id — the backend reads
 * it from the token.
 *
 * Switching it on hands over the leads ALREADY waiting in their group, not
 * just the next to arrive, so the list can grow the moment it is called.
 */
export async function setMyReadyForMore(readyForMore: boolean): Promise<MyProfile> {
  const { data } = await adminHttp.patch<MyProfile>('/my-ready-for-more', {
    ready_for_more: readyForMore,
  })
  return data
}

/** What PATCH /my-availability answers with. */
export interface AvailabilityResult {
  /** The state now in force. */
  paused: boolean
  /**
   * Whether the leads already waiting in the group were handed out.
   *
   * ONLY MEANINGFUL WHEN RESUMING, and false is NOT an error — the pause
   * change itself succeeded either way, which is why this arrives in a 200
   * rather than as a rejected request. False means the broker is back on but
   * the backlog is still sitting there and needs an admin.
   */
  backlog_drained: boolean
}

/**
 * The signed-in broker stopping their own leads, or starting them again.
 *
 * BOTH DIRECTIONS TAKE EFFECT AT ONCE. The admin equivalent (blockBroker)
 * needs someone else to act and only reverses the next morning, which is no
 * use to a broker who is ill today.
 *
 * Takes no broker id, exactly like setMyReadyForMore above — the backend reads
 * it from the token, so there is no way to pause a colleague.
 *
 * Resuming also offers the leads already waiting, so the list can grow a
 * moment after the call. Check backlog_drained: when it is false the broker is
 * receiving new leads but the waiting ones could not be handed out.
 */
export async function setMyAvailability(paused: boolean): Promise<AvailabilityResult> {
  const { data } = await adminHttp.patch<AvailabilityResult>('/my-availability', {
    paused,
  })
  return data
}

/**
 * An admin switching a broker's own pause back off, on their behalf.
 *
 * RESUME ONLY — there is deliberately no admin call that SETS self_paused. An
 * admin stopping a broker uses blockBroker, which records a reason and is a
 * different act; keeping them apart is what lets "why did this broker get
 * nothing" be answered.
 *
 * Exists because self_paused persists until somebody clears it, and a broker
 * who is away and unreachable cannot clear their own.
 *
 * Returns the same shape as setMyAvailability, backlog_drained included — the
 * waiting leads can fail to hand out here exactly as they can there, and the
 * admin is the person who would have to fix it. Throws 400 if the broker
 * hasn't stopped their own leads.
 */
export async function resumeBrokerLeads(brokerId: string): Promise<AvailabilityResult> {
  const { data } = await adminHttp.patch<AvailabilityResult>(`/brokers/${brokerId}/resume-leads`)
  return data
}

export async function archiveBroker(brokerId: string): Promise<Broker> {
  const { data } = await adminHttp.patch<Broker>(`/brokers/${brokerId}/archive`)
  return data
}

// daily_cap/max_cap are deliberately absent — editing an existing broker's
// cap goes through setBrokerDailyCap/setBrokerMaxCap (deferred to the next
// reset), never through this profile-edit shape. See §6.
export interface UpdateBrokerInput {
  name: string
  email: string
  phone: string
  role: string
  date_of_birth?: string | null
  address?: string | null
  ribo_license_number?: string | null
  lines_of_authority?: string[]
  resident_province?: string
  license_expiry_date?: string | null
  eo_start_date?: string | null
  eo_expiry_date?: string | null
}

export async function updateBroker(brokerId: string, input: UpdateBrokerInput): Promise<Broker> {
  const { data } = await adminHttp.patch<Broker>(`/brokers/${brokerId}`, input)
  return data
}

// The REAL lead row as the admin allocation screens see it, straight from
// the `lead` table plus two joined names. This is deliberately a DIFFERENT
// type from the broker-facing `Lead` (domain/types.ts) — that one is the
// pipeline/working shape (drivers, vehicles, licence…); this one is the
// allocation shape (who did the system route it to). See
// GET /leads (repositories/lead.py::list_all) for the exact join.
export interface AdminLead {
  id: string
  group_id: string
  assigned_broker_id: string | null
  source: string // 'meta' | 'website' | 'tiktok'
  name: string
  phone: string
  email: string
  stage: string
  combined_policy_type: string | null
  extra_data: Record<string, unknown> | null
  // When the CUSTOMER requested it (Meta created_time); null on manual/test.
  requested_at: string | null
  // When the current broker got it; who assigned ('system' | 'admin'); null
  // while unassigned.
  assigned_at: string | null
  assigned_by: string | null
  created_at: string
  // Joined names (null-safe): broker is null for an unassigned lead.
  broker_group: {
    id: string
    name: string | null
    group_number: number
    community: { name: string } | null
  } | null
  broker: { id: string; name: string } | null
}

// ── Admin Leads list — filters, pagination, and KPI counts in ONE payload ──
// The screen makes a single request per view; the response carries the page of
// rows, the filtered total (for the pager), AND the four KPI counts. This keeps
// it to one network round-trip regardless of how many facets are shown — the
// biggest perceived-speed win (see the endpoint design notes). Every filter is
// server-side: the browser never holds more than one page, so it stays fast at
// any lead volume.
export interface AdminLeadFilters {
  search?: string
  community?: string // community name ('Dragon' | 'Tiger' | ...) — filters via group→community
  groupId?: string
  brokerId?: string
  stage?: string // a backend lead.stage value (see ADMIN_LEAD_STAGES)
  unassigned?: boolean // true = only leads sitting in the pool (assigned_broker_id is null)
  page?: number // 1-based
  limit?: number // page size (default 50)
}

// The four KPI chips. `all` is the count IGNORING the assigned/unassigned facet
// (so switching between them doesn't make the totals jump); it still respects
// the other filters (community/group/broker/stage/search) so the KPIs describe
// the same slice the table shows.
export interface AdminLeadCounts {
  all: number
  unassigned: number
  assigned: number
  today: number
}

export interface AdminLeadPage {
  rows: AdminLead[]
  total: number // rows matching ALL active filters (drives the pager)
  counts: AdminLeadCounts
}

// The 7 real backend stages (lead.stage CHECK constraint, migrations/0051).
// NOTE: deliberately NOT domain/stages.ts — that is the BROKER pipeline
// taxonomy (new/called/quoted/…), a different vocabulary. The admin leads view
// shows raw backend leads, so it must use the backend's own stage values.
// DELIBERATELY ITS OWN LIST, not derived from LEAD_STAGES — but it must still
// carry every real stage, or the admin filter silently cannot find them. In
// Progress was added with 0115 for exactly that reason.
export const ADMIN_LEAD_STAGES = [
  'New', 'In Progress', 'Quote Done', 'Pending',
  'Sold', 'Submitted', 'Issued', 'Lost',
] as const

// Admin view of every lead + who the system routed it to. One combined payload
// (rows + total + KPI counts) so the whole screen is a single round-trip. Like
// every admin call, this always talks to the real backend (no mock seam) — see
// adminHttp above.
export async function getAdminLeads(filters: AdminLeadFilters = {}): Promise<AdminLeadPage> {
  const params = {
    search: filters.search || undefined,
    community: filters.community || undefined,
    group_id: filters.groupId || undefined,
    broker_id: filters.brokerId || undefined,
    stage: filters.stage || undefined,
    unassigned: filters.unassigned ? 1 : undefined,
    page: filters.page ?? 1,
    limit: filters.limit ?? 50,
  }
  const { data } = await adminHttp.get<AdminLeadPage>('/leads', { params })
  return data
}

// Live allocation capacity for one group — GET /broker-groups/{id}/capacity
// (migrations/0021 group_capacity). The per-broker `delivered_today` is the
// ONLY count not already on the broker row; everything else about a broker
// (cap, pause, overtime, pending changes) comes from getBrokers(), so callers
// merge the two by broker id rather than duplicating a source of truth.
export interface GroupCapacity {
  group_id: string
  last_reset_at: string
  leads_today: number
  unassigned: number
  brokers: { id: string; name: string; delivered_today: number }[]
}

export async function getGroupCapacity(groupId: string): Promise<GroupCapacity> {
  const { data } = await adminHttp.get<GroupCapacity>(`/broker-groups/${groupId}/capacity`)
  return data
}

// Admin-only move of an ALREADY-assigned (or unassigned) lead to a chosen
// broker — e.g. the original broker is off sick, or a stuck lead is placed by
// hand. NOT a broker "claim" (there is no such thing under round-robin
// allocation — see §9). Deliberately invisible to both brokers' daily caps
// (§3). Returns the updated real lead row.
export async function reassignLead(leadId: string, brokerId: string): Promise<AdminLead> {
  const { data } = await adminHttp.patch<AdminLead>(`/leads/${leadId}/reassign`, { broker_id: brokerId })
  return data
}

// ── The BROKER's own leads (GET /leads/mine) ──────────────────────────────
// The real data behind the broker Leads screen. Matches the trimmed backend
// MY_LEAD_SELECT (no broker/group embeds — always the broker themselves — but
// WITH extra_data for the detail panel's Meta answers). Real backend only
// (adminHttp), like every authenticated call.
export interface MyLead {
  id: string
  group_id: string
  source: string // 'meta' | 'website' | 'tiktok'
  name: string
  phone: string
  /** The same number in E.164, maintained by a trigger (migration 0058). Null
   *  when it will not normalise — a placeholder, an extension, an
   *  international number. wa.me needs this form, not stripped digits. */
  phone_e164: string | null
  email: string
  stage: string // one of the 8 real backend stages (see domain/leadStages)
  /** A label INSIDE the stage (migration 0092) — 'In Progress' on a New lead
   *  someone has started, 'Junk' on a Lost one that was never a real
   *  opportunity. Null is the ordinary case and means the plain stage; the
   *  server clears it on every stage move. */
  sub_stage: string | null
  combined_policy_type: string | null
  /** The Meta form's Ontario auto qualifier (migration 0108), stored in the
   *  form's OWN vocabulary — '2020_or_before' / '2021_or_later', underscores
   *  and all — so the column can never disagree with extra_data about what the
   *  customer answered. Null on website and phone leads, never asked. */
  licensed_in_canada: string | null
  requested_at: string | null
  assigned_at: string | null
  created_at: string
  // The broker's judgement, and the reason the pipeline can stall: a META
  // lead sitting on null cannot move to Quote Done or beyond until it is
  // answered. THREE STATES, and null is not false —
  //   true  — a real prospect; Meta hears about it
  //   false — not a prospect; Meta hears nothing, ever
  //   null  — NOT ASKED YET (also what dismissing the dialog leaves behind)
  // Always null on a non-Meta lead: the question is never put to the broker
  // because there is no conversion to attach the answer to.
  is_qualified: boolean | null
  /** Calls where the customer picked up (migration 0121). A scanning aid for
   *  "who have I not rung yet?", NOT call history — contact_log is that.
   *  Both counters start at 0 for every lead, so 0 means "nothing recorded
   *  with these counters", not "nobody ever rang this person". */
  answered_count: number
  /** Calls where the customer did not pick up. See answered_count. */
  no_answer_count: number
  // The raw Meta question/answers for this lead (flattened key->value).
  extra_data: Record<string, unknown> | null
  /** The applicant's own details, embedded so the list can tell a lead that is
   *  READY to be judged from one that is merely old (domain/unjudged.ts).
   *
   *  AN ARRAY BECAUSE A LEAD CAN CARRY SEVERAL DRIVERS — a spouse, an
   *  additional operator off the DASH report. The consumer picks the principal
   *  itself; PostgREST cannot narrow an embed to one row without dropping the
   *  leads that have no driver at all, which are exactly the ones a readiness
   *  check must see as not-ready.
   *
   *  Optional because only the broker's own list embeds it — the admin lead
   *  list uses a leaner select. */
  driver?: {
    is_principal: boolean
    first_name: string | null
    last_name: string | null
    phone: string | null
    email: string | null
    date_of_birth: string | null
    gender: string | null
    street_address: string | null
    city: string | null
    postal_code: string | null
  }[]
}

// Per-stage counts for the left rail — keyed by the real stage names, plus
// 'all'. Every one of the 8 stages is always present (0 when none), so the
// rail renders a stable list.
/**
 * GET /my-day. `counts` is keyed by the real backend stage names plus 'all' —
 * the same shape the Leads rail consumes, so both screens read one source.
 *
 * No follow-up field yet: /my-day/reminders returns those counts but nothing
 * calls it, so My Day still shows follow-ups as an inert "not set up" note.
 */
export interface MyDay {
  broker: { name: string }
  counts: MyLeadCounts
  attention: {
    new_today: number
    stale: number
  }
  /**
   * Annual premium the CARRIER ISSUED in the chosen month, how many policies
   * produced it, and the broker's commission on them. Counted by issued_at —
   * the date the broker marked the policy Issued.
   *
   * Money crosses this boundary as strings and is parsed where it is
   * rendered, so nothing has to survive a float.
   */
  premium: {
    /** The month these figures describe, '2026-10'. */
    month: string
    /** Written in `month` (the name predates the month picker). */
    written_this_month: string
    policy_count: number
    /** The broker's share of the commission on those policies. Null when the
     *  commission settings could not be read — shown as "—", never as $0. */
    commission_earned: string | null
  }
}

export type MyLeadCounts = Record<string, number>

export interface MyLeadPage {
  rows: MyLead[]
  total: number // rows matching the active stage/search (drives paging)
  counts: MyLeadCounts // per-stage across the whole book (drives the rail)
}

export interface MyLeadFilters {
  stage?: string
  search?: string
  page?: number
  limit?: number
}

/**
 * Create a lead the broker took themselves — a referral, a walk-in, a call to
 * the office.
 *
 * No group or broker is sent: both are the caller's own, read from their token
 * server-side. A broker cannot file a customer into someone else's book because
 * there is no field for it.
 *
 * Assigned straight to the creator without touching the round-robin, so it
 * never counts toward anyone's daily cap.
 */
export async function createManualLead(lead: {
  name: string
  phone?: string
  email?: string
}): Promise<MyLead> {
  const { data } = await adminHttp.post<MyLead>('/leads/manual', lead)
  return data
}

/**
 * One of the broker's own leads, by id.
 *
 * FOR DEEP LINKS ONLY. getMyLeads is paginated, so a link naming a lead on a
 * later page has nothing to fetch it with — which is what the renewal
 * WhatsApp's "Set Up Reminder" button needs. Same row shape, so the caller can
 * select it exactly as if it had come from the list.
 *
 * 404s for a lead that is not this broker's, identically to one that does not
 * exist.
 */
export async function getMyLead(leadId: string): Promise<MyLead> {
  const { data } = await adminHttp.get<MyLead>(`/leads/${leadId}`)
  return data
}

export async function getMyLeads(filters: MyLeadFilters = {}): Promise<MyLeadPage> {
  const params = {
    stage: filters.stage || undefined,
    search: filters.search || undefined,
    page: filters.page ?? 1,
    limit: filters.limit ?? 50,
  }
  const { data } = await adminHttp.get<MyLeadPage>('/leads/mine', { params })
  return data
}

// The broker moving one of THEIR OWN leads through the pipeline
// (PATCH /leads/{id}/stage). Free-form, but the backend enforces the ownership
// guard (only the current holder) — a 403 means "not your lead". Returns the
// updated lead row.
export async function updateLeadStage(
  leadId: string,
  stage: string,
  issuedAt?: string,
  subStage?: string | null,
  /** The broker has seen the low-auto-premium warning and meant it. Sent only
   *  after they answer the 409 — see LeadDetail's stage handler. */
  confirm?: boolean,
): Promise<MyLead> {
  const { data } = await adminHttp.patch<MyLead>(`/leads/${leadId}/stage`, {
    stage,
    // Sent ONLY when there is one. An explicit null on every other move would
    // read as "clear the date", which is the server's decision to make when a
    // lead leaves Issued — not something the client should assert on a move
    // that has nothing to do with issuance.
    ...(issuedAt ? { issued_at: issuedAt } : {}),
    // UNLIKE issued_at, this IS sent on every move — including as null. The
    // server clears the label on any stage change (a lead marked Junk and
    // reopened must not still read Junk), and omitting the key would leave
    // the default null doing the same thing implicitly. Saying it plainly is
    // what makes the clear intentional rather than incidental.
    sub_stage: subStage ?? null,
    // Only when the broker has actually confirmed. Omitted otherwise so the
    // server's own default (false) decides, rather than the client asserting
    // a "no" that means the same thing.
    ...(confirm ? { confirm: true } : {}),
  })
  return data
}

/**
 * The broker's judgement on a Meta lead: is this a real prospect?
 * (PATCH /leads/{id}/qualification)
 *
 * This is what Meta hears about. An unqualified lead sends NOTHING — no
 * CompleteRegistration at Quote Done (migrations/0078, carried into 0100) —
 * so the ad platform learns from prospects worth having rather than from
 * everyone who filled in a form.
 *
 * REQUIRED before the lead can move to Quote Done or beyond; until it is
 * answered the stage change is refused with a 422. Meta leads only: a manual
 * lead has no conversion to attach an answer to and is never asked.
 *
 * There is no "skip" call. Skipping is the broker dismissing the dialog, which
 * sends nothing at all and leaves the answer null — which is exactly what
 * makes the question come back on their next attempt.
 */
/**
 * What the server will decide about this lead's qualification, before it is
 * asked (GET /leads/{id}/qualification-readiness).
 *
 * CALLED BEFORE THE DIALOG OPENS, so a broker is never put a question the
 * server has already answered — or one it is about to overwrite.
 *
 * `insurance` is the four-year rule's verdict, read from the applicant's
 * recorded insurance start date:
 *
 *   'ok'          long enough — ask the broker
 *   'too_recent'  DECIDED. Not qualified whatever the broker thinks, so the
 *                 dialog must not open at all
 *   'missing'     no date recorded — the stage move will be refused
 *
 * The server owns both rules; duplicating either here would drift the first
 * time one changed.
 */
export interface QualificationReadiness {
  missing: string[]
  insurance: 'ok' | 'too_recent' | 'missing'
  min_insured_years: number
}

export async function getQualificationReadiness(
  leadId: string,
): Promise<QualificationReadiness> {
  const { data } = await adminHttp.get<QualificationReadiness>(
    `/leads/${leadId}/qualification-readiness`,
  )
  return data
}

/**
 * Record one or more qualification answers.
 *
 * A PARTIAL PATCH, because the dialog saves on every tap rather than once at
 * the end: step 2 sends the vehicle count alone, step 3 the judgement alone.
 * Only the keys sent are written, so the later call cannot blank the earlier
 * one.
 *
 * OMITTING is_qualified IS NOT THE SAME AS SENDING NULL. Omitted leaves any
 * existing judgement untouched; an explicit null is refused with a 422, because
 * clearing a judgement is not something this endpoint does and a client sending
 * null by accident would otherwise erase it silently. An empty object is
 * refused too.
 */
export async function setLeadQualification(
  leadId: string,
  // is_qualified is the only field left. vehicle_count_declared was removed with
  // the dialog's vehicles question — nothing read the count, and the multi-car
  // dataset it was collected for was considered and rejected. The column still
  // exists and is simply never written.
  answers: { is_qualified?: boolean },
): Promise<MyLead> {
  const { data } = await adminHttp.patch<MyLead>(
    `/leads/${leadId}/qualification`,
    answers,
  )
  return data
}

export interface LeadForm {
  id: string
  platform_form_id: string
  group_id: string
  name: string
  created_at: string
  updated_at: string
}

export async function getLeadForms(): Promise<LeadForm[]> {
  const { data } = await adminHttp.get<LeadForm[]>('/lead-forms')
  return data
}

// Meta's own record for a form id — returned by the validate-only endpoint.
// Used to confirm a form is real and capture its real name BEFORE creating a
// group around it (nothing is written; this is a read-through to Meta).
export interface MetaFormRecord {
  id: string
  name: string
  status: string
  leads_count: number
}

export async function validateLeadForm(platformFormId: string): Promise<MetaFormRecord> {
  const { data } = await adminHttp.post<MetaFormRecord>('/lead-forms/validate', {
    platform_form_id: platformFormId,
  })
  return data
}

// The form's display name is pulled from Meta by the backend during
// validation, so only the form id and target group are sent.
export async function createLeadForm(
  platformFormId: string,
  groupId: string,
): Promise<LeadForm[]> {
  const { data } = await adminHttp.post<LeadForm[]>('/lead-forms', {
    platform_form_id: platformFormId,
    group_id: groupId,
  })
  return data
}

export async function deleteLeadForm(leadFormId: string): Promise<void> {
  await adminHttp.delete(`/lead-forms/${leadFormId}`)
}

// ── Driver ───────────────────────────────────────────────────────────────
// The intake profile behind a lead: who is actually being insured, as opposed
// to whatever a customer typed into an ad form.

export interface Driver {
  id: string
  lead_id: string
  is_principal: boolean
  relationship_to_applicant: string | null

  /**
   * The name as one string — from the lead's ad form, or as DASH printed it.
   *
   * On the APPLICANT this and phone/email are seeded from the lead when the
   * row is first created, as a starting point for the broker to confirm or
   * correct on the call. Additional drivers start blank: the lead's details
   * are the applicant's, not a spouse's.
   */
  name: string | null

  /**
   * The split of `name`, and what Meta actually matches on — it hashes fn and
   * ln as separate parameters, so a single string reaches it fused and
   * matches nobody.
   *
   * Splitting is guesswork a rule cannot do ("SHARMA KARAN" is Karan Sharma,
   * "ASHNA EAPEN" is Ashna Eapen, and nothing in either string says which),
   * so it is derived once when the name is set and left EDITABLE — the broker
   * seeing the boxes is the review step. Blank means it could not be resolved
   * and nobody has confirmed it yet, never a guess.
   *
   * middle_name is never sent to Meta. It exists so a middle name stays out
   * of first_name, where "JOHN MICHAEL" would hash to "johnmichael".
   */
  first_name: string | null
  middle_name: string | null
  last_name: string | null

  phone: string | null
  email: string | null
  date_of_birth: string | null
  gender: string | null
  marital_status: string | null

  license_number: string | null
  license_province: string | null
  // Graduated licensing — G1 (learner) -> G2 -> G. Collected from the
  // customer over the call; nothing else is a source for these dates.
  g1_date: string | null
  g2_date: string | null
  g_date: string | null
  /** From the DASH Policies summary. Dates as printed — gaps are not
   *  interpreted, see migration 0098. renewal_date is normally in the future. */
  renewal_date: string | null
  insured_since: string | null
  current_insurer_since: string | null

  street_address: string | null
  unit_number: string | null
  city: string | null
  province: string | null
  postal_code: string | null

  /** Convictions in the last 3 years — the BROKER's answer (0114), collected by
   *  the qualification dialog. Convictions live on the MVR abstract rather than
   *  the DASH report, and the broker has the customer on the phone when the
   *  question is put, so this is typed rather than parsed. Read back so
   *  reopening the dialog shows what was already recorded. */
  convictions_3y: number | null

  /** At-fault claims in the last 6 years — DASH's OWN count, read from one line
   *  in the report header (0114). Written only by the DASH path, never by a
   *  broker. Null means unknown (no report, or unreadable), which is the
   *  opposite of a clean record and must never be treated as zero. */
  at_fault_claims_6y: number | null

  /** Licensed more than five years — the broker's answer in the qualify
   *  dialog (0117). Null means unanswered. */
  licensed_5y_plus: boolean | null

  /** Where this driver's DASH PDF is stored, or null if none was uploaded
   *  (migration 0102). READ-ONLY: the upload path is the only writer, and it
   *  is deliberately absent from the update request. The screen uses it to
   *  decide whether the Download DASH button is live — never to build a URL,
   *  which is minted per click and expires. */
  dash_report_path: string | null
}

/**
 * The lead's applicant driver, CREATED BLANK if it does not exist yet.
 *
 * A GET that can write, deliberately: the applicant row is not something a
 * broker decides to create, it is a fact of working the lead. Idempotent —
 * calling it twice returns the same row, never a second one.
 */
export async function getLeadDriver(leadId: string): Promise<Driver> {
  const { data } = await adminHttp.get<Driver>(`/leads/${leadId}/driver`)
  return data
}

/**
 * Every driver on the lead — the applicant plus any spouse/children — in tab
 * order, applicant first.
 *
 * Creates the applicant if this lead has never been opened, for the same
 * reason the single-driver read does: every lead has one by definition, so an
 * empty list would describe a state that cannot exist.
 */
export async function getLeadDrivers(leadId: string): Promise<Driver[]> {
  const { data } = await adminHttp.get<Driver[]>(`/leads/${leadId}/drivers`)
  return data
}

/**
 * Add an additional driver to the lead. Relationship is the only field —
 * everything else is filled in on New Policy once the row exists.
 *
 * Not idempotent: two children on one lead are two real people, so calling
 * this twice creates two rows.
 */
export async function addLeadDriver(
  leadId: string,
  relationship: string,
): Promise<Driver> {
  const { data } = await adminHttp.post<Driver>(`/leads/${leadId}/drivers`, {
    relationship_to_applicant: relationship,
  })
  return data
}

/**
 * Remove an additional driver. The applicant is rejected by the backend (400).
 */
export async function deleteLeadDriver(driverId: string): Promise<void> {
  await adminHttp.delete(`/drivers/${driverId}`)
}

/**
 * Patch a driver with ONLY the fields passed.
 *
 * Partial by design: the intake form is filled over several calls, so sending
 * just a licence number must not blank out a name captured earlier. Omitting a
 * key leaves it untouched; sending an explicit null clears it.
 */
export async function updateDriver(
  driverId: string,
  patch: Partial<Omit<Driver, 'id' | 'lead_id' | 'is_principal'>>,
): Promise<Driver> {
  const { data } = await adminHttp.patch<Driver>(`/drivers/${driverId}`, patch)
  return data
}

// ── Address lookup ───────────────────────────────────────────────────────
// Type-ahead behind the Address panel. Proxied through our own backend rather
// than called from here: the Google key bills to a real account and stays on
// the server (see backend/services/address.py).

export interface AddressSuggestion {
  place_id: string
  description: string
}

export interface AddressDetail {
  street_address: string | null
  city: string | null
  province: string | null
  postal_code: string | null
  formatted: string | null
}

/** Addresses matching what has been typed. Returns [] under 3 characters. */
export async function suggestAddresses(q: string): Promise<AddressSuggestion[]> {
  const { data } = await adminHttp.get<AddressSuggestion[]>('/address/suggest', { params: { q } })
  return data
}

/**
 * The picked address, split into driver's own columns.
 *
 * No unit_number — Google does not return one. The broker types it from what
 * the customer says.
 */
export async function getAddressDetail(placeId: string): Promise<AddressDetail> {
  const { data } = await adminHttp.get<AddressDetail>(`/address/${placeId}`)
  return data
}

// ── Property ─────────────────────────────────────────────────────────────
// The homes on a combined auto+property quote. Hangs off the APPLICANT, same
// as vehicles: the lead is whoever filled in the ad, which may not be who owns
// the house (see backend/migrations/0052_property.sql).

/** Which property this is on the quote. */
export type PropertyRole = 'Primary' | 'Rental Dwelling' | 'Rented Condo'
/** Which intake form applies to this row, and so which fields are relevant. */
export type PropertyType = 'Tenant' | 'Homeowner' | 'Condo'

export interface Property {
  id: string
  driver_id: string
  property_role: PropertyRole
  property_type: PropertyType

  // The Primary's is seeded from the applicant's address; a rental's is typed
  // fresh, being a different building.
  street_address: string | null
  city: string | null
  province: string | null
  postal_code: string | null

  // Common to all three forms.
  insured_since: string | null
  insured_with_property_since: string | null
  structure: string | null
  year_built: number | null
  notes: string | null

  // Homeowner and Condo only.
  stories: number | null
  full_baths: number | null
  half_baths: number | null
  number_of_mortgages: number | null
  finished_percent: string | null
  electrical_year: number | null
  plumbing_year: number | null
  heating_year: number | null
  roof_year: number | null
  water_heater_year: number | null
  burglar_alarm: string | null
  smoke_detectors: number | null
  fire_extinguishers: number | null
  sump_type: string | null

  // Condo only.
  contents_value: number | null

  created_at: string
  updated_at: string
}

/** This applicant's properties — the Primary first, then rentals in order. */
export async function getDriverProperties(driverId: string): Promise<Property[]> {
  const { data } = await adminHttp.get<Property[]>(`/drivers/${driverId}/properties`)
  return data
}

/**
 * Add an investment property.
 *
 * There is no call to add a Primary: that row exists because the quote is
 * combined, so it comes from setPolicyType instead.
 */
export async function addDriverProperty(
  driverId: string,
  property_role: Exclude<PropertyRole, 'Primary'>,
  property_type: PropertyType,
): Promise<Property> {
  const { data } = await adminHttp.post<Property>(`/drivers/${driverId}/properties`, {
    property_role,
    property_type,
  })
  return data
}

/** Partial by design — omitting a key leaves it untouched, null clears it. */
export async function updateProperty(
  propertyId: string,
  patch: Partial<Omit<Property, 'id' | 'driver_id' | 'property_role'>>,
): Promise<Property> {
  const { data } = await adminHttp.patch<Property>(`/properties/${propertyId}`, patch)
  return data
}

/** Remove an investment property. The Primary is refused (400). */
export async function deleteProperty(propertyId: string): Promise<void> {
  await adminHttp.delete(`/properties/${propertyId}`)
}

/**
 * Whether this quote bundles a property, and which kind.
 *
 * Creates or destroys the applicant's own home. Throws 409 when the change
 * would delete properties — switching to Standalone, or to Tenants while
 * rentals exist — with a message naming how many. Resend with confirm to
 * proceed.
 */
export async function setPolicyType(
  leadId: string,
  policy_type: string,
  confirm = false,
): Promise<MyLead> {
  const { data } = await adminHttp.patch<MyLead>(`/leads/${leadId}/policy-type`, {
    policy_type,
    confirm,
  })
  return data
}

// ── Vehicle ──────────────────────────────────────────────────────────────
// The cars being quoted. Entered entirely by hand: the broker reads DASH's own
// website during the call and types in what they see. Nothing is auto-imported.
//
// Hangs off the DRIVER, not the lead — vehicles belong to the primary
// applicant, and the route says whose cars these are.

export interface VehicleRow {
  id: string
  driver_id: string

  vin: string | null
  year_make_model: string | null

  condition: 'New' | 'Used' | null
  purchase_date: string | null
  km_at_purchase: number | null
  ownership: 'Paid Off' | 'Financed' | 'Leased' | null
  finance_company: string | null

  annual_km: number | null
  one_day_distance_km: number | null
  parking_at_night: string | null

  winter_tires: boolean | null
  anti_theft: boolean | null

  coverage_type: string | null
}

/** This driver's vehicles, oldest first. Empty for anyone but the applicant. */
export async function getDriverVehicles(driverId: string): Promise<VehicleRow[]> {
  const { data } = await adminHttp.get<VehicleRow[]>(`/drivers/${driverId}/vehicles`)
  return data
}

/**
 * Add a car to the quote. Sent empty: the row has to exist before any field
 * can be saved against its id, so it is created first and filled in after.
 */
export async function addDriverVehicle(driverId: string): Promise<VehicleRow> {
  const { data } = await adminHttp.post<VehicleRow>(`/drivers/${driverId}/vehicles`, {})
  return data
}

/**
 * Patch a vehicle with ONLY the fields passed — same partial semantics as
 * updateDriver. Omitting a key leaves it untouched; an explicit null clears it.
 */
export async function updateVehicle(
  vehicleId: string,
  patch: Partial<Omit<VehicleRow, 'id' | 'driver_id'>>,
): Promise<VehicleRow> {
  const { data } = await adminHttp.patch<VehicleRow>(`/vehicles/${vehicleId}`, patch)
  return data
}

/** Remove a car from the quote — sold, or never theirs. */
export async function deleteVehicle(vehicleId: string): Promise<void> {
  await adminHttp.delete(`/vehicles/${vehicleId}`)
}




/**
 * The signed-in broker's own profile.
 *
 * No broker id is sent — the backend reads it from the session. Accepting one
 * would turn this into a directory of every broker's licence number, address
 * and date of birth, readable by anyone with a login.
 *
 * Deliberately absent: anything about blocks, lockouts or password state. See the backend's
 * MyProfileResponse for the full deny-by-default list.
 */
export type MyProfile = {
  name: string
  email: string
  role: string
  phone: string | null
  date_of_birth: string | null
  address: string | null
  group: { name: string; group_number: number } | null

  /** A short-lived signed link to the broker's photo, for the header avatar.
   *  Null when no photo is on file — the avatar falls back to initials. Not
   *  worth caching: it expires, and a fresh one arrives with every profile
   *  fetch. Only an admin can upload one. */
  photo_url: string | null

  ribo_license_number: string | null
  lines_of_authority: string[]
  resident_province: string | null
  license_expiry_date: string | null
  eo_start_date: string | null
  eo_expiry_date: string | null

  daily_cap: number | null
  max_cap: number | null
  pending_daily_cap: number | null
  pending_daily_cap_effective_at: string | null
  pending_max_cap: number | null
  pending_max_cap_effective_at: string | null
  availability_status: string | null
  ready_for_more: boolean

  /** Whether this broker has stopped their own leads — see setMyAvailability.
   *
   *  NOT optional, because the header seeds its switch from this field. If the
   *  backend ever stopped sending it, `undefined` would render as OFF and the
   *  broker would be told leads are coming while they are genuinely stopped. */
  self_paused: boolean
}

export async function getMyProfile(): Promise<MyProfile> {
  const { data } = await adminHttp.get<MyProfile>('/my-profile')
  return data
}

/** Change the broker's own contact number. The only self-editable field. */
export async function updateMyPhone(phone: string): Promise<MyProfile> {
  const { data } = await adminHttp.patch<MyProfile>('/my-profile', { phone })
  return data
}

/**
 * A person's initials for an avatar fallback — "Eldho George" -> "EG".
 *
 * First and LAST word, so a middle name does not push the surname out.
 * Falls back to a dash rather than an empty circle, which reads as a broken
 * image rather than an absent name.
 */
export function initialsOf(name: string | null | undefined): string {
  const words = (name ?? '').trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '—'
  const first = words[0][0]
  const last = words.length > 1 ? words[words.length - 1][0] : ''
  return (first + last).toUpperCase()
}

// ── Meta conversion signals (Admin › Meta Conversions) ───────────────────
// The admin view of what we have told Meta about each lead, and what failed.
//
// It exists because a failure had nowhere to surface. Meta returns HTTP 400
// for an expired access token, every 400 is treated as permanent, and a Failed
// row is never picked up by the drain again — so a token expiring overnight
// lost every conversion queued in that window, silently.

export interface MetaSignalRow {
  id: string
  event_name: 'Lead' | 'CompleteRegistration' | 'Purchase'
  status: 'Pending' | 'Sent' | 'Failed'
  /** Meta's own numeric code. 190 = the access token, not the event. Null on a
   *  timeout, which never reached Meta at all. */
  error_code: number | null
  error_message: string | null
  attempt_count: number
  sent_at: string | null
  created_at: string
  /** Whose conversion this is — a row saying "CompleteRegistration /
   *  Failed" is useless without it. */
  lead: { id: string; name: string; email: string; source: string } | null
  /** History only. Every signal since migration 0133 goes to Auto Insurance
   *  and is 'legacy'; any other value is from the retired five-dataset setup. */
  dataset_target?: string | null
  /** Meta's request id, which their support asks for on a disputed event. */
  fbtrace_id: string | null
}

export async function getMetaSignals(params?: {
  status?: 'Pending' | 'Sent' | 'Failed'
  limit?: number
}): Promise<{ rows: MetaSignalRow[] }> {
  const { data } = await adminHttp.get<{ rows: MetaSignalRow[] }>('/meta-signal', {
    params,
  })
  return data
}

/**
 * Put ONE failed signal back in the queue; the next drain sends it.
 *
 * Safe because the row keeps its id — that id is the event_id Meta
 * deduplicates on, so retrying something Meta actually received cannot count
 * one conversion twice. The backend refuses anything not Failed with a 409.
 */
export async function retryMetaSignal(signalId: string): Promise<MetaSignalRow> {
  const { data } = await adminHttp.post<MetaSignalRow>(
    `/meta-signal/${signalId}/retry`,
  )
  return data
}

/** Send every Pending signal now, rather than waiting up to 15 minutes.
 *
 *  `skipped_locked` means a send pass was ALREADY RUNNING and this call did
 *  nothing — not a failure. The rows are still Pending and the pass in flight
 *  will take them. */
export async function sendPendingMetaSignals(): Promise<{
  sent: number
  failed: number
  skipped: number
  checked: number
  skipped_locked?: boolean
}> {
  const { data } = await adminHttp.post('/meta-signal/send-pending')
  return data
}

/**
 * Re-queue every signal that failed on a DEAD ACCESS TOKEN (Meta code 190).
 *
 * THE BUTTON TO PRESS AFTER REPLACING A TOKEN. A credential dying overnight
 * fails every conversion queued in that window, and retrying them one row at a
 * time is unusable at that scale.
 *
 * NOT "retry everything that failed", deliberately: a stale event_time or a bad
 * hash in the same list was judged on its merits, and resending it just repeats
 * a request Meta already refused. Every row keeps its id — the event_id Meta
 * deduplicates on — so this cannot double-count a conversion.
 */
export async function retryDeadTokenMetaSignals(): Promise<{ requeued: number }> {
  const { data } = await adminHttp.post('/meta-signal/retry-dead-token')
  return data
}

/** How far one milestone of one lead has got.
 *
 *  none        not happened yet, so nothing was queued
 *  sent        it has reached Meta
 *  waiting     it is still to be sent
 *  failed      Meta refused it
 *  not_set_up  queued, but this environment has no dataset configured */
export type MetaMilestoneState = 'none' | 'sent' | 'waiting' | 'failed' | 'not_set_up'

export type MetaMilestone = 'Lead' | 'CompleteRegistration' | 'Purchase'

/** One Meta lead and what Meta has been told about it. */
export interface MetaLeadProgress {
  lead_id: string
  name: string
  stage: string
  /** When the lead was downloaded into the CRM. */
  arrived_at: string
  /** Null while the lead is unassigned. */
  broker_name: string | null
  /** The lead's worst milestone: failed beats waiting beats sent. 'none' is a
   *  lead with nothing reportable yet. */
  status: 'sent' | 'waiting' | 'failed' | 'none'
  milestones: Record<MetaMilestone, MetaMilestoneState>
  failures: {
    id: string
    event_name: MetaMilestone
    dataset_target: string
    error_code: number | null
    error_message: string | null
  }[]
  /** OPTIONAL until the backend sends them (Meta Quality screen, 2026-10-25).
   *  The screen works without them: Qualified falls back to the
   *  CompleteRegistration milestone, and the filters show only "All". */
  /** The broker's answer: true / false, or null when nobody has asked. */
  qualified?: boolean | null
  broker_id?: string | null
  group?: { id: string; name: string } | null
  community?: { id: string; name: string } | null
  /** The yearly premium the Purchase carried, when there is one. */
  sale_value?: number | string | null
}

export interface MetaLeadProgressResponse {
  days: number
  leads: MetaLeadProgress[]
  /** CompleteRegistration events sent to Meta in the last 7 days — how close
   *  the ads are to the ~50 a week Meta needs to finish learning. Optional
   *  until the backend sends it. */
  qualified_sent_7d?: number
}

/** Every Meta lead that arrived in the last `days`, newest first. */
export async function getMetaLeadProgress(days = 90): Promise<MetaLeadProgressResponse> {
  const { data } = await adminHttp.get('/meta-signal/leads', { params: { days } })
  return data
}

/** Counts behind the admin screen's tiles and charts. */
export interface MetaSignalSummary {
  total: number
  by_status: { Sent: number; Pending: number; Failed: number }
  by_event: { event: string; count: number }[]
  /** One entry per day, gaps included — a chart with missing days rescales
   *  and makes a quiet week look busy. */
  series: { day: string; sent: number; failed: number; pending: number }[]
  /** Meta error code -> how many failures carry it. "190" is the token. */
  error_codes: Record<string, number>
}

export async function getMetaSignalSummary(days = 14): Promise<MetaSignalSummary> {
  const { data } = await adminHttp.get<MetaSignalSummary>('/meta-signal/summary', {
    params: { days },
  })
  return data
}

/** Is anything wrong on Meta's side right now? Asked directly, rather than
 *  inferred from conversions that have already failed. */
export interface MetaHealth {
  token: {
    ok: boolean
    reason: string | null
    /** SYSTEM_USER tokens never expire, so `ok` is the field that matters. */
    type?: string
    expires_at?: number | null
    scopes?: string[]
    dataset_id?: string | null
  }
  failed_count: number
  /** Meta error code -> count. "190" means the token itself. */
  failed_by_code: Record<string, number>
  /** Pending for over an hour — not waiting for the drain, stuck. */
  stuck_count: number
}

export async function getMetaHealth(): Promise<MetaHealth> {
  const { data } = await adminHttp.get<MetaHealth>('/meta-signal/health')
  return data
}

/** How much matching data our SENT events actually carried, by event type.
 *
 *  Separate denominators per event on purpose: a Lead fires when the lead row
 *  is created and is legitimately thin, while a CompleteRegistration fires at
 *  Quote Done with the applicant filled in. One combined percentage would
 *  describe neither.
 *
 *  Counts and field NAMES only — never a hashed value. */
export interface MetaSendQuality {
  events: {
    event_name: string
    total: number
    average_fields: number
    /** field -> how many of this event type carried it. */
    coverage: Record<string, { sent: number; total: number }>
  }[]
  /** Which dataset the events went to. "not recorded" for rows sent before
   *  the id was stored — a real state, not a mismatch. */
  datasets: Record<string, number>
  /** Meta's own words on an ACCEPTED event, grouped: the same warning on
   *  forty events is one problem, not forty. */
  warnings: { message: string; count: number; last_seen: string | null }[]
  /** The display vocabulary, sent WITH the data rather than duplicated here:
   *  we and Meta name five of these fields differently, and a second copy of
   *  that mapping is a second place for it to drift. */
  fields: { field: string; label: string; meta_key: string }[]
}

/** What our sent events carried, for Auto Insurance — the one dataset (0133). */
export async function getMetaSendQuality(days = 14): Promise<MetaSendQuality> {
  const { data } = await adminHttp.get<MetaSendQuality>('/meta-signal/send-quality', {
    params: { days },
  })
  return data
}

/** What META says it received, over the same window.
 *
 *  FETCHED SEPARATELY from getMetaSendQuality, which is a fast database read.
 *  This one calls Meta, so a slow or unreachable Meta must not hold up figures
 *  that are already in hand.
 *
 *  `available: false` means we could not ASK — not that nothing arrived. Those
 *  two readings look identical as zeros and mean opposite things. */
export interface MetaReceived {
  available: boolean
  days: number
  dataset_id?: string
  /** Meta's own "last received", straight from the dataset object. */
  last_fired_time?: string | null
  /** event name -> how many Meta counted. */
  totals?: Record<string, number>
  /** event name -> Meta's key name -> how many events carried it.
   *
   *  RECEIVED, never MATCHED. Meta does not expose match results for
   *  conversion-leads integrations, so this counts identifiers that arrived —
   *  it says nothing about whether any of them found a person. */
  match_keys?: Record<string, Record<string, number>>
}

export async function getMetaReceived(days = 14): Promise<MetaReceived> {
  const { data } = await adminHttp.get<MetaReceived>('/meta-signal/meta-received', {
    params: { days },
  })
  return data
}

// ── DASH prefill ───────────────────────────────────────────────────────
// The broker can supply the DASH report they already pulled, instead of
// retyping what it says. OPTIONAL — typing still works exactly as before.
//
// Only BLANK fields are filled. Anything the broker typed is left alone, and
// the response says which fields were skipped for that reason, so the screen
// can report it rather than leaving them to compare the form against the PDF.

export interface DashPrefillResult {
  /** Columns DASH filled in, e.g. ["city", "gender", "license_number"]. */
  driver_filled: string[]
  /** Columns left alone because the broker had already typed something. */
  driver_skipped: string[]
  vehicles_added: number
  /** Other people on the most recent policy, added as additional drivers.
   *
   *  They land with relationship 'NA' — the report knows they are on the
   *  policy and cannot know what they are to the applicant — so the broker
   *  picks that on New Policy. */
  drivers_added: number
  /** WHOSE report this was, as printed — DASH's own "LAST,FIRST" form.
   *
   *  Read from the report itself, not from what was written, so it is still
   *  here when the name was skipped because the broker had typed one. Nothing
   *  refuses a report for the wrong person; showing the subject is what lets
   *  the broker notice. Null if the header could not be read. */
  subject_name: string | null
  subject_license_number: string | null
}

/** Read an uploaded DASH PDF and prefill this driver. The file is not stored. */
export async function uploadDashReport(
  driverId: string,
  file: File,
): Promise<DashPrefillResult> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await adminHttp.post<DashPrefillResult>(
    `/drivers/${driverId}/dash-report`,
    form,
    // Content-Type UNSET, deliberately. adminHttp defaults every request to
    // application/json, which on a FormData body suppresses the multipart
    // boundary the browser would otherwise generate — so the server receives
    // a body it cannot split into fields and reports "file: Field required"
    // about a file that was plainly attached.
    //
    // Undefined rather than a hand-written multipart/form-data: the boundary
    // is a random token the browser picks per request, and a header naming
    // the type without it is no more parseable than the JSON one.
    { headers: { 'Content-Type': undefined } },
  )
  return data
}

/** Same, from a link the broker already has open. Fetched server-side. */
/**
 * A short-lived link to the stored copy of this driver's DASH report.
 *
 * Fetched per click rather than held: the bucket is private and the URL
 * expires in fifteen minutes, so a link kept in state would stop working
 * while the page was still open.
 */
export async function dashReportUrl(driverId: string): Promise<string> {
  const { data } = await adminHttp.get<{ url: string }>(
    `/drivers/${driverId}/dash-report/file`,
  )
  return data.url
}

export async function parseDashReportUrl(
  driverId: string,
  url: string,
): Promise<DashPrefillResult> {
  const { data } = await adminHttp.post<DashPrefillResult>(
    `/drivers/${driverId}/dash-report/url`,
    { url },
  )
  return data
}

// ── MVR register ────────────────────────────────────────────────────────
// Every MVR a broker pulls, kept as the MTO "Records of Licensed Information"
// log (backend migrations/0132). The broker drops the PDF; the backend reads
// the date, licence, name and ARIS order number off it and drops the file.

export type MvrBusinessReason = 'new_business' | 'adding_driver'
export type MvrInfoRetrieved = 'mvr' | 'predictor'

export const MVR_BUSINESS_REASONS: { value: MvrBusinessReason; label: string }[] = [
  { value: 'new_business', label: 'New Business' },
  { value: 'adding_driver', label: 'Adding New Driver' },
]

export const MVR_INFO_RETRIEVED: { value: MvrInfoRetrieved; label: string }[] = [
  { value: 'mvr', label: 'MVR' },
  { value: 'predictor', label: 'Predictor' },
]

export interface MvrPull {
  id: string
  broker_id: string
  completed_by: string
  driver_name: string | null
  /** Full for an admin and on the head-office report, masked for a broker.
   *  The backend decides; the screen only prints it. */
  licence_number: string
  licence_masked: string
  requested_on: string
  aris_order_number: string
  business_reason: MvrBusinessReason
  info_retrieved: MvrInfoRetrieved
  use_completed_on: string
  destruction_on: string
  created_at: string
}

export interface MvrMonth {
  month: string
  pulls: MvrPull[]
  /** Everyone in the register, for the admin's Broker dropdown. Empty for a
   *  broker, who only ever sees themselves. */
  brokers: { broker_id: string; name: string }[]
}

export interface MvrUploadResult {
  file_name: string
  status: 'logged' | 'already_logged' | 'unreadable'
  detail: string | null
  pulls: MvrPull[]
}

/** Log one or more MVR PDFs. One result per file; a bad file never stops the
 *  rest. Content-Type unset for the reason uploadDashReport gives. */
export async function uploadMvrs(files: File[]): Promise<MvrUploadResult[]> {
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  const { data } = await adminHttp.post<MvrUploadResult[]>('/mvr-pulls', form, {
    headers: { 'Content-Type': undefined },
  })
  return data
}

export async function getMvrPulls(month: string, brokerId?: string): Promise<MvrMonth> {
  const { data } = await adminHttp.get<MvrMonth>('/mvr-pulls', {
    params: { month, broker_id: brokerId || undefined },
  })
  return data
}

export async function updateMvrPull(
  id: string,
  changes: { business_reason?: MvrBusinessReason; info_retrieved?: MvrInfoRetrieved },
): Promise<MvrPull> {
  const { data } = await adminHttp.patch<MvrPull>(`/mvr-pulls/${id}`, changes)
  return data
}

export async function deleteMvrPull(id: string): Promise<void> {
  await adminHttp.delete(`/mvr-pulls/${id}`)
}

export async function setMvrReportPassword(password: string): Promise<void> {
  await adminHttp.put('/mvr-report/password', { password })
}

// The head-office report is opened with a password, not a broker session, so
// it does NOT go through adminHttp: that client's interceptor attaches the
// broker's token and treats a 401 as "session expired, refresh or log out" —
// and here a 401 only means "wrong or changed password".
const reportHttp = axios.create({
  baseURL: import.meta.env.VITE_ADMIN_API_BASE_URL ?? 'http://127.0.0.1:8000',
  headers: { 'Content-Type': 'application/json' },
})

export async function unlockMvrReport(password: string): Promise<string> {
  const { data } = await reportHttp.post<{ token: string }>('/mvr-report/unlock', { password })
  return data.token
}

export async function getMvrReport(
  token: string,
  month: string,
  brokerId?: string,
): Promise<MvrMonth> {
  const { data } = await reportHttp.get<MvrMonth>('/mvr-report', {
    params: { month, broker_id: brokerId || undefined },
    headers: { Authorization: `Bearer ${token}` },
  })
  return data
}

// ── Commission rates ────────────────────────────────────────────────────
// Two rates and the broker's share, and that is the whole configuration.
// Nothing is stored per policy: commission is these numbers times the
// premiums, computed when a screen is opened.

export interface CommissionRates {
  /** Percentages as the admin types them — 15 means 15%. Strings, like every
   *  other money-adjacent value crossing this boundary. */
  auto_rate_pct: string
  property_rate_pct: string
  /** What the broker keeps of a policy's commission (0134). One figure for
   *  every broker. */
  broker_share_pct: string
}

export async function getCommissionRates(): Promise<CommissionRates> {
  const { data } = await adminHttp.get<CommissionRates>('/commission-rates')
  return data
}

/** All three together — they are one card, and a partial save would leave a
 *  moment where one is new and one old. */
export async function saveCommissionRates(rates: CommissionRates): Promise<CommissionRates> {
  const { data } = await adminHttp.put<CommissionRates>('/commission-rates', rates)
  return data
}

// ── Website leads ───────────────────────────────────────────────────────
// Who receives leads from the website's quote form, and how many a day. A
// routing system of its own (migration 0127): it ignores groups, and its limit
// is separate from the Facebook daily cap — neither counts the other's leads.

export interface WebsiteBroker {
  broker_id: string
  name: string
  group_id: string
  group_name: string | null
  is_on: boolean
  /** Website leads a day. Separate from the broker's Facebook daily cap. */
  daily_cap: number
  /** Website leads received since the last daily reset. */
  today: number
  self_paused: boolean
  is_blocked_by_admin: boolean
  is_active: boolean
  /** The group's community. Null only from a backend whose database has not
   *  had migration 0129 yet. */
  community_id: string | null
  community_name: string | null
  /** When the broker starts receiving leads; null once they have. The drain
   *  skips them until then, even when switched on. */
  eligible_from: string | null
}

export interface WebsiteLeadBoard {
  /** Stored submissions nobody could take yet — everyone switched on is
   *  paused or at their limit, or nobody is switched on. */
  waiting: number
  /** Submissions that can never become a lead as sent. Needs a person. */
  failed: number
  brokers: WebsiteBroker[]
}

export async function getWebsiteLeadBoard(): Promise<WebsiteLeadBoard> {
  const { data } = await adminHttp.get<WebsiteLeadBoard>('/admin/website-leads')
  return data
}

export interface WebsiteBrokerSetting {
  broker_id: string
  is_on: boolean
  daily_cap: number
}

/** One field at a time: the switch saves on click, the limit on blur. The
 *  server runs the drain after answering, so a switch-on can release waiting
 *  submissions straight away. */
export async function updateWebsiteBroker(
  brokerId: string,
  change: { is_on?: boolean; daily_cap?: number },
): Promise<WebsiteBrokerSetting> {
  const { data } = await adminHttp.patch<WebsiteBrokerSetting>(
    `/admin/website-leads/${brokerId}`,
    change,
  )
  return data
}

/** The same change for many brokers in one request — all of them, or none
 *  (migration 0129). The server runs the drain once afterwards. */
export async function updateWebsiteBrokers(
  brokerIds: string[],
  change: { is_on?: boolean; daily_cap?: number },
): Promise<WebsiteBrokerSetting[]> {
  const { data } = await adminHttp.patch<WebsiteBrokerSetting[]>('/admin/website-leads', {
    broker_ids: brokerIds,
    ...change,
  })
  return data
}
