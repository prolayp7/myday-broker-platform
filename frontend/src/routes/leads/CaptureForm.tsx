import { useEffect } from 'react'
import {
  useForm,
  useFieldArray,
  type Control,
  type UseFormWatch,
  type Path,
} from 'react-hook-form'
import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { useConfirm } from '@/components/ConfirmDialog'
import {
  PROPERTY_SPEC,
  STRUCTURE_ITEMS,
  type PropRow,
} from '@/domain/propertySpec'
import type { Lead } from '@/domain/types'
import { emptyDriver, emptyVehicle, emptyRentedProperty } from '@/domain/types'
import { TextField, SelectField, TwoOptionField } from './fields'

// The capture form (03-my-leads info panel). One RHF form per selected lead;
// grows as the lead matures. useFieldArray replaces the manual rebuild*()
// array-splicing; watch() drives progressive disclosure.

const OWNERSHIP = [
  { k: 'owned', t: 'Owned' },
  { k: 'financed', t: 'Financed' },
  { k: 'leased', t: 'Leased' },
]
const COVERAGE = [
  { k: 'full', t: 'Full coverage' },
  { k: 'thirdparty', t: 'Third party' },
  { k: 'liability', t: 'Liability only' },
]
const PARKING = [
  { k: 'garage', t: 'Garage' },
  { k: 'driveway', t: 'Driveway' },
  { k: 'street', t: 'Street' },
  { k: 'lot', t: 'Parking lot' },
]
const USE_TYPE = [
  { k: 'personal', t: 'Personal' },
  { k: 'commute', t: 'Commute' },
  { k: 'business', t: 'Business' },
  { k: 'pleasure', t: 'Pleasure' },
]
const RELATIONSHIP = [
  { k: 'spouse', t: 'Spouse / Partner' },
  { k: 'child', t: 'Child' },
  { k: 'parent', t: 'Parent' },
  { k: 'sibling', t: 'Sibling' },
  { k: 'other', t: 'Other' },
]
const BUNDLE = [
  { k: 'none', t: 'No bundle (auto only)' },
  { k: 'tenant', t: 'Tenant' },
  { k: 'homeowner', t: 'Homeowner' },
  { k: 'condo', t: 'Condo' },
]
const TENANT_STRUCTURE = [
  { k: 'apartment', t: 'Apartment' },
  { k: 'house', t: 'House' },
  { k: 'condo', t: 'Condo unit' },
  { k: 'basement', t: 'Basement' },
  { k: 'townhouse', t: 'Townhouse' },
]

// property fields for a variant, given a prefix builder over the row key
function renderPropertyFields(
  control: Control<Lead>,
  pathFor: (key: string) => Path<Lead>,
) {
  return PROPERTY_SPEC.map((row: PropRow) => {
    const name = pathFor(row.key)
    if (row.type === 'date')
      return <TextField key={row.key} control={control} name={name} label={row.label} type="date" />
    if (row.type === 'sel')
      return <SelectField key={row.key} control={control} name={name} label={row.label} items={row.items ?? []} />
    return (
      <TextField
        key={row.key}
        control={control}
        name={name}
        label={row.label}
        type={row.type === 'num' ? 'number' : 'text'}
        placeholder={row.placeholder}
      />
    )
  })
}

// homeowner/condo flat path: key 'yearBuilt' + prefix 'ho' → 'hoYearBuilt'
function flatPath(prefix: string) {
  return (key: string) =>
    `${prefix}${key.charAt(0).toUpperCase()}${key.slice(1)}` as Path<Lead>
}

// list of ho/co/tn field names for the "has data?" check on bundle switch
const HO_FIELDS = PROPERTY_SPEC.map((r) => flatPath('ho')(r.key))
const CO_FIELDS = PROPERTY_SPEC.map((r) => flatPath('co')(r.key))
const TN_FIELDS: Path<Lead>[] = ['tnInsuredSince', 'tnInsuredPropSince', 'tnStructure']

function Section({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="space-y-3">
      <h3 className="border-b pb-1 text-sm font-semibold">{title}</h3>
      <div className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        {children}
      </div>
    </section>
  )
}

export function CaptureForm({
  lead,
  onChange,
}: {
  lead: Lead
  onChange: (lead: Lead) => void
}) {
  const confirm = useConfirm()
  // defaultValues (not values): the parent remounts this via key={lead.id} on
  // lead switch, so we initialize once per lead and never feed parent updates
  // back in — that would fight in-progress edits / field-array state.
  const form = useForm<Lead>({ defaultValues: lead })
  const { control, watch, getValues, setValue } = form

  // push edits up to the parent so the list stays in sync as the broker types
  useEffect(() => {
    const sub = watch((value) => onChange(value as Lead))
    return () => sub.unsubscribe()
  }, [watch, onChange])

  const drivers = useFieldArray({ control, name: 'drivers' })
  const vehicles = useFieldArray({ control, name: 'vehicles' })
  const rented = useFieldArray({ control, name: 'rentedProperties' })

  const bundle = watch('bundle')

  // Property-type switch: warn before clearing entered property data.
  const onBundleChange = async (newKey: string) => {
    const prevKey = getValues('bundle')
    if (newKey === prevKey) return

    const hasData =
      prevKey === 'homeowner'
        ? HO_FIELDS.some((f) => !!getValues(f))
        : prevKey === 'condo'
          ? CO_FIELDS.some((f) => !!getValues(f))
          : prevKey === 'tenant'
            ? TN_FIELDS.some((f) => !!getValues(f))
            : (getValues('rentedProperties') ?? []).length > 0

    const doSwitch = () => {
      setValue('bundle', newKey)
      setValue('rentedProperties', [])
      ;[...HO_FIELDS, ...CO_FIELDS, ...TN_FIELDS].forEach((f) =>
        setValue(f, '' as never),
      )
    }

    if (!hasData) return doSwitch()

    const ok = await confirm({
      title: 'Clear property data?',
      description:
        'Switching property type will clear all property information entered so far. Continue?',
    })
    if (ok) doSwitch()
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      {/* Applicant */}
      <Section title="Applicant">
        <TextField control={control} name="firstName" label="First name" placeholder="First name" required />
        <TextField control={control} name="lastName" label="Last name" placeholder="Last name" required />
        <TextField control={control} name="dob" label="Date of birth" type="date" />
        <TextField control={control} name="city" label="City" placeholder="City" />
        <TextField control={control} name="phone" label="Mobile" type="tel" placeholder="+1 ___-___-____" />
        <TextField control={control} name="email" label="Email" type="email" placeholder="name@email.com" />
        <TextField control={control} name="street" label="Street address" placeholder="Street address" />
        <TextField control={control} name="postal" label="Postal code" placeholder="A1A 1A1" />
      </Section>

      {/* Additional drivers */}
      <section className="space-y-3">
        {drivers.fields.map((f, i) => (
          <div key={f.id} className="rounded-lg border p-4">
            <div className="mb-3 flex items-center justify-between">
              <h4 className="text-sm font-semibold">Additional driver {i + 1}</h4>
              <Button variant="ghost" size="icon" onClick={() => drivers.remove(i)}>
                <Trash2 />
              </Button>
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
              <TextField control={control} name={`drivers.${i}.licenceNumber`} label="Licence number" placeholder="e.g. P1234-56789" />
              <TextField control={control} name={`drivers.${i}.name`} label="Full name" placeholder="Full name" />
              <SelectField control={control} name={`drivers.${i}.relationship`} label="Relationship" items={RELATIONSHIP} />
            </div>
          </div>
        ))}
        <Button variant="ghost" size="sm" onClick={() => drivers.append(emptyDriver())}>
          <Plus /> Add driver
        </Button>
      </section>

      {/* Vehicles */}
      <section className="space-y-3">
        {vehicles.fields.map((f, i) => (
          <VehiclePanel
            key={f.id}
            control={control}
            watch={watch}
            index={i}
            onRemove={vehicles.fields.length > 1 ? () => vehicles.remove(i) : undefined}
          />
        ))}
        <Button variant="ghost" size="sm" onClick={() => vehicles.append(emptyVehicle())}>
          <Plus /> Add vehicle
        </Button>
      </section>

      {/* Licence dates */}
      <Section title="Licence dates">
        <TextField control={control} name="gDate" label="G date" type="date" />
        <TextField control={control} name="g2Date" label="G2 date" type="date" />
        <TextField control={control} name="g1Date" label="G1 date" type="date" />
      </Section>

      {/* Policy bundling — the change is gated so we can confirm before
          clearing any property data the broker already entered. */}
      <Section title="Policy bundling">
        <SelectField
          control={control}
          name="bundle"
          label="Property type"
          items={BUNDLE}
          onChangeIntercept={onBundleChange}
        />
      </Section>

      {/* Tenant */}
      {bundle === 'tenant' && (
        <Section title="Tenant">
          <TextField control={control} name="tnInsuredSince" label="Insured since" type="date" />
          <TextField control={control} name="tnInsuredPropSince" label="Insured with property since" type="date" />
          <SelectField control={control} name="tnStructure" label="Structure" items={TENANT_STRUCTURE} />
        </Section>
      )}

      {/* Homeowner */}
      {bundle === 'homeowner' && (
        <Section title="Homeowner — Primary Property">
          {renderPropertyFields(control, flatPath('ho'))}
        </Section>
      )}

      {/* Condo */}
      {bundle === 'condo' && (
        <Section title="Condo — Primary Property">
          {renderPropertyFields(control, flatPath('co'))}
        </Section>
      )}

      {/* Rented properties — Homeowner/Condo only */}
      {(bundle === 'homeowner' || bundle === 'condo') && (
        <section className="space-y-3">
          <h3 className="border-b pb-1 text-sm font-semibold">Rented properties</h3>
          {rented.fields.map((f, i) => {
            const type = watch(`rentedProperties.${i}.type`)
            return (
              <div key={f.id} className="rounded-lg border p-4">
                <div className="mb-3 flex items-center justify-between">
                  <h4 className="text-sm font-semibold">
                    {type === 'renteddwelling' ? 'Rented Dwelling' : 'Rented Condo'}
                  </h4>
                  <Button variant="ghost" size="icon" onClick={() => rented.remove(i)}>
                    <Trash2 />
                  </Button>
                </div>
                <div className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
                  {renderPropertyFields(
                    control,
                    (key) => `rentedProperties.${i}.${key}` as Path<Lead>,
                  )}
                </div>
              </div>
            )
          })}
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={() => rented.append(emptyRentedProperty('rentedcondo'))}>
              <Plus /> Rented Condo
            </Button>
            <Button variant="ghost" size="sm" onClick={() => rented.append(emptyRentedProperty('renteddwelling'))}>
              <Plus /> Rented Dwelling
            </Button>
          </div>
        </section>
      )}

      <Separator />
      <p className="text-xs text-muted-foreground">
        Structure options: {STRUCTURE_ITEMS.map((s) => s.t).join(', ')}.
      </p>
    </div>
  )
}

function VehiclePanel({
  control,
  watch,
  index,
  onRemove,
}: {
  control: Control<Lead>
  watch: UseFormWatch<Lead>
  index: number
  onRemove?: () => void
}) {
  const ownership = watch(`vehicles.${index}.ownership`)
  const showFinancing = ownership === 'financed' || ownership === 'leased'
  return (
    <div className="rounded-lg border p-4">
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-sm font-semibold">Vehicle {index + 1}</h4>
        {onRemove && (
          <Button variant="ghost" size="icon" onClick={onRemove}>
            <Trash2 />
          </Button>
        )}
      </div>
      <div className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        <TextField control={control} name={`vehicles.${index}.vin`} label="VIN number" placeholder="VIN" />
        <TextField control={control} name={`vehicles.${index}.ymm`} label="Year / Make / Model" placeholder="e.g. 2022 Toyota Corolla" />
        <TwoOptionField control={control} name={`vehicles.${index}.condition`} label="Condition" a="New" b="Used" />
        <SelectField control={control} name={`vehicles.${index}.ownership`} label="Ownership" items={OWNERSHIP} />
        {showFinancing && (
          <TextField control={control} name={`vehicles.${index}.financingCo`} label="Financing company" placeholder="Lender / lessor" />
        )}
        <TextField control={control} name={`vehicles.${index}.purchaseDate`} label="Purchase date" type="date" />
        <TextField control={control} name={`vehicles.${index}.oneWayKm`} label="1-way KM" type="number" placeholder="Daily commute (km)" />
        <TextField control={control} name={`vehicles.${index}.annualKm`} label="Annual KM" type="number" placeholder="Yearly km" />
        <TwoOptionField control={control} name={`vehicles.${index}.winterTires`} label="Winter tires" />
        <SelectField control={control} name={`vehicles.${index}.coverageType`} label="Coverage type" items={COVERAGE} />
        <TwoOptionField control={control} name={`vehicles.${index}.antiTheft`} label="Anti-theft" />
        <SelectField control={control} name={`vehicles.${index}.parkingNight`} label="Parking at night" items={PARKING} />
        <SelectField control={control} name={`vehicles.${index}.useType`} label="Use type" items={USE_TYPE} />
      </div>
    </div>
  )
}
