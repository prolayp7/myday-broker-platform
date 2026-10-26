// The REAL backend lead-stage taxonomy — the 7 values in the lead.stage CHECK
// constraint (backend migration 0051, which cut the original 8 down and renamed
// three). This is the single source of truth for the broker Leads screen and
// anything that reads real lead data.
//
// Deliberately separate from domain/stages.ts, which is the OLDER 11-stage mock
// taxonomy (new/called/quoted/…) still used by My Day and the shared
// StageBadge/CountTile until those are rebuilt. Do not merge the two: the mock
// includes Called/Contacted/Follow Up, which are a future contact-status axis,
// NOT real pipeline stages (see the stage-vs-contact decision).
//
// `state` maps to the app's semantic palette (index.css) for chip/rail color.

export type LeadStageState = 'info' | 'warning' | 'success' | 'error' | 'neutral'

export interface LeadStage {
  key: string // the exact backend value, e.g. 'Quote Done'
  label: string // display text (same as key here — backend names are already human)
  state: LeadStageState
}

// Order = pipeline order, used left-to-right / top-to-bottom in the rail.
export const LEAD_STAGES: LeadStage[] = [
  { key: 'New', label: 'New', state: 'info' },
  // A REAL STAGE SINCE 0115, not a label inside New. It carries the
  // qualification judgement and — from 0116 — Meta's CompleteRegistration, which
  // is more than a nullable column could answer for.
  { key: 'In Progress', label: 'In Progress', state: 'info' },
  { key: 'Quote Done', label: 'Quote Done', state: 'info' },
  { key: 'Pending', label: 'Pending', state: 'warning' },
  { key: 'Sold', label: 'Sold', state: 'success' },
  { key: 'Submitted', label: 'Submitted', state: 'success' },
  { key: 'Issued', label: 'Issued', state: 'success' },
  { key: 'Lost', label: 'Lost', state: 'error' },
]

// A finer label INSIDE a stage (backend migration 0092), never a stage of its
// own. The rail still shows exactly the seven above.
//
// Kept HERE, beside the real taxonomy, and deliberately NOT in domain/stages.ts
// — that file is the older 11-stage mock still feeding My Day and the shared
// StageBadge, and putting these there would spread them into screens that do
// not read real lead data.
//
// The pairing is the same one services/lead.py::update_stage enforces. A stage
// missing from this map takes no sub-stage at all, which is most of them.
export const SUB_STAGES: Record<string, readonly string[]> = {
  // New: ['In Progress'] was here until 0115 promoted it. Junk is the only
  // label left — mirrors _SUB_STAGES_FOR and lead_sub_stage_check, both of
  // which now accept Junk alone.
  Lost: ['Junk'],
}

/** The labels this stage can carry — empty for the five that carry none. */
export function subStagesFor(stage: string): readonly string[] {
  return SUB_STAGES[stage] ?? []
}

// ONE dropdown value covering both columns, because Radix Select suppresses
// onValueChange when the picked value equals the current one. A New lead
// re-picking "New" fires nothing, so "New · In Progress" cannot be a state the
// same value reaches — it has to be a value of its own.
//
// Only the pair is encoded; the stage alone stays its own bare key, so every
// existing value ('New', 'Sold', …) still round-trips unchanged and a lead with
// no sub-stage is described exactly as it was before this existed.
const PAIR = ' · '

/** The dropdown value for a stage and its label, e.g. 'New · In Progress'. */
export function stageValue(stage: string, subStage?: string | null): string {
  return subStage ? `${stage}${PAIR}${subStage}` : stage
}

/** Back to the two columns. A bare stage yields a null sub-stage. */
export function parseStageValue(value: string): { stage: string; subStage: string | null } {
  const at = value.indexOf(PAIR)
  if (at === -1) return { stage: value, subStage: null }
  return { stage: value.slice(0, at), subStage: value.slice(at + PAIR.length) }
}

// What the RAIL shows: the seven stages, each followed by its labels as chips
// of their own. 'In Progress' sits right after 'New' and 'Junk' after 'Lost',
// so a broker reads them as stages — which is how they think about them —
// while the database keeps them as a column on an unchanged seven-stage
// pipeline (0092).
//
// A CHIP IS NOT ALWAYS A STAGE, so anything consuming this must not assume
// isLeadStage(). The backend translates the two label chips into a stage plus
// a sub_stage filter (services/lead.py::list_my_leads), and counts them the
// same way (0093), so a chip's number always matches the rows it opens.
export const RAIL_CHIPS: { key: string; label: string; isSubStage: boolean }[] =
  LEAD_STAGES.flatMap((s) => [
    { key: s.key, label: s.label, isSubStage: false },
    ...subStagesFor(s.key).map((sub) => ({ key: sub, label: sub, isSubStage: true })),
  ])

/** Which stage a label chip belongs to — the colour it should borrow, since a
 *  label has no palette entry of its own. Empty for a real stage. */
export function chipParentStage(key: string): string {
  for (const [stage, subs] of Object.entries(SUB_STAGES)) {
    if (subs.includes(key)) return stage
  }
  return ''
}

/** True when this rail key is a label rather than one of the seven stages. */
export function isSubStageChip(key: string): boolean {
  return chipParentStage(key) !== ''
}

/** Every option the stage dropdown offers: the seven, each followed by its
 *  labelled variants. Two extra rows in total, both under stages that already
 *  exist — the rail and the pipeline are untouched. */
export function stageOptions(): { value: string; label: string }[] {
  return LEAD_STAGES.flatMap((s) => [
    { value: s.key, label: s.label },
    // The LABEL is just the sub-stage — 'In Progress', not 'New · In
    // Progress'. It sits directly under its stage in the list, so the parent
    // is already obvious from position and repeating it only makes the row
    // longer. The VALUE keeps the full pair: that is the encoding
    // parseStageValue decodes back into two columns.
    ...subStagesFor(s.key).map((sub) => ({
      value: stageValue(s.key, sub),
      label: sub,
    })),
  ])
}

const BY_KEY: Record<string, LeadStage> = Object.fromEntries(
  LEAD_STAGES.map((s) => [s.key, s]),
)

export function leadStageState(key: string): LeadStageState {
  return BY_KEY[key]?.state ?? 'neutral'
}

export function isLeadStage(key: string): boolean {
  return key in BY_KEY
}

/**
 * True when a contact field holds a stand-in rather than a value.
 *
 * lead.phone and lead.email are NOT NULL, so a lead captured with only one of
 * them carries the literal string "(not provided)" in the other. A plain
 * `lead.phone &&` check passes that straight through and renders it as though
 * it were a phone number.
 *
 * Lives here rather than in one screen because every screen showing a lead's
 * contact details needs it — LeadList had it and LeadHeader did not, so the
 * same lead hid the placeholder in the list and displayed it in the title bar.
 */
export function isPlaceholderContact(v: string | null | undefined): boolean {
  if (!v) return true
  return /^\(?\s*(not provided|not available|n\/?a|unknown|none)\s*\)?$/i.test(v.trim())
}

