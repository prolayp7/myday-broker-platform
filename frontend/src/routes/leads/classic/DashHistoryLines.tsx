import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  dismissDashNotes,
  getDashHistory,
  setDashClaimLeftOut,
  type DashClaim,
  type DashGap,
} from '@/data/api'
import { rawDate } from './copy'
import { CopyButton } from './inline'

// What the DASH reading leaves on the paper besides the filled fields:
//
//   CheckThese            what the last reading wants the broker to look at,
//                         kept until Dismiss or the next reading
//   DashHistorySections   Insurance Gaps and Claims, read-only, between
//                         Vehicles and Premium
//
// Gaps and claims are DASH facts, not broker input: a new full reading of the
// report replaces them. Nothing shows until a report has been read.

export function CheckThese({
  driverId,
  notes,
  onDismissed,
}: {
  driverId: string
  notes: string[]
  onDismissed: () => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  return (
    <div className="cl-check" role="status">
      <div className="cl-check-h">
        <span>Check these ({notes.length})</span>
        <button
          type="button"
          className="cl-link"
          disabled={busy}
          onClick={async () => {
            setBusy(true)
            try {
              await dismissDashNotes(driverId)
              await onDismissed()
            } catch {
              toast.error('Could not dismiss — try again')
            } finally {
              setBusy(false)
            }
          }}
        >
          Dismiss
        </button>
      </div>
      {notes.map((note) => (
        <div key={note}>• {note}</div>
      ))}
    </div>
  )
}

const money = (n: number) =>
  Number(n) > 0 ? Math.round(Number(n)).toLocaleString('en-CA') : '—'

/** How a gap reason reads to a broker. The stored value stays as saved
 *  (0135's check list); only the words on screen change. */
function reasonLabel(reason: DashGap['reason']): string {
  return reason === 'Possible new vehicle' ? 'Possibly no vehicle, no insurance' : reason
}

function whose(claim: DashClaim): string {
  switch (claim.whose) {
    case 'applicant':
      return claim.claim_driver_name ?? 'Applicant'
    case 'additional':
      return claim.claim_driver_name ?? 'Additional driver'
    case 'not_on_policy':
      return claim.claim_driver_name ?? 'Driver not on the policy'
    default:
      return 'Unknown driver'
  }
}

function fault(claim: DashClaim): string {
  // As DASH prints it on the claim's top line ("At-Fault : 50%") — the share
  // of fault on the policy side. Missing is unknown, never 0%.
  if (claim.at_fault_percent === null) return '—'
  return `${Number(claim.at_fault_percent)}%`
}

export function DashHistorySections({
  driverId,
  reportPath,
  refreshKey,
}: {
  driverId: string
  /** Whether a report copy is stored — shows the sections (with their empty
   *  states) even when the reading found nothing. */
  reportPath: string | null
  /** Bumped after every reading, so the lists refresh. NOT tied to the PDF
   *  copy: a reading whose copy failed to store still saved its lists. */
  refreshKey: number
}) {
  const [gaps, setGaps] = useState<DashGap[]>([])
  const [claims, setClaims] = useState<DashClaim[]>([])

  useEffect(() => {
    let current = true
    getDashHistory(driverId)
      .then((history) => {
        if (!current) return
        setGaps(history.gaps)
        setClaims(history.claims)
      })
      // Silent: extra information, and the rest of the paper must not look
      // broken because it could not be fetched.
      .catch(() => {})
    return () => {
      current = false
    }
  }, [driverId, reportPath, refreshKey])

  async function leaveOut(claim: DashClaim, leftOut: boolean) {
    setClaims((all) => all.map((c) => (c.id === claim.id ? { ...c, left_out: leftOut } : c)))
    try {
      await setDashClaimLeftOut(driverId, claim.id, leftOut)
    } catch {
      setClaims((all) => all.map((c) => (c.id === claim.id ? { ...c, left_out: !leftOut } : c)))
      toast.error('Could not save that — try again')
    }
  }

  // Nothing until a report has been read — then always both sections, so
  // "no gaps" is said rather than left to be inferred from a missing block.
  if (!reportPath && refreshKey === 0 && gaps.length === 0 && claims.length === 0) return null

  return (
    <>
      <section className="cl-sec">
        <div className="cl-bar">
          <span>Insurance Gaps</span>
          <span className="cl-bar-note">last 3 years</span>
        </div>
        {gaps.length === 0 ? (
          <div className="cl-empty">No gaps found.</div>
        ) : (
          <table className="cl-table cl-history">
            <thead>
              <tr>
                <th>From</th>
                <th>To</th>
                <th className="cl-num">Days</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {gaps.map((gap) => (
                <tr key={gap.gap_from} data-past-window={gap.past_window ? '' : undefined}>
                  <Cell copy={rawDate(gap.gap_from)} label="gap start">{gap.gap_from}</Cell>
                  <Cell copy={rawDate(gap.gap_to)} label="gap end">{gap.gap_to}</Cell>
                  <Cell className="cl-num" copy={String(gap.days)} label="days">{gap.days}</Cell>
                  <Cell
                    className={gap.reason === 'Non-payment' ? 'cl-b cl-red' : 'cl-b'}
                    copy={reasonLabel(gap.reason)}
                    label="reason"
                  >
                    {reasonLabel(gap.reason)}
                  </Cell>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="cl-sec">
        <div className="cl-bar">
          <span>Claims</span>
          <span className="cl-bar-note">last 8 years · Loss + Expense</span>
        </div>
        {claims.length === 0 ? (
          <div className="cl-empty">No claims in the last 8 years.</div>
        ) : (
          <div className="cl-scroll">
            <table className="cl-table cl-claims cl-history">
              <thead>
                <tr>
                  <th>Date of loss</th>
                  <th>Driver</th>
                  <th>At-Fault</th>
                  <th className="cl-num">TP/BI</th>
                  <th className="cl-num">TP/PD</th>
                  <th className="cl-num">AB</th>
                  <th className="cl-num">Coll</th>
                </tr>
              </thead>
              <tbody>
                {claims.map((claim) => (
                  <ClaimRows key={claim.id} claim={claim} onLeaveOut={leaveOut} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  )
}

function ClaimRows({
  claim,
  onLeaveOut,
}: {
  claim: DashClaim
  onLeaveOut: (claim: DashClaim, leftOut: boolean) => void
}) {
  const notOnPolicy = claim.whose === 'not_on_policy'
  return (
    <>
      <tr
        data-left-out={claim.left_out ? '' : undefined}
        data-past-window={claim.past_window ? '' : undefined}
      >
        <Cell copy={rawDate(claim.date_of_loss)} label="date of loss">{claim.date_of_loss}</Cell>
        <Cell className={notOnPolicy ? 'cl-b' : undefined} copy={whose(claim)} label="driver">
          {whose(claim)}
        </Cell>
        <Cell
          className={Number(claim.at_fault_percent) > 0 ? 'cl-b cl-red' : undefined}
          copy={claim.at_fault_percent === null ? null : String(Number(claim.at_fault_percent))}
          label="at-fault"
        >
          {fault(claim)}
        </Cell>
        <AmountCell value={claim.tp_bi} label="TP/BI" />
        <AmountCell value={claim.tp_pd} label="TP/PD" />
        <AmountCell value={claim.ab} label="AB" />
        <AmountCell value={claim.coll} label="Coll" />
      </tr>
      {notOnPolicy && !claim.past_window && (
        <tr className="cl-claim-note">
          <td />
          <td colSpan={6} className="cl-wrap">
            {claim.left_out ? (
              <>
                Left out — this driver has their own insurance.{' '}
                <button type="button" className="cl-link" onClick={() => onLeaveOut(claim, false)}>
                  Undo
                </button>
              </>
            ) : (
              <>
                ⓘ This driver is not on the policy. Ask the customer if they have their own
                insurance. If yes, leave this claim out.{' '}
                <button type="button" className="cl-link" onClick={() => onLeaveOut(claim, true)}>
                  Leave out
                </button>
              </>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

/** A table cell with the hover copy button. `copy` is already raw (copy.ts). */
function Cell({
  children,
  copy,
  label,
  className,
}: {
  children: React.ReactNode
  copy: string | null
  label: string
  className?: string
}) {
  return (
    <td className={className}>
      {children}
      {copy && <CopyButton value={copy} label={label} />}
    </td>
  )
}

/** An amount: shown with thousands separators, copied (button or Ctrl+C) as
 *  plain digits. */
function AmountCell({ value, label }: { value: number; label: string }) {
  const n = Math.round(Number(value))
  return (
    <td className="cl-num" data-copy="digits">
      {money(value)}
      {n > 0 && <CopyButton value={String(n)} label={label} />}
    </td>
  )
}
