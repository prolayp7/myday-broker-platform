// "What's new" — a one-line callout beside the thing that changed, shown on
// first visit after a release and gone once the broker presses Got it.
//
// NO TABLE. This is a "have you seen it yet" flag with exactly one reader: the
// browser it was written in. Nobody reports on it, audits it, or queries it,
// which is the shape localStorage exists for — and the convention this app
// already uses for 'myday.auth' and 'myday.theme'. A column on broker would
// mean a migration, a repository, a service, a route and an API call on every
// login, to store a boolean nothing reads back.
//
// THE ONE TRADE-OFF, stated plainly: per-browser, not per-person. A broker
// sees this once per browser. Strictly once-per-person across devices would
// need that broker column; it is not worth it until someone asks.
//
// DRIVEN BY A VERSION, NOT A COUNTER. The callout shows when the stored
// version differs from CURRENT.version, and Got it writes it. Ship a new
// announcement with a new version and it reopens once, for everyone, without
// touching any code but this file.

export interface Announcement {
  /** Bump this to reopen the callout for everyone. A date reads better than a
   *  number in the storage value when someone is debugging. */
  version: string
  /** ONE LINE. This sits in a small bubble beside a button; anything longer
   *  is a dialog wearing a costume. */
  text: string
}

export const CURRENT: Announcement = {
  // '-2': the first cut of this announcement shipped as a modal and was
  // dismissed during testing under the plain date, so the bubble that
  // replaced it read as already seen. A new version reopens it.
  version: '2026-09-13-2',
  text: 'Check out the new dark mode',
}

const STORAGE_KEY = 'myday.whatsnew.seen'

/** True when this browser has not yet dismissed the CURRENT announcement. */
export function shouldShow(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== CURRENT.version
  } catch {
    // Storage refused (private mode, policy). Showing it every time would be
    // a nag; never showing it loses nothing important. Stay quiet.
    return false
  }
}

export function markSeen(): void {
  try {
    localStorage.setItem(STORAGE_KEY, CURRENT.version)
  } catch {
    // Nothing to do — see shouldShow.
  }
}
