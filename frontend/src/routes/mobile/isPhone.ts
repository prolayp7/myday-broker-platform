/**
 * THE ONE definition of "this is a phone".
 *
 * Its own file rather than living beside the PhoneRedirect component: a module
 * that exports both a component and plain functions breaks Vite's fast refresh,
 * so editing either one full-reloads the page instead of hot-swapping it.
 *
 * WHY A WIDTH AND NOT USER-AGENT SNIFFING: user-agent strings lie, and iPadOS
 * deliberately reports itself as a Mac. Width is the thing we actually care
 * about — whether the desktop layout fits — so it is the thing to measure.
 *
 * 768px is the line. Below it is a phone; above it an iPad or a laptop, and the
 * desktop CRM is right for both. An iPad in landscape is 1080px+, which is a
 * desktop-class screen, and a third layout for the middle would be a case
 * nothing needs.
 */
const PHONE_MAX_WIDTH = 768

export function isPhone(): boolean {
  return window.innerWidth <= PHONE_MAX_WIDTH
}

/** Where a broker belongs after signing in, when no specific destination was
 *  requested. */
export function homeRoute(): string {
  return isPhone() ? '/m' : '/my-day'
}

// The desktop screens the companion has its own version of. Only these two —
// the admin screens are desktop-only by design and are deliberately absent, so
// a deep link to one is returned unchanged rather than bounced to /m.
const MOBILE_EQUIVALENT: Record<string, string> = {
  '/my-day': '/m',
  '/leads': '/m/leads',
}

/**
 * The phone's version of a desktop path, or null when there isn't one.
 *
 * WHY THIS EXISTS: RequireAuth saves the path it bounced to /login, and Login
 * prefers that saved path over homeRoute() — correctly, because a bookmarked
 * deep link should survive the login it triggered. But a phone opening /leads
 * while logged out then landed on the DESKTOP /leads, because the saved path
 * is a desktop one and nothing translated it.
 *
 * Returns null off a phone, so the desktop is never redirected anywhere.
 *
 * TAKES A FULL LOCATION, NOT JUST A PATHNAME. The renewal WhatsApp's "Set Up
 * Reminder" button links to /leads?lead=<uuid>, and the lead id is the whole
 * point of the link — matching on the bare path and returning a bare path
 * dropped it, landing the broker on an unfiltered list. The query is carried
 * through untouched; only the path is translated.
 */
export function mobileEquivalent(path: string | undefined | null): string | null {
  if (!path || !isPhone()) return null
  // Split rather than parse: a URL object needs an origin, and this only ever
  // sees an app-relative path.
  const [pathname, query] = path.split('?')
  const mobile = MOBILE_EQUIVALENT[pathname]
  if (!mobile) return null
  return query ? `${mobile}?${query}` : mobile
}
