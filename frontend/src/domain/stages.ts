// Stage taxonomy — the single source of truth shared by My Day and My Leads.
// (In the UI5 wireframes this list was duplicated across both screens.)
// `state` maps to our semantic palette (see index.css) and drives chip/tile color.

export type StageState = 'success' | 'warning' | 'error' | 'info' | 'neutral'

export interface Stage {
  key: string
  label: string
  state: StageState
}

export const STAGES: Stage[] = [
  { key: 'new', label: 'New', state: 'info' },
  { key: 'called', label: 'Called', state: 'neutral' },
  { key: 'contacted', label: 'Contacted', state: 'neutral' },
  { key: 'followup', label: 'Follow Up', state: 'warning' },
  { key: 'quoted', label: 'Quoted', state: 'info' },
  { key: 'awaiting', label: 'Awaiting', state: 'warning' },
  { key: 'sold', label: 'Sold', state: 'success' },
  { key: 'submitted', label: 'Submitted', state: 'success' },
  { key: 'issued', label: 'Issued', state: 'success' },
  { key: 'notint', label: 'Not Interested', state: 'error' },
  { key: 'invalid', label: 'Invalid', state: 'error' },
]

const STAGE_BY_KEY: Record<string, Stage> = Object.fromEntries(
  STAGES.map((s) => [s.key, s]),
)

export function stageLabel(key: string): string {
  return STAGE_BY_KEY[key]?.label ?? key
}

export function stageState(key: string): StageState {
  return STAGE_BY_KEY[key]?.state ?? 'neutral'
}

// Active pipeline vs closed-off (dead) stages — used by My Day's two rows.
export const ACTIVE_KEYS = [
  'new',
  'called',
  'contacted',
  'followup',
  'quoted',
  'awaiting',
  'sold',
  'submitted',
  'issued',
]
export const DEAD_KEYS = ['notint', 'invalid']
