import { useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { DateField } from '@/components/DateField'

// Asked on the way into Issued, and the only way to get there.
//
// TWO THINGS AT ONCE, both of which the broker should see before a policy is
// recorded as written:
//
//   the PREMIUMS — read-only, straight off the row. Not editable here: this
//     is a last look before committing, and a figure changed in a dialog is a
//     figure changed without the context of the form it belongs to. If they
//     are wrong, cancel and fix them on the page.
//
//   the DATE — the day the CARRIER wrote the policy, which is not necessarily
//     today. It used to be today automatically, so a policy written on the
//     30th and recorded on the 2nd landed in the following month's figures
//     with nothing able to correct it. Defaults to today because that is
//     usually right, and is editable because it is not always.
//
// Cancel writes nothing and abandons the move — the same shape
// QualifyLeadDialog uses for Skip.

/** One premium line to show back. Annual, which is how premiums are stored
 *  since 0099 — a broker entering a monthly figure has it multiplied by
 *  twelve before it ever reaches the database. */
export interface IssuedPremiumLine {
  label: string
  annual: string
}

interface Props {
  /** The lead being issued, or null when the dialog is closed. */
  leadName: string | null
  premiums: IssuedPremiumLine[]
  /** The date already recorded, when this is a correction rather than a first
   *  issue. Null on the way in, so the field defaults to today. */
  currentIssuedAt?: string | null
  busy?: boolean
  onConfirm: (issuedAt: string) => void
  onCancel: () => void
}

/** Today as yyyy-mm-dd in LOCAL time — the broker's own day, not UTC's.
 *
 *  toISOString() would be wrong here: it converts to UTC first, so any evening
 *  after 8pm Ontario reports tomorrow's date and defaults the field to a day
 *  the backend then refuses as being in the future. */
function todayIso(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

export default function IssuedDialog({
  leadName,
  premiums,
  currentIssuedAt = null,
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  const open = leadName !== null
  const [issuedAt, setIssuedAt] = useState(todayIso)

  // Reset each time it opens. Without this a broker who cancels and issues a
  // different lead an hour later inherits the previous date, which is the kind
  // of wrong that looks deliberate.
  //
  // THE SAVED DATE WINS when there is one. Opening a correction on today's
  // date would quietly propose changing what is already recorded — the broker
  // came here to see the current value, not to overwrite it by pressing the
  // confirm button.
  useEffect(() => {
    if (open) setIssuedAt(currentIssuedAt || todayIso())
  }, [open, currentIssuedAt])

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent className="sm:max-w-sm">
        {/* Correcting a date already recorded is the same dialog doing a
            different job, and saying "Mark as issued" over a policy that
            already is reads as though it were about to happen again. */}
        <DialogHeader>
          <DialogTitle>
            {currentIssuedAt ? `Issued date for ${leadName}` : `Mark ${leadName} as issued`}
          </DialogTitle>
          <DialogDescription>
            Check the premiums and confirm the date the policy was issued.
          </DialogDescription>
        </DialogHeader>

        {premiums.length > 0 && (
          <div className="space-y-1.5">
            {premiums.map((line) => (
              <div
                key={line.label}
                className="flex items-center justify-between rounded-md border bg-secondary px-3 py-2 text-sm"
              >
                <span className="text-muted-foreground">{line.label}</span>
                <span className="font-medium">${line.annual} /yr</span>
              </div>
            ))}
          </div>
        )}

        <DateField
          value={issuedAt}
          onChange={setIssuedAt}
          label="Issued on"
          className="w-full"
        />

        <DialogFooter className="pt-2">
          <Button variant="outline" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          {/* Disabled on an empty date rather than letting the backend refuse
              it: the broker would get a 422 for something the screen already
              knows is missing. */}
          <Button onClick={() => onConfirm(issuedAt)} disabled={busy || !issuedAt}>
            {busy ? 'Saving…' : currentIssuedAt ? 'Save date' : 'Mark as issued'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
