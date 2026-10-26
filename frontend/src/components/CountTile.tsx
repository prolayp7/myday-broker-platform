import { cn } from '@/lib/utils'
import type { StageState } from '@/domain/stages'

// Compact tappable count tile (the SAP GenericTile equivalent). Big number +
// label; Fiori conveys state through the NUMBER color (valueColor), not a
// border or fill — so the tile stays calm and the count carries the signal.
const NUMBER_COLOR: Record<StageState, string> = {
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-error',
  info: 'text-info',
  neutral: 'text-foreground',
}

export function CountTile({
  title,
  subtitle,
  count,
  state = 'neutral',
  onClick,
}: {
  title: string
  subtitle?: string
  count: number
  state?: StageState
  onClick?: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'w-full min-w-36 rounded-md border bg-card p-3 text-left transition',
        'hover:border-ring/50 hover:shadow-card focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none',
      )}
    >
      <div
        className={cn(
          'text-2xl font-semibold tabular-nums',
          NUMBER_COLOR[state],
        )}
      >
        {count}
      </div>
      <div className="mt-0.5 truncate text-sm font-medium text-foreground">
        {title}
      </div>
      {subtitle && (
        <div className="truncate text-xs text-muted-foreground">{subtitle}</div>
      )}
    </button>
  )
}
