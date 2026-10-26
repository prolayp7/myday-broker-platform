import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from './AuthContext'

// Wraps every screen that needs a real session. No tokens in AuthContext ->
// straight to /login, carrying the original destination so a post-login
// redirect can return the broker to where they were headed (e.g. a
// bookmarked /admin/broker-groups/:id link).
//
// This does NOT itself verify the token is still valid — that's the
// backend's job on every request (get_current_broker re-fetches the live
// broker row, see auth_dependencies.py). This guard only handles the
// "no session at all" case instantly, client-side, without waiting on a
// network round-trip just to find out.
export function RequireAuth({ children }: { children: ReactNode }) {
  const { tokens } = useAuth()
  const location = useLocation()

  if (!tokens) {
    // PATHNAME AND QUERY, not the pathname alone. A deep link's meaning often
    // lives entirely in the query — /leads?lead=<uuid> from a renewal WhatsApp
    // is the lead id and nothing else — so saving only the path returned the
    // broker to a bare list after logging in.
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname + location.search }}
      />
    )
  }

  return children
}
