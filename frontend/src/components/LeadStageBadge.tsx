import { cn } from '@/lib/utils'
import { leadStageState, type LeadStageState } from '@/domain/leadStages'

// Soft-colored status pill for the REAL 8 backend lead stages (the SAP
// ObjectStatus non-inverted look). Separate from the shared StageBadge, which
// is bound to the older 11-stage mock taxonomy still used by My Day.
const STATE_CLASSES: Record<LeadStageState, string> = {
  success: 'text-success bg-success-muted',
  warning: 'text-warning bg-warning-muted',
  error: 'text-error bg-error-muted',
  info: 'text-info bg-info-muted',
  neutral: 'text-muted-foreground bg-muted',
}

export function LeadStageBadge({
  stage,
  subStage,
  className,
}: {
  stage: string
  /** The label inside the stage, appended to the text. A SEPARATE PROP, not
   *  part of `stage`: the colour comes from leadStageState(stage), and a
   *  pre-joined 'Lost · Junk' matches no stage, so a Junk lead would silently
   *  lose its red and read as neutral. */
  subStage?: string | null
  className?: string
}) {
  return (
    <span
      className={cn(
        // text size is intentionally last-wins: callers in dense lists pass
        // text-2xs, everywhere else inherits the default text-xs.
        'inline-flex items-center rounded-full px-1.5 py-0.5 text-xs font-medium whitespace-nowrap',
        STATE_CLASSES[leadStageState(stage)],
        className,
      )}
    >
      {/* The label alone when there is one: each label belongs to exactly one
          stage, so 'In Progress' already says New and 'Junk' already says
          Lost. The colour still comes from the parent stage, which is why it
          stays a separate prop. */}
      {subStage || stage}
    </span>
  )
}
