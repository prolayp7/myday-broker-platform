import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

// Asked on the way into Lost, because two very different things end up there.
//
// A LOST LEAD IS A REAL OPPORTUNITY THAT DID NOT CONVERT — someone who was
// quoted and went elsewhere. Junk never was one: spam, a duplicate, a test, a
// wrong number. Both have to leave the pipeline, and before this they left it
// through the same door, which made "how many did we actually lose" a question
// the data could not answer.
//
// SAME SHAPE AS QualifyLeadDialog, deliberately — it already solved
// interrupting a stage move to ask one question and then resuming it. Two
// answers and a cancel, rather than useConfirm's boolean.
//
//   Lost   -> stage Lost, no label. The ordinary case.
//   Junk   -> stage Lost, sub_stage 'Junk' (migration 0092).
//   Cancel -> abandons the move entirely; the lead stays where it was.
//
// JUNK IS NOT DESTRUCTIVE. It does not delete the lead, credit its cost,
// change who holds it, or send anything to Meta — the CAPI triggers key on
// `stage`, which is Lost either way, and Lost already signals nothing. It is a
// label, and that is all it is.

interface Props {
  /** The lead being closed, or null when the dialog is closed. */
  leadName: string | null
  busy?: boolean
  /** null = a genuine lost opportunity; 'Junk' = it was never real. */
  onAnswer: (subStage: 'Junk' | null) => void
  onCancel: () => void
}

export default function LostReasonDialog({
  leadName,
  busy = false,
  onAnswer,
  onCancel,
}: Props) {
  const open = leadName !== null

  return (
    // Escape and the overlay click both mean cancel, so they route through it
    // rather than leaving the caller believing a move is still in progress.
    <Dialog open={open} onOpenChange={(next) => !next && !busy && onCancel()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Why is {leadName} closing?</DialogTitle>
          <DialogDescription>
            Junk keeps spam and duplicates out of your lost-deal numbers.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2 sm:justify-between">
          <Button variant="ghost" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
          <div className="flex gap-2">
            {/* Junk is the secondary action: most closes are real losses, and
                the ordinary answer should be the easy one to hit. */}
            <Button variant="outline" disabled={busy} onClick={() => onAnswer('Junk')}>
              Junk
            </Button>
            <Button disabled={busy} onClick={() => onAnswer(null)}>
              Lost
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
