import type { AxiosError, InternalAxiosRequestConfig } from 'axios'
import { adminHttp, refreshAccessToken } from '@/data/api'
import { clearStoredAuth, getStoredTokens, setStoredAccessToken } from './AuthContext'

// Wires adminHttp so every admin screen gets a working session without
// each call site touching a token directly:
//   1. Attach the current access_token as a Bearer header on every request.
//   2. On a 401, silently trade the refresh_token for a new access_token
//      and retry the ONE failed request — the broker never sees this happen.
//   3. If the refresh itself fails (refresh_token also dead — expired, or
//      the broker was blocked/archived, see auth_dependencies.py), give up
//      and send the broker to /login.
//
// Don't attach a Bearer header — these run before a session exists (or, for
// refresh, ARE the token exchange), so a header would be meaningless or wrong.
const NO_TOKEN_ENDPOINTS = ['/auth/login', '/auth/login/verify', '/auth/refresh']

// Don't treat a 401 from these as "session expired → refresh & retry". Their
// 401 means something else entirely: wrong password on login, or wrong
// CURRENT password on change-password. Refreshing the token would be wrong
// (and would loop), and the caller's own catch block wants to show a clean
// field error. change-password DOES get a Bearer header (voluntary path),
// so it's here but not in NO_TOKEN_ENDPOINTS — the two lists are separate on
// purpose.
const NO_REFRESH_ON_401 = [...NO_TOKEN_ENDPOINTS, '/auth/change-password']

function matches(url: string | undefined, paths: string[]): boolean {
  return !!url && paths.some((path) => url.startsWith(path))
}

// Concurrent 401s (e.g. the onboard wizard's Promise.all of 4 calls) must
// trigger exactly ONE /auth/refresh, not one per failed request — every
// other 401 waits on this same in-flight promise and retries once it
// resolves. Reset to null after each attempt (success or failure) so the
// NEXT access-token expiry starts a fresh refresh, not a stale settled one.
let refreshInFlight: Promise<string> | null = null

function onSessionExpired() {
  clearStoredAuth()
  if (window.location.pathname !== '/login') {
    window.location.assign('/login')
  }
}

export function installAuthInterceptor() {
  adminHttp.interceptors.request.use((config: InternalAxiosRequestConfig) => {
    if (matches(config.url, NO_TOKEN_ENDPOINTS)) return config
    const tokens = getStoredTokens()
    if (tokens) {
      config.headers.Authorization = `Bearer ${tokens.accessToken}`
    }
    return config
  })

  adminHttp.interceptors.response.use(
    (response) => response,
    async (error: AxiosError) => {
      const original = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined

      if (
        error.response?.status !== 401 ||
        !original ||
        original._retried ||
        matches(original.url, NO_REFRESH_ON_401)
      ) {
        // Not a retryable 401 — e.g. a 401 from /auth/login ("wrong
        // password") or /auth/change-password ("wrong current password"),
        // neither of which means the session expired. Let the caller's own
        // catch block handle it.
        return Promise.reject(error)
      }

      const tokens = getStoredTokens()
      if (!tokens) {
        // No session to refresh — this 401 is the initial, unauthenticated
        // state, not an expiry. Let the route guard handle sending the
        // broker to /login rather than forcing a redirect from here.
        return Promise.reject(error)
      }

      original._retried = true

      // adminHttp defaults to Content-Type: application/json. On the FIRST
      // attempt axios overrides that for a FormData body, stamping in the
      // multipart boundary it generated. Replaying `original` below reuses the
      // already-built headers, where that override is gone and the stale JSON
      // content-type is back — the server then reads multipart bytes as JSON
      // and 422s on every field. Deleting the header lets axios re-derive it
      // for the retry, exactly as it did the first time.
      // .delete() rather than `delete headers[...]`: AxiosHeaders matches
      // case-insensitively, so this removes the header whatever case it was
      // stored under, where the bracket form only hits an exact-case key.
      if (typeof FormData !== 'undefined' && original.data instanceof FormData) {
        original.headers.delete('Content-Type')
      }

      try {
        if (!refreshInFlight) {
          refreshInFlight = refreshAccessToken(tokens.refreshToken)
            .then(({ access_token }) => {
              setStoredAccessToken(access_token)
              return access_token
            })
            .finally(() => {
              refreshInFlight = null
            })
        }
        const newAccessToken = await refreshInFlight
        original.headers.Authorization = `Bearer ${newAccessToken}`
        return adminHttp(original)
      } catch {
        // Refresh token is also dead (expired, or the broker was blocked/
        // archived/deactivated mid-session — see auth_dependencies.py's
        // live re-check). No path back without a full re-login.
        onSessionExpired()
        return Promise.reject(error)
      }
    },
  )
}
