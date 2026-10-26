import { useEffect, useRef, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'

// Asked once per Meta lead, on the way into In Progress (and into Quote Done
// and beyond, for a lead that skipped it).
//
// FOUR QUESTIONS, ONE PER SCREEN. It was a single yes/no until the signal moved
// earlier in the funnel: In Progress now fires Meta's CompleteRegistration, so
// the facts that describe the lead have to be collected at the same moment the
// judgement is made. Stepped rather than one long form — a broker is usually
// mid-call, and one question at a time is answerable in the gaps.
//
// THE THREE OUTCOMES ARE UNCHANGED, and they are the reason this is not
// useConfirm (which resolves a boolean):
//
//   Qualified      -> true.  Meta hears about this lead.
//   Not qualified  -> false. Meta hears nothing about it, ever. NOT a cancel:
//                     the stage move still goes through, because a judgement
//                     about Meta is not a reason to refuse a broker their own
//                     pipeline.
//   Skip           -> writes NOTHING, and abandons the move. The answers stay
//                     null, so the questions return next time.
//
// WHAT IS NOT ASKED HERE:
//
//   The PREMIUM. It never blocks this transition — if an annual premium is
//   already recorded when the signal is prepared it becomes the Meta event
//   value, and if not the event is sent without one. Nothing to ask, so no
//   question and no Skip for it.
//
//   The FIVE-YEAR INSURANCE RULE. A lead insured less than five years is
//   recorded not-qualified automatically and this dialog never opens at all, so
//   it is not a step.

/** What one step just answered. Sent as it is tapped, not collected up and sent
 *  at the end — a mis-click closing the dialog must not cost the answers already
 *  given, and reopening shows the broker their own work.
 *
 *  EXACTLY ONE KEY IS SET PER CALL. Each maps to its own endpoint, and a partial
 *  write is what stops step 3 blanking what step 2 recorded.
 *
 *  POLICY TYPE IS NOT HERE, deliberately. It has a control of its own on the New
 *  Policy page, beside the stage dropdown — the screen the broker is already
 *  looking at. Asking again made it the only answer collected in two places, and
 *  a second copy of a question is a second chance for the two to disagree. It is
 *  still REQUIRED to reach In Progress; it is just required there.
 *
 *  NEITHER IS THE PREMIUM. It is entered on New Policy with all six product
 *  fields and the year/month toggle. Nothing is lost: most leads have no quote
 *  at In Progress — which is the whole reason the signal fires there — and the
 *  Meta event reads total_new_premium_annual at send time wherever it came
 *  from. */
export type QualifyAnswer =
  | { convictions3y: number }
  | { licensed5yPlus: boolean }
  | { isQualified: boolean }

const TOTAL_STEPS = 3

interface Props {
  /** The lead being judged, or null when the dialog is closed. */
  leadName: string | null
  /** What this lead already has recorded, so reopening shows the broker their
   *  own answers instead of a blank form. Null means unanswered — which is NOT
   *  zero, and is why these are nullable rather than defaulted. */
  savedConvictions3y?: number | null
  savedLicensed5yPlus?: boolean | null
  /** True while a step's save is in flight. The step does not advance until it
   *  succeeds. */
  busy?: boolean
  /** Fires once per step, with only that step's answer.
   *
   *  MUST REJECT ON FAILURE. The dialog awaits it and keeps the step where it is
   *  when it throws, so a broker is never advanced past an answer that was not
   *  saved. */
  onAnswer: (answer: QualifyAnswer) => Promise<void>
  /** Escape, the overlay, or closing the dialog. Writes nothing further — the
   *  answers already saved stay saved. */
  onSkip: () => void
}

/** One row of count buttons — used by both number steps.
 *
 *  SHARED RATHER THAN DUPLICATED because the two rows differ only in their
 *  choices: two copies of this markup would drift the moment one row gained a
 *  size or spacing tweak, and they must stay visually identical or the second
 *  step looks like a different kind of question.
 *
 *  aria-pressed rather than a radio group: these are buttons that record a
 *  value, and a screen reader should hear which one is currently chosen. */
function ChoiceRow({
  choices,
  value,
  onPick,
  disabled,
}: {
  choices: readonly number[]
  value: number | null
  onPick: (n: number) => void
  disabled?: boolean
}) {
  return (
    // Wraps rather than scrolls — six buttons fit one line on a desktop dialog
    // and two lines at phone width, and a row that scrolls sideways hides
    // choices a broker cannot see to look for.
    <div className="flex flex-wrap gap-2">
      {choices.map((n) => {
        const selected = value === n
        return (
          <Button
            key={n}
            type="button"
            variant={selected ? 'default' : 'outline'}
            disabled={disabled}
            aria-pressed={selected}
            onClick={() => onPick(n)}
            // Square-ish and equal width, so the row reads as a scale rather
            // than as buttons of different importance. tabular-nums keeps the
            // digits from shifting width between 1 and 4.
            className="h-10 w-12 tabular-nums"
          >
            {choiceLabel(n)}
          </Button>
        )
      })}
    </div>
  )
}

// BUTTONS, NOT A NUMBER BOX. The answer is a single digit in almost every real
// case, so a tap beats a keyboard — especially on a phone mid-call, where the
// number pad covers half the dialog.
//
// STARTS AT 0, because zero is the overwhelmingly common answer and the one a
// broker needs to record fastest, so it must be the easiest to reach.
//
// 5+ IS STORED AS 5. Nothing downstream distinguishes five convictions from
// nine — the extra precision would be typing nobody reads.
//
// A VEHICLE_CHOICES row sat beside this until the vehicles question was
// removed: nothing read the count, and the multi-car dataset it was collected
// for was considered and rejected.
const CONVICTION_CHOICES = [0, 1, 2, 3, 4, 5] as const

/** The last choice in each row means "this many or more". */
const PLUS_AT = 5

function choiceLabel(n: number): string {
  return n >= PLUS_AT ? `${n}+` : String(n)
}

export default function QualifyLeadDialog({
  leadName,
  savedConvictions3y = null,
  savedLicensed5yPlus = null,
  busy = false,
  onAnswer,
  onSkip,
}: Props) {
  const open = leadName !== null

  const [step, setStep] = useState(1)

  // THE PENDING AUTO-ADVANCE, held so it can be cancelled. Without this a timer
  // set by a tap outlives whatever the broker does next: tapping a number and
  // hitting Back inside the delay pushes them forward again over the top of
  // their own correction, and closing the dialog in that window leaves a
  // setStep to run against an unmounted component.
  const advanceTimer = useRef<number | null>(null)
  // null means UNANSWERED, which is not the same as 0 — a broker who has not
  // answered must not be recorded as having said "none". With buttons the
  // distinction is structural rather than a parsing problem: nothing is
  // selected until something is tapped.
  const [convictions, setConvictions] = useState<number | null>(null)
  const [licensed, setLicensed] = useState<boolean | null>(null)

  // Fresh every time it opens. Without this, a broker who skips one lead and
  // qualifies a different one an hour later inherits the previous answers —
  // the kind of wrong that looks deliberate, and here it would be written to
  // the wrong customer's record.
  // PREFILLED FROM WHAT IS ALREADY SAVED, which is the point of saving each tap:
  // a broker who answered two questions and lost the dialog should not be asked
  // them again. Null stays null — unanswered is not zero, and pre-selecting 0
  // would be indistinguishable from an answer they gave.
  useEffect(() => {
    if (open) {
      setStep(1)
      setConvictions(savedConvictions3y)
      setLicensed(savedLicensed5yPlus)
    }
  }, [open, savedConvictions3y, savedLicensed5yPlus])

  // Drop any pending advance when the dialog closes or unmounts, so a tap made
  // a moment before Skip cannot move a step that is no longer on screen.
  useEffect(() => {
    if (!open && advanceTimer.current !== null) {
      window.clearTimeout(advanceTimer.current)
      advanceTimer.current = null
    }
    return () => {
      if (advanceTimer.current !== null) window.clearTimeout(advanceTimer.current)
    }
  }, [open])

  // Nothing advances on a Next any more — every step either taps to its answer
  // or, on step 1, answers with one of two buttons. Advancing is done by `save`
  // once the write has actually succeeded.

  /** Move on after a tap.
   *
   *  AUTO-ADVANCING IS ONLY RIGHT FOR THE TAP STEPS. Picking a number is a
   *  complete answer the moment it happens — there is nothing to adjust, so a
   *  Next button afterwards is a second click asking the broker to confirm what
   *  they just said. The premium step keeps its Next because typing a figure is
   *  not finished until the broker says so.
   *
   *  A SMALL DELAY, so the tap is visibly registered before the screen changes.
   *  Advancing on the same frame means the chosen button never renders as
   *  selected, and the step appears to skip itself — the broker cannot tell
   *  whether their answer landed, which on a four-question dialog is the
   *  difference between confidence and re-checking.
   *
   *  RE-TAPPING IS SAFE: this only ever moves forward one step, and a broker who
   *  taps twice quickly gets the same single advance.
   */
  /** Record the tap, save it, and only then move on.
   *
   *  THE ORDER MATTERS. The selection is shown immediately so the button paints
   *  as chosen — but the step advances ONLY after the write succeeds. Advancing
   *  first would tell a broker their answer was recorded at the exact moment it
   *  was not, and they would have no way of knowing.
   *
   *  On failure the choice is rolled back and the step stays put; the caller has
   *  already shown the error, so nothing is said twice.
   */
  async function save(answer: QualifyAnswer, apply: () => void) {
    apply()
    try {
      await onAnswer(answer)
      // NOT QUALIFIED ENDS THE DIALOG. Meta is told nothing about this lead, so
      // the remaining questions would be asked for nothing. Advancing would be
      // asking a broker to do work that is thrown away.
      //
      // The caller closes the dialog and resumes the stage move; nothing further
      // happens here.
      if ('isQualified' in answer && !answer.isQualified) return
      advance()
    } catch {
      // Put the buttons back to unanswered, so the broker can see there is
      // something still to do rather than a selection that did not stick.
      if ('convictions3y' in answer) setConvictions(savedConvictions3y)
      else if ('licensed5yPlus' in answer) setLicensed(savedLicensed5yPlus)
    }
  }

  function advance() {
    // CANCEL ANY PENDING ONE FIRST. Two taps in quick succession would otherwise
    // queue two advances and skip a question entirely.
    if (advanceTimer.current !== null) window.clearTimeout(advanceTimer.current)
    // 320ms, not 140. The shorter delay was long enough for the tapped button to
    // paint as selected, but not long enough for the eye to register that a
    // TRANSITION happened — the dialog is the same size with the same footer, so
    // an instant content swap reads as the same screen with different words
    // rather than as a step being completed.
    //
    // The pause is doing two jobs: showing the answer land, and separating one
    // question from the next so they are not perceived as one flicker.
    advanceTimer.current = window.setTimeout(() => {
      advanceTimer.current = null
      setStep((s) => Math.min(s + 1, TOTAL_STEPS))
    }, 320)
  }

  return (
    // Escape and the overlay click both mean Skip, so they route through it
    // rather than leaving the caller believing a move is still in progress.
    <Dialog open={open} onOpenChange={(next) => !next && !busy && onSkip()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <div className="flex items-start justify-between gap-3">
            {/* The name, because a broker with several leads open needs to know
                which one they are judging. */}
            <DialogTitle>Qualify {leadName}</DialogTitle>
            {/* FOUR DOTS, so four steps are visibly four. A broker who cannot
                see how much is left assumes the worst and skips. */}
            <div className="mt-1.5 flex shrink-0 items-center gap-1.5" aria-hidden>
              {Array.from({ length: TOTAL_STEPS }, (_, i) => (
                <span
                  key={i}
                  className={cn(
                    'h-1.5 w-1.5 rounded-full',
                    i + 1 === step
                      ? 'bg-primary'
                      : i + 1 < step
                        ? 'bg-muted-foreground/60'
                        : 'bg-muted',
                  )}
                />
              ))}
            </div>
          </div>
          {/* WHY, NOT JUST WHAT. These four answers are what Meta learns from —
              they are the difference between the ads finding more people like
              this one and finding more form-fillers. A broker who reads this as
              admin will skip it or answer carelessly, and a carelessly answered
              question is worse than an unanswered one: it teaches the model the
              wrong thing rather than nothing.

              Said once, at the top, rather than repeated per step. */}
          <DialogDescription>
            Quick answers here help us get you better leads.
            {step < TOTAL_STEPS && (
              <span className="text-muted-foreground/70">
                {' '}
                · Step {step} of {TOTAL_STEPS}
              </span>
            )}
          </DialogDescription>
        </DialogHeader>

        {/* KEYED ON THE STEP, which is what makes the animation fire. React
            reuses this subtree across steps otherwise — same element type, same
            position — so the class is already applied and the browser has no
            reason to replay it. A changing key forces a real remount, and the
            slide-in plays on every step.

            The movement is the point: it says "you have moved" in a way that
            changed text alone cannot, and it is what stops a fast tap sequence
            reading as one flickering screen. */}
        <div
          key={step}
          className="min-h-28 animate-in fade-in slide-in-from-right-4 py-1 duration-200"
        >
          {/* THE JUDGEMENT FIRST, because it can end the conversation.

              It used to be last, which meant a broker rejecting a lead had
              already tapped through convictions and vehicles — and both were
              SAVED. That work bought nothing: an unqualified lead sends Meta
              nothing at all.

              Asking the cheap question that can stop the flow before the two
              that only matter if it continues. */}
          {step === 1 && (
            <div className="space-y-3">
              <Label className="text-sm font-normal">
                Is {leadName} a qualified lead?
              </Label>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  className="flex-1"
                  disabled={busy}
                  onClick={() => void save({ isQualified: false }, () => {})}
                >
                  Not qualified
                </Button>
                <Button
                  className="flex-1"
                  disabled={busy}
                  onClick={() => void save({ isQualified: true }, () => {})}
                >
                  Qualified
                </Button>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-2">
              <Label className="text-sm font-normal">
                How many convictions in the last 3 years?
              </Label>
              <ChoiceRow
                choices={CONVICTION_CHOICES}
                value={convictions}
                onPick={(n) => void save({ convictions3y: n }, () => setConvictions(n))}
                disabled={busy}
              />
            </div>
          )}

          {step === TOTAL_STEPS && (
            <div className="space-y-3">
              <Label className="text-sm font-normal">
                Has {leadName} been licensed more than 5 years?
              </Label>
              {/* YES/NO RATHER THAN A COUNT. The question is a threshold, so
                  the exact number changes no decision — and one tap beats
                  choosing from a row of digits for an answer with two outcomes.

                  Selected state shows on the chosen side, because this step
                  prefills from what was already saved and a broker reopening the
                  dialog needs to see their own answer. */}
              <div className="flex gap-2">
                <Button
                  variant={licensed === false ? 'default' : 'outline'}
                  className="flex-1"
                  disabled={busy}
                  aria-pressed={licensed === false}
                  onClick={() => void save({ licensed5yPlus: false }, () => setLicensed(false))}
                >
                  No
                </Button>
                <Button
                  variant={licensed === true ? 'default' : 'outline'}
                  className="flex-1"
                  disabled={busy}
                  aria-pressed={licensed === true}
                  onClick={() => void save({ licensed5yPlus: true }, () => setLicensed(true))}
                >
                  Yes
                </Button>
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="gap-2 sm:justify-between">
          {/* NO SKIP BUTTON. It existed only for the premium, which is no longer
              asked here — the broker enters that on New Policy, where all six
              product fields and the year/month toggle live. Every remaining
              question is required.

              Escape and the overlay still close the dialog, and whatever was
              already saved stays saved, so nobody is trapped.

              An empty span keeps the footer's space-between layout, so Back and
              Next stay on the right. */}
          <span />
          <div className="flex gap-2">
            {/* BACK KEEPS THE ANSWERS. A mistyped conviction count should cost
                one click, not the whole flow. Absent on step 1, where there is
                nothing behind it. */}
            {step > 1 && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => {
                  // Kill a pending auto-advance, or it fires straight after this
                  // and undoes the Back the broker just pressed.
                  if (advanceTimer.current !== null) {
                    window.clearTimeout(advanceTimer.current)
                    advanceTimer.current = null
                  }
                  setStep((s) => s - 1)
                }}
              >
                Back
              </Button>
            )}
            {/* NO NEXT ANYWHERE, and each step has its own reason.

                Step 1 is the judgement: its two buttons ARE the answer, so a
                Next beside them would be a third button meaning the same as one
                of them.

                Steps 2 and 3 are taps that advance themselves once the write
                succeeds (see save) — a Next there would ask the broker to
                confirm what they just said.

                Back remains, because correcting an answer is a different act
                from giving one. */}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
