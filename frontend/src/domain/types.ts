import { z } from 'zod'

// ── Lead model (mirrors the UI5 `lead()` factory in 03-my-leads.html) ──
// Zod schema is the single source of truth: it validates the capture form
// AND its inferred type is used everywhere a Lead is passed around.

export const driverSchema = z.object({
  licenceNumber: z.string().default(''),
  name: z.string().default(''),
  relationship: z.string().default(''),
})

export const vehicleSchema = z.object({
  vin: z.string().default(''),
  ymm: z.string().default(''),
  condition: z.string().default(''),
  ownership: z.string().default(''),
  financingCo: z.string().default(''),
  purchaseDate: z.string().default(''),
  oneWayKm: z.string().default(''),
  annualKm: z.string().default(''),
  winterTires: z.string().default(''),
  coverageType: z.string().default(''),
  antiTheft: z.string().default(''),
  parkingNight: z.string().default(''),
  useType: z.string().default(''),
})

// One shared property fact-set — Homeowner, Condo and each Rented property
// all capture these same facts (see PROPERTY_SPEC).
export const propertySchema = z.object({
  type: z.string().default('rentedcondo'), // only used by rented entries
  insuredSince: z.string().default(''),
  insuredPropSince: z.string().default(''),
  structure: z.string().default(''),
  yearBuilt: z.string().default(''),
  stories: z.string().default(''),
  fullBaths: z.string().default(''),
  halfBaths: z.string().default(''),
  mortgages: z.string().default(''),
  basement: z.string().default(''),
  finishedPct: z.string().default(''),
  sumpType: z.string().default(''),
  elecYear: z.string().default(''),
  plumbYear: z.string().default(''),
  heatingYear: z.string().default(''),
  roofYear: z.string().default(''),
  waterHeaterYear: z.string().default(''),
})

export const leadSchema = z.object({
  id: z.string(),
  name: z.string().default(''),
  stage: z.string().default('new'),
  dob: z.string().default(''),
  address: z.string().default(''),
  phone: z.string().default(''),
  email: z.string().default(''),
  licenceYear: z.string().default(''),
  licenceNumber: z.string().default(''),
  attempts: z.number().default(0),
  lastContact: z.string().default(''),
  source: z.string().default('Auto insurance enquiry'),
  currentPremium: z.string().default(''),
  myPremium: z.string().default(''),
  effectiveDate: z.string().default(''),
  contInsurance: z.string().default(''),
  insurer: z.string().default(''),
  campaign: z.string().default('Campaign A'),
  received: z.string().default(''),
  dueToday: z.boolean().default(false),
  sentToMeta: z.boolean().default(false),

  // Applicant capture
  firstName: z.string().default(''),
  lastName: z.string().default(''),
  street: z.string().default(''),
  city: z.string().default(''),
  postal: z.string().default(''),

  drivers: z.array(driverSchema).default([]),
  vehicles: z.array(vehicleSchema).default([]),

  // Licence dates
  gDate: z.string().default(''),
  g2Date: z.string().default(''),
  g1Date: z.string().default(''),

  // Policy bundling: '' | 'none' | 'tenant' | 'homeowner' | 'condo'
  bundle: z.string().default(''),

  // Tenant (3 fields)
  tnInsuredSince: z.string().default(''),
  tnInsuredPropSince: z.string().default(''),
  tnStructure: z.string().default(''),

  // Homeowner primary property (flat ho-prefixed fields)
  hoInsuredSince: z.string().default(''),
  hoInsuredPropSince: z.string().default(''),
  hoStructure: z.string().default(''),
  hoYearBuilt: z.string().default(''),
  hoStories: z.string().default(''),
  hoFullBaths: z.string().default(''),
  hoHalfBaths: z.string().default(''),
  hoMortgages: z.string().default(''),
  hoBasement: z.string().default(''),
  hoFinishedPct: z.string().default(''),
  hoSumpType: z.string().default(''),
  hoElecYear: z.string().default(''),
  hoPlumbYear: z.string().default(''),
  hoHeatingYear: z.string().default(''),
  hoRoofYear: z.string().default(''),
  hoWaterHeaterYear: z.string().default(''),

  // Condo primary property (flat co-prefixed fields)
  coInsuredSince: z.string().default(''),
  coInsuredPropSince: z.string().default(''),
  coStructure: z.string().default(''),
  coYearBuilt: z.string().default(''),
  coStories: z.string().default(''),
  coFullBaths: z.string().default(''),
  coHalfBaths: z.string().default(''),
  coMortgages: z.string().default(''),
  coBasement: z.string().default(''),
  coFinishedPct: z.string().default(''),
  coSumpType: z.string().default(''),
  coElecYear: z.string().default(''),
  coPlumbYear: z.string().default(''),
  coHeatingYear: z.string().default(''),
  coRoofYear: z.string().default(''),
  coWaterHeaterYear: z.string().default(''),

  rentedProperties: z.array(propertySchema).default([]),
})

export type Driver = z.infer<typeof driverSchema>
export type Vehicle = z.infer<typeof vehicleSchema>
export type PropertyFacts = z.infer<typeof propertySchema>
export type Lead = z.infer<typeof leadSchema>

export function emptyVehicle(): Vehicle {
  return vehicleSchema.parse({})
}
export function emptyDriver(): Driver {
  return driverSchema.parse({})
}
export function emptyRentedProperty(type = 'rentedcondo'): PropertyFacts {
  return propertySchema.parse({ type })
}

let _seq = 0
// Factory mirroring the UI5 lead() default shape; assigns a stable id.
export function makeLead(overrides: Partial<Lead> & { name: string }): Lead {
  return leadSchema.parse({
    id: `lead-${++_seq}`,
    vehicles: [emptyVehicle()],
    ...overrides,
  })
}
