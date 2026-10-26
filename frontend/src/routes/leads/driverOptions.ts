// The relationships mirror migrations/0035_driver.sql exactly. 'Applicant' is
// absent by design: a lead has exactly one, created with the lead itself.
// 'NA' is what a DASH import writes when the report does not say; the paper
// shows it as "Not stated" and Add Driver never offers it.
export const RELATIONSHIPS = [
  'Spouse',
  'Common Law',
  'Same Sex Partner',
  'Co-habitant',
  'Child',
  'Sibling',
  'Parent',
  'NA',
] as const
