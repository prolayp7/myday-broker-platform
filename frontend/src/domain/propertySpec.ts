// Shared property fact-set spec (ported from PROPERTY_SPEC in 03-my-leads.html).
// Homeowner, Condo and each Rented property render these same rows, so the
// field set is defined ONCE and mapped onto each variant's binding path — no
// duplication / drift between the three.

export type PropControlType = 'date' | 'sel' | 'num' | 'text'

export interface SelectItem {
  k: string
  t: string
}

export interface PropRow {
  label: string
  key: string // short key: 'yearBuilt' → ho/co prefixed, or rented nested
  type: PropControlType
  items?: SelectItem[]
  placeholder?: string
}

const YN: SelectItem[] = [
  { k: 'yes', t: 'Yes' },
  { k: 'no', t: 'No' },
]
export const STRUCTURE_ITEMS: SelectItem[] = [
  { k: 'detached', t: 'Detached' },
  { k: 'semi', t: 'Semi-detached' },
  { k: 'townhouse', t: 'Townhouse' },
  { k: 'row', t: 'Row' },
]
const FINISHED_ITEMS: SelectItem[] = [
  { k: '0', t: '0%' },
  { k: '25', t: '25%' },
  { k: '50', t: '50%' },
  { k: '75', t: '75%' },
  { k: '100', t: '100%' },
]
const SUMP_ITEMS: SelectItem[] = [
  { k: 'none', t: 'None' },
  { k: 'pump', t: 'Sump pump' },
  { k: 'pumpbackup', t: 'Pump + backup' },
]

export const PROPERTY_SPEC: PropRow[] = [
  { label: 'Insured since', key: 'insuredSince', type: 'date' },
  { label: 'Insured with property since', key: 'insuredPropSince', type: 'date' },
  { label: 'Structure', key: 'structure', type: 'sel', items: STRUCTURE_ITEMS },
  { label: 'Year built', key: 'yearBuilt', type: 'num', placeholder: 'e.g. 1998' },
  { label: 'Stories', key: 'stories', type: 'num', placeholder: 'e.g. 2' },
  { label: 'Full baths', key: 'fullBaths', type: 'num', placeholder: 'e.g. 2' },
  { label: 'Half baths', key: 'halfBaths', type: 'num', placeholder: 'e.g. 1' },
  { label: 'No. of mortgages', key: 'mortgages', type: 'num', placeholder: 'e.g. 1' },
  { label: 'Basement', key: 'basement', type: 'sel', items: YN },
  { label: 'Basement finished %', key: 'finishedPct', type: 'sel', items: FINISHED_ITEMS },
  { label: 'Sump type', key: 'sumpType', type: 'sel', items: SUMP_ITEMS },
  { label: 'Elec year', key: 'elecYear', type: 'num', placeholder: 'e.g. 2010' },
  { label: 'Plumb year', key: 'plumbYear', type: 'num', placeholder: 'e.g. 2010' },
  { label: 'Heating year', key: 'heatingYear', type: 'num', placeholder: 'e.g. 2015' },
  { label: 'Roof year', key: 'roofYear', type: 'num', placeholder: 'e.g. 2018' },
  { label: 'Water heater year', key: 'waterHeaterYear', type: 'num', placeholder: 'e.g. 2019' },
]
