import { cn } from '@/lib/utils'
import { stageLabel, stageState, type StageState } from '@/domain/stages'

// Calm, soft-colored status text (the SAP ObjectStatus non-inverted look):
// semantic color on a tinted pill, never a heavy solid badge.
const STATE_CLASSES: Record<StageState, string> = {
  success: 'text-success bg-success-muted',
  warning: 'text-warning bg-warning-muted',
  error: 'text-error bg-error-muted',
  info: 'text-info bg-info-muted',
  neutral: 'text-muted-foreground bg-muted',
}

export function StageBadge({
  stage,
  className,
}: {
  stage: string
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap',
        STATE_CLASSES[stageState(stage)],
        className,
      )}
    >
      {stageLabel(stage)}
    </span>
  )
}
