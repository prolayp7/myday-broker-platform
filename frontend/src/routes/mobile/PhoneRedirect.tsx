import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { isPhone, mobileEquivalent } from './isPhone'

/**
 * Sends a PHONE to the mobile companion, once.
 *
 * WHY A WIDTH AND NOT USER-AGENT SNIFFING: user-agent strings lie, and iPadOS
 * deliberately reports itself as a Mac. Width is the thing we actually care
 * about — whether the desktop layout fits — so it is the thing to measure.
 *
 * 768px is the line. Below it is a phone in either orientation, including an
 * iPhone Pro Max in landscape (~430px portrait, ~932px landscape — see the
 * orientation note below). Above it is an iPad or a laptop, and the desktop
 * CRM is the right screen for both: an iPad in landscape is 1080px+, which is
 * a desktop-class screen, and building a third layout for the middle would
 * mean maintaining a case nothing needs.
 *
 * ORIENTATION IS DELIBERATELY IGNORED after the first check. A phone held
 * sideways is still a phone, and more importantly a broker who rotates mid-task
 * should not have the entire app swap underneath them.
 */

export function PhoneRedirect() {
  const navigate = useNavigate()
  const location = useLocation()

  // ONCE PER PAGE LOAD, not once per render and not on every resize.
  //
  // Without this guard a broker who deliberately opens the full CRM on their
  // phone — which is a legitimate thing to want, some screens only exist there
  // — would be bounced straight back to /m and could never reach it. The
  // redirect is a helpful default on arrival, not a rule to enforce.
  const done = useRef(false)

  useEffect(() => {
    if (done.current) return
    done.current = true

    // Already heading somewhere mobile, or to login: leave it alone. Matched
    // as /m or /m/..., NOT startsWith('/m'): that also matched /mvr-log and
    // /mvr-report, and skipped them only by accident of spelling.
    //
    // /mvr-report is the head-office page, opened with a password by someone
    // with no CRM login. Sending their phone to /m would land them on a broker
    // login they cannot get past.
    const path = location.pathname
    if (path === '/m' || path.startsWith('/m/') || path === '/login' || path === '/mvr-report') return

    if (isPhone()) {
      // THE MOBILE EQUIVALENT, NOT A HARDCODED /m. This used to send every
      // phone to My Day whatever it had asked for, which threw away both the
      // screen and its query string — so the renewal WhatsApp's
      // /leads?lead=<uuid> landed on My Day with the lead id gone.
      //
      // mobileEquivalent carries the query through and returns null for the
      // screens the companion does not have (the admin ones), which still fall
      // back to /m because a phone cannot use them.
      const target = mobileEquivalent(location.pathname + location.search) ?? '/m'
      // replace: true so the desktop URL never enters history. Otherwise the
      // back button returns to a screen we just decided does not fit, and the
      // broker is stuck bouncing between the two.
      navigate(target, { replace: true })
    }
  }, [location.pathname, location.search, navigate])

  return null
}
