import { createContext, useContext, useState, type ReactNode } from 'react'

// Holds the current session (access + refresh tokens) and the broker
// identity decoded from the access token — id + role only, since that's
// all create_access_token embeds server-side (see backend/security.py).
// Persisted to localStorage so a page reload doesn't force a re-login;
// the tokens themselves are still what every request is actually gated on,
// so a stale/invalid token in storage just fails the next API call and
// the interceptor (wired in adminHttp) sends the broker back to /login.

const STORAGE_KEY = 'myday.auth'

export interface AuthTokens {
  accessToken: string
  refreshToken: string
}

export interface CurrentBroker {
  id: string
  // Two tiers — 'manager' was dropped in backend migration 0066. Kept narrow
  // so a future `role === 'manager'` fails to typecheck rather than quietly
  // comparing against a value the backend can no longer return.
  role: 'broker' | 'admin'
}

interface AuthState extends AuthTokens {
  broker: CurrentBroker
}

interface AuthContextValue {
  tokens: AuthTokens | null
  broker: CurrentBroker | null
  login: (accessToken: string, refreshToken: string) => void
  updateAccessToken: (accessToken: string) => void
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

// The access token's payload (see security.py's _create_token) — decoded
// client-side purely to read id/role for display and route-guarding.
// This is NOT a verification step; the token's signature is only ever
// checked server-side. A tampered token would just decode to garbage here
// and fail the next real API call.
function decodeBroker(accessToken: string): CurrentBroker | null {
  try {
    const payload = accessToken.split('.')[1]
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
    const claims = JSON.parse(json)
    if (!claims.sub || !claims.role) return null
    return { id: claims.sub, role: claims.role }
  } catch {
    return null
  }
}

function loadState(): AuthState | null {
  const raw = localStorage.getItem(STORAGE_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as AuthState
  } catch {
    return null
  }
}

function saveState(state: AuthState | null) {
  if (state) localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  else localStorage.removeItem(STORAGE_KEY)
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState | null>(loadState)

  function login(accessToken: string, refreshToken: string) {
    const broker = decodeBroker(accessToken)
    if (!broker) return
    const next: AuthState = { accessToken, refreshToken, broker }
    setState(next)
    saveState(next)
  }

  // Called after a silent /auth/refresh — keeps the same refresh_token and
  // broker identity, only the access_token (and its embedded claims) changes.
  function updateAccessToken(accessToken: string) {
    setState((prev) => {
      if (!prev) return prev
      const broker = decodeBroker(accessToken) ?? prev.broker
      const next: AuthState = { ...prev, accessToken, broker }
      saveState(next)
      return next
    })
  }

  function logout() {
    setState(null)
    saveState(null)
  }

  const value: AuthContextValue = {
    tokens: state ? { accessToken: state.accessToken, refreshToken: state.refreshToken } : null,
    broker: state?.broker ?? null,
    login,
    updateAccessToken,
    logout,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

// Non-hook accessors for the axios interceptor (outside React) — reads/
// writes localStorage directly rather than going through context, since
// interceptors run outside any component tree.
export function getStoredTokens(): AuthTokens | null {
  const state = loadState()
  return state ? { accessToken: state.accessToken, refreshToken: state.refreshToken } : null
}

export function setStoredAccessToken(accessToken: string) {
  const state = loadState()
  if (!state) return
  saveState({ ...state, accessToken })
}

export function clearStoredAuth() {
  saveState(null)
}
