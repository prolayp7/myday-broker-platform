import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { type Driver, type LeadPremium, type Property, type SavePremiumInput } from '@/data/api'
import { stageOptions } from '@/domain/leadStages'
import {
  CONDITIONS,
  COVERAGE_TYPES,
  EMPTY_PREMIUMS,
  OWNERSHIPS,
  PARKING_AT_NIGHT,
  POLICY_TYPES,
  policyHasAuto,
  PREMIUM_FIELDS_FOR_POLICY,
  PREMIUM_ROWS,
  premiumApiField,
  type PolicyType,
  type Premiums,
  type QuoteVehicle,
} from '@/domain/vehicle'
import { CallMarks } from '../CallMarks'
import { DashPrefill } from '../DashPrefill'
import { AddressRow } from '../AddressRow'
import { RELATIONSHIPS } from '../driverOptions'
import { STRUCTURES } from '../propertyOptions'
import { RENTAL_ROLES, useProperties } from '../useProperties'
import { InlineDate, InlineSelect, InlineText, Line } from './inline'

// THE CLASSIC VIEW: the customer detail panel as a DASH-style paper report.
//
// LeadDetail owns the fetches and the saves; this draws them. It replaced the
// tabbed Auto/Home form in 2026-10 at the user's direction, because brokers
// read DASH reports all day and a form full of boxes was harder to scan than
// the same facts laid out as a page. It draws Auto and Home on ONE sheet — Applicant · Additional Drivers · Vehicles
// · Premium · Home — as plain "Label: value" lines in two columns, every value
// edited in place. There are no tabs: a report is read top to bottom.
//
// Two things it does NOT own: the Add Driver dialog and the policy-type
// warning, both of which LeadDetail renders beside the paper. Vehicle and
// property removal are confirmed here, beside the lists they act on.

// The applicant fields the stage gate refuses Quote Done without
// (services/lead.py::_REQUIRED_APPLICANT_FIELDS). Marked "required" in the
// gap while empty; nothing here enforces them — the backend does.
const REQUIRED = new Set<keyof Driver>([
  'first_name',
  'last_name',
  'street_address',
  'phone',
  'email',
  'gender',
  'date_of_birth',
  'insured_since',
])

const MARITAL = ['Single', 'Married', 'Common Law', 'Divorced', 'Widowed'] as const
const YES_NO = [
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
] as const
const UNITS = [
  { value: 'year', label: 'Year' },
  { value: 'month', label: 'Month' },
] as const

type DriverPatch = Partial<
  Pick<
    Driver,
    | 'name'
    | 'phone'
    | 'email'
    | 'g1_date'
    | 'g2_date'
    | 'g_date'
    | 'renewal_date'
    | 'insured_since'
    | 'current_insurer_since'
    | 'date_of_birth'
    | 'gender'
    | 'marital_status'
    | 'license_number'
    | 'relationship_to_applicant'
    | 'street_address'
    | 'city'
    | 'province'
    | 'postal_code'
  >
>

export function ClassicPaper({
  leadName,
  drivers,
  driversLoading,
  driverError,
  vehicles,
  pendingVehicles,
  onSaveDriverField,
  onSaveVehicle,
  onAddVehicle,
  onRemoveVehicle,
  onAddDriver,
  onDriversChanged,
  onRemoveDriver,
  policyType,
  onSetPolicyType,
  premium,
  onSavePremium,
  stage,
  onStageChange,
  onEditIssuedDate,
  stageSaving,
  answeredCount,
  noAnswerCount,
  callCountSaving,
  onAdjustCallCount,
  onRemind,
}: {
  leadName: string
  drivers: Driver[]
  driversLoading: boolean
  driverError: boolean
  vehicles: QuoteVehicle[]
  pendingVehicles: Set<string>
  onSaveDriverField: (driverId: string, patch: DriverPatch) => void
  onSaveVehicle: (vehicleId: string, patch: Partial<QuoteVehicle>) => void
  onAddVehicle: () => void
  onRemoveVehicle: (vehicleId: string) => void
  onAddDriver: () => void
  onDriversChanged: () => void
  onRemoveDriver: (driver: Driver) => void
  policyType: string | null
  onSetPolicyType: (value: string) => void
  premium: LeadPremium | null
  onSavePremium: (patch: SavePremiumInput) => void
  stage: string
  onStageChange: (stageValue: string) => void
  onEditIssuedDate: () => void
  stageSaving: boolean
  answeredCount: number
  noAnswerCount: number
  callCountSaving: boolean
  onAdjustCallCount: (outcome: 'answered' | 'no_answer', delta: 1 | -1) => void
  onRemind: (at: Date) => Promise<void>
}) {
  const applicant = drivers.find((d) => d.is_principal) ?? null
  const additional = drivers.filter((d) => !d.is_principal)
  const [removingVehicle, setRemovingVehicle] = useState<QuoteVehicle | null>(null)

  // A DASH fill changes many values at once; flash the applicant's lines for a
  // moment so the broker sees WHAT moved rather than hunting for it.
  const [flash, setFlash] = useState(false)
  useEffect(() => {
    if (!flash) return
    const t = setTimeout(() => setFlash(false), 1300)
    return () => clearTimeout(t)
  }, [flash])

  const name = applicant
    ? [applicant.last_name, [applicant.first_name, applicant.middle_name].filter(Boolean).join(' ')]
        .filter(Boolean)
        .join(', ') || applicant.name || leadName
    : leadName

  return (
    <div className="classic min-w-0">
      <div className="cl-paper">
        {/* ── header ── */}
        <div className="cl-hdr">
          <div className="cl-name">{name.toUpperCase()}</div>
          <div className="cl-kv">
            <Line label="Policy Type">
              <InlineSelect
                label="Policy type"
                value={policyType}
                options={POLICY_TYPES}
                onChange={onSetPolicyType}
                placeholder="choose"
              />
            </Line>
            <Line label="Stage">
              <InlineSelect
                label="Stage"
                value={stage}
                options={stageOptions()}
                onChange={onStageChange}
                disabled={stageSaving}
              />
              {/* Only on Issued: re-picking Issued fires nothing, so this is
                  the one way to correct the date already recorded. */}
              {stage === 'Issued' && (
                <button
                  type="button"
                  className="cl-link cl-link-sm"
                  disabled={stageSaving}
                  onClick={onEditIssuedDate}
                >
                  Edit issued date
                </button>
              )}
            </Line>
            <Line label="Renewal">
              {applicant ? (
                <InlineDate
                  label="Renewal"
                  value={applicant.renewal_date}
                  onChange={(x) => onSaveDriverField(applicant.id, { renewal_date: x || null })}
                />
              ) : (
                <span className="cl-text" />
              )}
            </Line>
          </div>
        </div>

        {/* ── the grey strip: calls, then DASH ── */}
        <div className="cl-strip">
          <div className="cl-strip-row">
            <span className="cl-strip-k">Calls</span>
            <CallMarks
              answeredCount={answeredCount}
              noAnswerCount={noAnswerCount}
              busy={callCountSaving}
              onAdjust={onAdjustCallCount}
              onRemind={onRemind}
            />
          </div>
          {applicant && (
            <div className="cl-strip-row">
              <span className="cl-strip-k">DASH</span>
              <DashPrefill
                driverId={applicant.id}
                hasReport={Boolean(applicant.dash_report_path)}
                onFilled={async () => {
                  await onDriversChanged()
                  setFlash(true)
                }}
              />
            </div>
          )}
        </div>

        {/* ── Applicant ── */}
        <section className="cl-sec">
          <div className="cl-bar">Applicant</div>
          {driverError && (
            <div className="cl-error">
              Could not load this lead&apos;s drivers — reload to try again.
            </div>
          )}
          {driversLoading && drivers.length === 0 ? (
            <div className="cl-empty">Loading…</div>
          ) : (
            applicant && (
              <div className={flash ? 'cl-flash' : undefined}>
                <DriverLines
                  driver={applicant}
                  onSave={(patch) => onSaveDriverField(applicant.id, patch)}
                />
              </div>
            )
          )}
        </section>

        {/* ── Additional drivers ── */}
        <section className="cl-sec">
          <div className="cl-bar">
            <span>Additional Drivers</span>
            <span className="cl-bar-actions">
              <button type="button" className="cl-link" onClick={onAddDriver}>
                + Add driver
              </button>
            </span>
          </div>
          {additional.length === 0 && <div className="cl-empty">None added.</div>}
          {additional.map((d) => (
            <div key={d.id} className="cl-enter">
              <div className="cl-sub">
                <InlineSelect
                  label="Relationship to the applicant"
                  value={d.relationship_to_applicant}
                  options={RELATIONSHIPS.map((r) => ({
                    value: r,
                    label: r === 'NA' ? 'Not stated' : r,
                  }))}
                  onChange={(v) => onSaveDriverField(d.id, { relationship_to_applicant: v })}
                  placeholder="Relationship"
                />
                <button
                  type="button"
                  className="cl-link"
                  onClick={() => onRemoveDriver(d)}
                  aria-label={`Remove ${d.relationship_to_applicant ?? 'this driver'}`}
                >
                  Remove
                </button>
              </div>
              <DriverLines driver={d} onSave={(patch) => onSaveDriverField(d.id, patch)} />
            </div>
          ))}
        </section>

        {/* ── Vehicles: only on a policy that insures cars ── */}
        {policyHasAuto(policyType) && (
          <section className="cl-sec">
            <div className="cl-bar">
              <span>Vehicles</span>
              <span className="cl-bar-actions">
                <button type="button" className="cl-link" onClick={onAddVehicle}>
                  + Add vehicle
                </button>
              </span>
            </div>
            {vehicles.length === 0 && <div className="cl-empty">No vehicles added yet.</div>}
            {vehicles.map((v, i) => (
              <div key={v.id} className="cl-enter">
                <div className="cl-sub">
                  <span>Vehicle #{i + 1}</span>
                  <button
                    type="button"
                    className="cl-link"
                    onClick={() => setRemovingVehicle(v)}
                    disabled={pendingVehicles.has(v.id)}
                  >
                    Remove
                  </button>
                </div>
                <VehicleLines
                  vehicle={v}
                  disabled={pendingVehicles.has(v.id)}
                  onSave={(patch) => onSaveVehicle(v.id, patch)}
                />
              </div>
            ))}
          </section>
        )}

        {/* ── Premium ── */}
        <section className="cl-sec">
          <div className="cl-bar">
            <span>Premium</span>
            <span className="cl-bar-note">
              Accurate premiums improve the quality of leads you receive
            </span>
          </div>
          <PremiumLines premium={premium} policyType={policyType} onSave={onSavePremium} />
        </section>

        {/* ── Home: the property side, under the car on the same sheet ── */}
        {applicant && policyType && !policyType.startsWith('Auto Only') && (
          <HomeSection applicant={applicant} policyType={policyType} />
        )}
      </div>

      {/* Confirmed rather than instant: the row holds everything the broker
          asked about that car, and nothing keeps a copy. */}
      <Dialog
        open={removingVehicle !== null}
        onOpenChange={(open) => !open && setRemovingVehicle(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Remove {removingVehicle?.yearMakeModel || 'this vehicle'}?</DialogTitle>
            <DialogDescription>Everything entered for this vehicle will be lost.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setRemovingVehicle(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => {
                if (removingVehicle) onRemoveVehicle(removingVehicle.id)
                setRemovingVehicle(null)
              }}
            >
              Remove vehicle
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/**
 * One person's lines, in two columns. The applicant carries the address,
 * contact details, gender/DOB and the policy dates; an additional driver only
 * the name, licence, marital status and licensing history. Meta matches on
 * the applicant alone, and the policy being replaced is one per lead.
 */
function DriverLines({
  driver,
  onSave,
}: {
  driver: Driver
  onSave: (patch: DriverPatch) => void
}) {
  const main = driver.is_principal
  const req = (k: keyof Driver) => main && REQUIRED.has(k)
  const text = (k: keyof DriverPatch, label: string, type?: 'tel' | 'email') => (
    <Line label={label} required={req(k)}>
      <InlineText
        label={label}
        type={type}
        value={driver[k] as string | null}
        required={req(k)}
        onSave={(v) => onSave({ [k]: v || null } as DriverPatch)}
      />
    </Line>
  )
  const date = (k: keyof DriverPatch, label: string) => (
    <Line label={label} required={req(k)}>
      <InlineDate
        label={label}
        value={driver[k] as string | null}
        required={req(k)}
        onChange={(v) => onSave({ [k]: v || null } as DriverPatch)}
      />
    </Line>
  )
  const marital = (
    <Line label="Marital Status">
      <InlineSelect
        label="Marital status"
        value={driver.marital_status}
        options={MARITAL}
        onChange={(v) => onSave({ marital_status: v || null })}
        clearable
      />
    </Line>
  )

  return (
    <div className="cl-cols">
      <div className="cl-col">
        {text('first_name' as keyof DriverPatch, 'First name')}
        {text('middle_name' as keyof DriverPatch, 'Middle name')}
        {text('last_name' as keyof DriverPatch, 'Last name')}
        {main && (
          <Line label="Address" required>
            <span className="cl-addr" data-required={req('street_address') && !driver.street_address ? '' : undefined}>
              <AddressRow driver={driver} onSave={onSave} />
            </span>
          </Line>
        )}
        {main && text('phone', 'Phone', 'tel')}
        {main && text('email', 'Email', 'email')}
        {text('license_number', 'Licence Number')}
        {!main && marital}
      </div>
      <div className="cl-col">
        {main && (
          <Line label="Gender" required>
            <InlineSelect
              label="Gender"
              value={driver.gender}
              options={['Male', 'Female', 'Other']}
              onChange={(v) => onSave({ gender: v || null })}
              required
            />
          </Line>
        )}
        {main && marital}
        {main && date('date_of_birth', 'Date of Birth')}
        {main && date('insured_since', 'Continuous Insurance')}
        {main && date('current_insurer_since', 'Company Insured')}
        {date('g1_date', 'G1 Date')}
        {date('g2_date', 'G2 Date')}
        {date('g_date', 'G Date')}
      </div>
    </div>
  )
}

function VehicleLines({
  vehicle: v,
  disabled,
  onSave,
}: {
  vehicle: QuoteVehicle
  disabled: boolean
  onSave: (patch: Partial<QuoteVehicle>) => void
}) {
  const text = (k: keyof QuoteVehicle, label: string, type?: 'number') => (
    <Line label={label}>
      <InlineText
        label={label}
        type={type}
        value={v[k] as string}
        disabled={disabled}
        onSave={(x) => onSave({ [k]: x } as Partial<QuoteVehicle>)}
      />
    </Line>
  )
  const yn = (k: 'winterTires' | 'antiTheft', label: string) => (
    <Line label={label}>
      <InlineSelect
        label={label}
        value={v[k] === null ? '' : v[k] ? 'yes' : 'no'}
        options={YES_NO}
        disabled={disabled}
        onChange={(x) => onSave({ [k]: x === '' ? null : x === 'yes' } as Partial<QuoteVehicle>)}
        clearable
      />
    </Line>
  )
  const financed = v.ownership === 'Financed' || v.ownership === 'Leased'
  return (
    <div className="cl-cols">
      <div className="cl-col">
        {text('yearMakeModel', 'Make / Model')}
        {text('vin', 'VIN')}
        <Line label="Condition">
          <InlineSelect
            label="Condition"
            value={v.condition}
            options={CONDITIONS}
            disabled={disabled}
            onChange={(x) => onSave({ condition: x as QuoteVehicle['condition'] })}
            clearable
          />
        </Line>
        <Line label="Purchase Date">
          <InlineDate
            label="Purchase date"
            value={v.purchaseDate}
            onChange={(x) => onSave({ purchaseDate: x })}
          />
        </Line>
        {text('kmAtPurchase', 'KM at Purchase', 'number')}
        <Line label="Ownership">
          <InlineSelect
            label="Ownership"
            value={v.ownership}
            options={OWNERSHIPS}
            disabled={disabled}
            onChange={(x) => onSave({ ownership: x as QuoteVehicle['ownership'] })}
            clearable
          />
        </Line>
        {financed && text('financeCompany', 'Finance Company')}
      </div>
      <div className="cl-col">
        {text('annualKm', 'Annual KM', 'number')}
        {text('oneDayDistanceKm', '1 Day Distance', 'number')}
        <Line label="Parking at Night">
          <InlineSelect
            label="Parking at night"
            value={v.parkingAtNight}
            options={PARKING_AT_NIGHT}
            disabled={disabled}
            onChange={(x) => onSave({ parkingAtNight: x as QuoteVehicle['parkingAtNight'] })}
            clearable
          />
        </Line>
        {yn('winterTires', 'Winter Tires')}
        {yn('antiTheft', 'Anti-Theft')}
        <Line label="Coverage Type">
          <InlineSelect
            label="Coverage type"
            value={v.coverageType}
            options={COVERAGE_TYPES}
            disabled={disabled}
            onChange={(x) => onSave({ coverageType: x })}
            clearable
          />
        </Line>
      </div>
    </div>
  )
}

type Unit = 'year' | 'month' | ''

/**
 * The premium as a small table: one row per product the policy type carries,
 * Current and Proposed columns, each column in the unit the broker chose.
 *
 * A click on Year/Month saves the unit; a box saves on blur under the column's
 * unit (premiumApiField picks the column); every total and the savings are the
 * database's generated columns, read and never recomputed here.
 */
function PremiumLines({
  premium,
  policyType,
  onSave,
}: {
  premium: LeadPremium | null
  policyType: string | null
  onSave: (patch: SavePremiumInput) => void
}) {
  const currentUnit: Unit = premium?.current_premium_unit ?? ''
  const proposedUnit: Unit = premium?.new_premium_unit ?? ''
  const cur = currentUnit === 'month' ? 'monthly' : 'annual'
  const pro = proposedUnit === 'month' ? 'monthly' : 'annual'
  const current: Premiums = premium
    ? {
        auto: premium[`current_auto_premium_${cur}`] ?? '',
        homeowner: premium[`current_homeowner_premium_${cur}`] ?? '',
        tenant: premium[`current_tenant_premium_${cur}`] ?? '',
      }
    : EMPTY_PREMIUMS
  const proposed: Premiums = premium
    ? {
        auto: premium[`new_auto_premium_${pro}`] ?? '',
        homeowner: premium[`new_homeowner_premium_${pro}`] ?? '',
        tenant: premium[`new_tenant_premium_${pro}`] ?? '',
      }
    : EMPTY_PREMIUMS

  const fields = PREMIUM_FIELDS_FOR_POLICY[policyType as PolicyType] ?? ['auto']
  const rows = PREMIUM_ROWS.filter((r) => fields.includes(r.key))

  const money = (n: string | null | undefined) =>
    n == null || n === ''
      ? '—'
      : `$${Number(n).toLocaleString('en-CA', { maximumFractionDigits: 0 })}`

  const totalCurrent =
    currentUnit === 'month' ? premium?.total_current_premium_monthly : premium?.total_current_premium_annual
  const totalProposed =
    proposedUnit === 'month' ? premium?.total_new_premium_monthly : premium?.total_new_premium_annual
  const savings = proposedUnit === 'month' ? premium?.savings_monthly : premium?.savings_annual
  const worse = savings != null && Number(savings) < 0

  const box = (side: 'current' | 'new', key: keyof Premiums, unit: Unit, value: string) => (
    <InlineText
      label={`${side === 'current' ? 'Current' : 'Proposed'} ${key} premium`}
      type="number"
      value={value}
      disabled={unit === ''}
      minSize={4}
      onSave={(x) => onSave({ [premiumApiField(side, key, unit)]: x === '' ? null : x })}
    />
  )

  return (
    <>
      <table className="cl-table">
        <thead>
          <tr>
            <th />
            <th className="cl-num">
              Current{' '}
              <InlineSelect
                label="Current premium unit"
                value={currentUnit}
                options={UNITS}
                placeholder="Year/Month"
                onChange={(u) => u && onSave({ current_premium_unit: u as 'year' | 'month' })}
              />
            </th>
            <th className="cl-num">
              Proposed{' '}
              <InlineSelect
                label="Proposed premium unit"
                value={proposedUnit}
                options={UNITS}
                placeholder="Year/Month"
                onChange={(u) => u && onSave({ new_premium_unit: u as 'year' | 'month' })}
              />
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <td>{r.label}</td>
              <td className="cl-num">{box('current', r.key, currentUnit, current[r.key])}</td>
              <td className="cl-num">{box('new', r.key, proposedUnit, proposed[r.key])}</td>
            </tr>
          ))}
          {rows.length > 1 && (
            <tr className="cl-total">
              <td>Combined</td>
              <td className="cl-num">{money(totalCurrent)}</td>
              <td className="cl-num">{money(totalProposed)}</td>
            </tr>
          )}
        </tbody>
      </table>
      <div className="cl-savings" data-worse={worse ? '' : undefined}>
        Savings:{' '}
        <b>
          {savings == null || savings === ''
            ? '—'
            : `${worse ? '−' : ''}${money(String(Math.abs(Number(savings))))} / ${proposedUnit === 'month' ? 'month' : 'year'}`}
        </b>
      </div>
    </>
  )
}

/**
 * The property side. The Primary is where the applicant lives — its address
 * is the applicant's, shown and not stored twice — and cannot be removed here
 * (it goes by changing the policy type). Rentals carry their own address and
 * a Remove.
 */
function HomeSection({ applicant, policyType }: { applicant: Driver; policyType: string }) {
  const { primary, rentals, canAddRentals, loading, save, add, remove } = useProperties(
    applicant.id,
    policyType,
  )
  const [removing, setRemoving] = useState<Property | null>(null)
  const applicantAddress = [
    applicant.street_address,
    applicant.city,
    [applicant.province, applicant.postal_code].filter(Boolean).join(' '),
  ]
    .filter(Boolean)
    .join(', ')

  return (
    <section className="cl-sec">
      <div className="cl-bar">
        <span>Home</span>
        {canAddRentals && (
          <span className="cl-bar-actions">
            {RENTAL_ROLES.map((role) => (
              <button key={role} type="button" className="cl-link" onClick={() => add(role)}>
                + Add {role.toLowerCase()}
              </button>
            ))}
          </span>
        )}
      </div>
      {loading && <div className="cl-empty">Loading…</div>}
      {!loading && primary && (
        <PropertyLines
          property={primary}
          title="Primary Residence"
          applicantAddress={applicantAddress}
          onSave={(patch) => save(primary.id, patch)}
        />
      )}
      {rentals.map((p, i) => (
        <PropertyLines
          key={p.id}
          property={p}
          title={`${p.property_role} #${i + 1}`}
          applicantAddress={applicantAddress}
          onSave={(patch) => save(p.id, patch)}
          onRemove={() => setRemoving(p)}
        />
      ))}

      <Dialog open={removing !== null} onOpenChange={(open) => !open && setRemoving(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Remove this {removing?.property_role.toLowerCase()}?</DialogTitle>
            <DialogDescription>Everything entered for this property will be lost.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRemoving(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => removing && void remove(removing).finally(() => setRemoving(null))}
            >
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}

function PropertyLines({
  property: p,
  title,
  applicantAddress,
  onSave,
  onRemove,
}: {
  property: Property
  title: string
  applicantAddress: string
  onSave: (patch: Partial<Property>) => void
  onRemove?: () => void
}) {
  const isPrimary = p.property_role === 'Primary'
  // Tenants answer the shared questions only; the rest is about the BUILDING.
  const isBuilding = p.property_type !== 'Tenant'
  const isCondo = p.property_type === 'Condo'

  const text = (k: keyof Property, label: string) => (
    <Line label={label}>
      <InlineText
        label={`${title} ${label}`}
        value={p[k] as string | null}
        onSave={(x) => onSave({ [k]: x || null } as Partial<Property>)}
      />
    </Line>
  )
  // '' means "not collected", which is null — not zero.
  const num = (k: keyof Property, label: string) => (
    <Line label={label}>
      <InlineText
        label={`${title} ${label}`}
        type="number"
        value={p[k] == null ? '' : String(p[k])}
        onSave={(x) => onSave({ [k]: x === '' ? null : Number(x) } as Partial<Property>)}
      />
    </Line>
  )
  const date = (k: keyof Property, label: string) => (
    <Line label={label}>
      <InlineDate
        label={`${title} ${label}`}
        value={p[k] as string | null}
        onChange={(x) => onSave({ [k]: x || null } as Partial<Property>)}
      />
    </Line>
  )

  return (
    <div className="cl-enter">
      <div className="cl-sub">
        <span>{title}</span>
        {onRemove && (
          <button type="button" className="cl-link" onClick={onRemove} aria-label={`Remove ${title}`}>
            Remove
          </button>
        )}
      </div>
      <div className="cl-cols">
        <div className="cl-col">
          <Line label="Address">
            {isPrimary ? (
              <span className="cl-addr-ro">
                {applicantAddress || 'Set on the applicant'}{' '}
                <span className="cl-note">
                  (from Applicant)
                </span>
              </span>
            ) : (
              <span className="cl-addr">
                <AddressRow driver={p} onSave={onSave} />
              </span>
            )}
          </Line>
          {date('insured_since', 'Insured Since')}
          {date('insured_with_property_since', 'With Property Since')}
          <Line label="Structure">
            <InlineSelect
              label={`${title} structure`}
              value={p.structure}
              options={STRUCTURES}
              onChange={(x) => onSave({ structure: x || null })}
              clearable
            />
          </Line>
          {num('year_built', 'Year Built')}
          {isBuilding && num('stories', 'Stories')}
          {isBuilding && num('full_baths', 'Full Baths')}
          {isBuilding && num('half_baths', 'Half Baths')}
          {isBuilding && num('number_of_mortgages', 'Mortgages')}
          {isBuilding && text('finished_percent', 'Finished %')}
        </div>
        <div className="cl-col">
          {isBuilding && num('electrical_year', 'Electrical Year')}
          {isBuilding && num('plumbing_year', 'Plumbing Year')}
          {isBuilding && num('heating_year', 'Heating Year')}
          {isBuilding && num('roof_year', 'Roof Year')}
          {isBuilding && num('water_heater_year', 'Water Heater Year')}
          {isBuilding && text('burglar_alarm', 'Burglar Alarm')}
          {isBuilding && num('smoke_detectors', 'Smoke Detectors')}
          {isBuilding && num('fire_extinguishers', 'Fire Extinguishers')}
          {isBuilding && text('sump_type', 'Sump Type')}
          {isCondo && num('contents_value', 'Contents Value')}
          {text('notes', 'Notes')}
        </div>
      </div>
    </div>
  )
}
