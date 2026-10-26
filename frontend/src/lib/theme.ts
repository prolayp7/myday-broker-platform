// The one place the app decides whether it is dark.
//
// THE PALETTE ALREADY EXISTED. Every token has a `.dark` value in index.css,
// every component consumes tokens, and Tailwind's `dark:` variant is bound to
// the same `.dark` class (index.css:10) — so shadcn's own dark styles and our
// tokens agree. Charts follow too: lib/tokens.ts hands Recharts `var(--…)`
// strings, which re-resolve the moment the class flips. What was missing was
// only the switch: nothing anywhere set the class on <html>.
//
// THREE STATES, NOT A BOOLEAN. 'system' is the default and follows the OS —
// a broker who never touches this gets whatever their phone or laptop already
// does, which is the only default that surprises nobody. 'light' and 'dark'
// are explicit choices and win over the OS.
//
// PERSISTED under 'myday.theme', the same localStorage convention and prefix
// as 'myday.auth' (auth/AuthContext.tsx). A preference is per-browser, not
// per-account: it describes the screen it is read on, and the same broker on
// a dim phone and a bright desk monitor may reasonably want each different.
//
// A FIRST-PAINT SCRIPT IN index.html MIRRORS THE READ BELOW. React mounts
// after the first paint, so without it a saved dark preference flashes light
// for a frame on every reload. The two must agree on the key and the states;
// change one, change both.

import { useCallback, useSyncExternalStore } from 'react'

export type Theme = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'myday.theme'
const THEMES: readonly Theme[] = ['system', 'light', 'dark']

function readStored(): Theme {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return THEMES.includes(raw as Theme) ? (raw as Theme) : 'system'
  } catch {
    // Private mode, blocked storage — treat as unset rather than crash a
    // page over a preference.
    return 'system'
  }
}

function osPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

/** What the screen should actually show, once 'system' is resolved. */
export function resolve(theme: Theme): 'light' | 'dark' {
  return theme === 'system' ? (osPrefersDark() ? 'dark' : 'light') : theme
}

function apply(theme: Theme): void {
  const dark = resolve(theme) === 'dark'
  document.documentElement.classList.toggle('dark', dark)
  // Tells the browser to draw its OWN chrome — scrollbars, form controls it
  // still owns, the colour of a native <select> list — in the matching
  // scheme. Without this a dark page gets a white scrollbar.
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
}

// ── a tiny external store, so every subscriber re-renders on one change ──
const listeners = new Set<() => void>()

function emit() {
  for (const l of listeners) l()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function setTheme(theme: Theme): void {
  try {
    if (theme === 'system') localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Storage refused; the choice still applies for this page's lifetime.
  }
  apply(theme)
  emit()
}

/**
 * Run once at startup. Applies the saved preference (the index.html script
 * already did for the first paint; this keeps React's view in step) and, for
 * 'system', follows the OS if it changes while the app is open — a laptop
 * switching to dark at sunset should not need a reload.
 */
export function initTheme(): () => void {
  apply(readStored())
  const mq = window.matchMedia('(prefers-color-scheme: dark)')
  const onChange = () => {
    if (readStored() === 'system') {
      apply('system')
      emit()
    }
  }
  mq.addEventListener('change', onChange)
  return () => mq.removeEventListener('change', onChange)
}

/** The current preference and a setter, for any component with a toggle.
 *
 *  Does NOT call initTheme(): that runs once from main.tsx. Two screens each
 *  mounting this hook (the desktop shell and the phone landing page) would
 *  otherwise each register their own OS-preference listener. */
export function useTheme(): [Theme, (t: Theme) => void] {
  const theme = useSyncExternalStore(subscribe, readStored, () => 'system' as Theme)
  const set = useCallback((t: Theme) => setTheme(t), [])
  return [theme, set]
}
