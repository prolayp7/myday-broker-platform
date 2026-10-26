import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import { stageValue } from '@/domain/leadStages'
import {
  addDriverVehicle,
  addLeadDriver,
  createReminder,
  deleteLeadDriver,
  deleteVehicle,
  firstValidationMessage,
  getDriverVehicles,
  getLeadDrivers,
  setPolicyType as savePolicyType,
  updateDriver,
  updateVehicle,
  type Driver,
  type MyLead,
  getLeadPremium,
  saveLeadPremium,
  type LeadPremium,
  type SavePremiumInput,
} from '@/data/api'
import { fromRow, toPatch, type QuoteVehicle } from '@/domain/vehicle'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { AddDriverDialog } from './AddDriverDialog'
import { AddReminderDialog } from './AddReminderDialog'
import { ClassicPaper } from './classic/ClassicPaper'

// The working area for ONE lead. Has NO header of its own: the lead's
// identity, stage and facts are folded into the app title bar (LeadHeader,
// passed to AppShell as `context` from Leads.tsx), the way a desktop app puts
// the object you're working on in its title bar rather than spending a row.
//
// Just the New Policy form. There is no upload or auto-import in this
// workflow: the broker calls the customer, reads DASH's OWN website on their
// own screen, and types what they see straight into this form — VIN, model,
// licensing dates, premiums.


// Policy types with no auto side — switching to one wipes the lead's
// vehicles. Mirrors _PROPERTY_ONLY_TYPES in backend/services/lead.py, which
// does the actual deleting; this copy only clears what is on screen.
const PROPERTY_ONLY_TYPES = new Set(['Homeowner Only', 'Tenant Only'])
export function LeadDetail({
  lead,
  onStageChange,
  onEditIssuedDate,
  stageSaving,
  callCountSaving,
  onAdjustCallCount,
  onRemind,
  openReminder = false,
  onReminderOpened,
}: {
  lead: MyLead
  /** Moving the lead through the pipeline. Owned by Leads.tsx (it holds the
   *  list this reorders) and forwarded to the quote screen, where the stage
   *  now sits beside the policy's own dates and insurer. */
  onStageChange: (stage: string) => void
  /** Correcting a date already recorded, which is NOT a stage move — the lead
   *  stays on Issued and no history row is written (migrations/0086). */
  onEditIssuedDate: () => void
  stageSaving: boolean
  /** A call-counter request is in flight. Owned by Leads.tsx so the stepper
   *  here and the tally in the list move together. */
  callCountSaving: boolean
  onAdjustCallCount: (outcome: 'answered' | 'no_answer', delta: 1 | -1) => void
  onRemind: (at: Date) => Promise<void>
  /** Arrived from the renewal WhatsApp's "Set Up Reminder" button. Opens the
   *  lead with the reminder form already showing — the button names an
   *  action, so opening the lead and leaving the broker to find the form
   *  would be a broken promise. */
  openReminder?: boolean
  /** Fired once the dialog has been opened, so the caller can clear the URL
   *  param and a refresh does not reopen it. */
  onReminderOpened?: () => void
}) {
  // Which side of the quote is showing. Reset per lead by the remount
  // (key={lead.id} in Leads.tsx), so opening a lead always lands on the car —
  // the side every quote has.
  //
  // DELIBERATELY NOT the first tab in the strip. Contact History leads the
  // strip because the call comes before the quote, but the landing tab is
  // still the work: a broker opens a lead to fill it in, and starting them on
  // a read-only history would put one click between them and every edit.
  //
  // UNLESS THE BROKER CAME FOR THE REMINDER FORM, which lives on Contact
  // History. Landing them on the car and popping a dialog from a tab they
  // cannot see would be worse than either.

  // Every driver on the lead — the applicant, plus any spouse or children the
  // broker has added over the call. Loaded as soon as the pane opens; the read
  // creates the applicant if this lead has never been worked, so there is no
  // separate "create driver" step to perform.
  const [drivers, setDrivers] = useState<Driver[]>([])
  // Starts true: this component is remounted per lead (key={lead.id} in
  // Leads.tsx), so the very first render of every lead is already mid-fetch.
  // Starting false would flash the "no drivers" shape for one frame on every
  // single lead the broker opens.
  const [driversLoading, setDriversLoading] = useState(true)
  const [driverError, setDriverError] = useState(false)
  // Open state for the add-driver dialog.
  const [addingDriver, setAddingDriver] = useState(false)
  // The driver awaiting a delete confirmation, or null. Holding the DRIVER
  // rather than a boolean lets the dialog name them without a second field
  // that could fall out of step with it.
  const [removingDriver, setRemovingDriver] = useState<Driver | null>(null)

  // The cars being quoted. They hang off the APPLICANT — a spouse or child on
  // the policy does not bring their own vehicle list — so everything here is
  // keyed to that one driver's id.
  const [vehicles, setVehicles] = useState<QuoteVehicle[]>([])
  // Ids with a create or delete in flight. Their fields are disabled: a patch
  // against a row the backend has not finished creating would 404.
  const [pendingVehicles, setPendingVehicles] = useState<Set<string>>(new Set())

  // The applicant row itself, not just its id: the property tab shows their
  // address on the Primary, which IS where they live.
  const applicant = drivers.find((d) => d.is_principal) ?? null
  const applicantId = applicant?.id ?? null

  // Held here rather than read straight off `lead`: the lead prop comes from
  // the list, which only refetches on its own schedule, so a change made here
  // has to show immediately rather than waiting for the next poll.
  const [policyType, setPolicyType] = useState<string | null>(lead.combined_policy_type)

  // The premium row, or null while it loads / when none has been entered.
  // Owned here beside the drivers and vehicles it sits with, so New Policy
  // stays a rendering component and every save on that page goes through one
  // place.
  const [premium, setPremium] = useState<LeadPremium | null>(null)
  // A policy-type change the server refused because it would delete
  // properties, held with the server's own message until the broker accepts or
  // cancels. Holding the VALUE as well as the message means the confirmed
  // retry sends exactly what was refused.
  const [pendingPolicyType, setPendingPolicyType] = useState<{
    value: string
    message: string
  } | null>(null)


  /** Re-read the drivers AND their vehicles from the server.
   *
   *  Used after a DASH report fills the form: it writes several fields across
   *  the driver and adds vehicles in one server call, so re-reading is simpler
   *  and less error-prone than mirroring all of that into local state.
   *
   *  BOTH, because the vehicles load in their own effect keyed to applicantId,
   *  which does not change when a report fills the form. Reloading only the
   *  drivers left the cars in the database and off the screen, which reads as
   *  "it did not work" for the half a broker most wants to see. */
  const reloadDriversAndVehicles = useCallback(async () => {
    // AWAITED, AND FAILURE IS SAID OUT LOUD. Both reads used to swallow their
    // errors, so a report that saved perfectly and then failed to refetch
    // showed a success toast over a blank form — the broker's evidence being
    // that it had not worked. Rare, and exactly the case where silence is
    // worst: they cannot tell "did nothing" from "did it and could not show
    // you".
    await Promise.all([
      getLeadDrivers(lead.id).then(setDrivers),
      applicantId
        ? getDriverVehicles(applicantId).then((rows) => setVehicles(rows.map(fromRow)))
        : Promise.resolve(),
    ])
  }, [lead.id, applicantId])

  useEffect(() => {
    let cancelled = false
    setDriverError(false)
    setDriversLoading(true)
    getLeadDrivers(lead.id)
      .then((list) => {
        if (!cancelled) setDrivers(list)
      })
      .catch(() => {
        if (!cancelled) setDriverError(true)
      })
      .finally(() => {
        // Cleared on failure too — the error message takes the placeholder's
        // place, and a skeleton left pulsing forever would promise rows that
        // are never coming.
        if (!cancelled) setDriversLoading(false)
      })
    // Guards against a slow response for the PREVIOUS lead landing after the
    // broker has already clicked onto another one.
    return () => {
      cancelled = true
    }
  }, [lead.id])

  // Vehicles load once the applicant is known, which is why this waits on
  // applicantId rather than lead.id: the driver fetch above is what produces
  // the id these hang off.
  useEffect(() => {
    if (!applicantId) return
    let cancelled = false
    getDriverVehicles(applicantId)
      .then((rows) => {
        if (!cancelled) setVehicles(rows.map(fromRow))
      })
      .catch(() => {
        // Deliberately quiet. An empty list is the normal state before any car
        // is added, so a toast here would fire on most leads and mean nothing.
        // A failed SAVE is what the broker needs telling about.
        if (!cancelled) setVehicles([])
      })
    return () => {
      cancelled = true
    }
  }, [applicantId])

  /**
   * Answer the combined-vs-standalone question.
   *
   * Switching DOWN — to Standalone, or to Tenants while rentals exist —
   * destroys properties, so the backend refuses first with a 409 naming how
   * many. That message becomes the confirmation dialog: the broker is told
   * what it costs before it happens, and only a second, explicit call carries
   * confirm.
   *
   * The count comes from the server rather than being derived here, so the
   * warning describes what is actually stored rather than what this component
   * last happened to load.
   */
  // The premium row for this lead. Null is the ordinary state — most leads
  // have no premium yet — so a miss is not an error and not worth a toast.
  useEffect(() => {
    let cancelled = false
    getLeadPremium(lead.id)
      .then((row) => {
        if (!cancelled) setPremium(row)
      })
      .catch(() => {
        // Quiet, same reasoning as the vehicles fetch above: an empty premium
        // is normal, and a failed SAVE is what a broker needs telling about.
        if (!cancelled) setPremium(null)
      })
    return () => {
      cancelled = true
    }
  }, [lead.id])

  /**
   * Save one premium field, or the insurer.
   *
   * SENDS ONLY WHAT CHANGED. The endpoint patches with exclude_unset, so an
   * omitted column is left alone — posting the whole object each time would
   * overwrite anything entered between two of these calls.
   *
   * Takes the server's row as the new truth rather than merging locally: every
   * annual figure, both totals and both savings are generated columns, so the
   * response is the only place their real values exist.
   */
  async function savePremium(patch: SavePremiumInput) {
    try {
      const saved = await saveLeadPremium(lead.id, patch)
      setPremium(saved)
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not save that premium')
    }
  }

  async function changePolicyType(value: string, confirm = false) {
    try {
      const updated = await savePolicyType(lead.id, value, confirm)
      setPolicyType(updated.combined_policy_type)
      setPendingPolicyType(null)

      // Switching to a property-only type deletes the vehicles server-side,
      // so clear them here too — otherwise the blocks stay on screen, still
      // editable, and anything typed into them saves into rows that are gone.
      if (PROPERTY_ONLY_TYPES.has(updated.combined_policy_type ?? '')) {
        setVehicles([])
      }

      // The switch also nulls the premiums for products the new type no longer
      // covers (services/lead.py::_clear_premiums), so the row on screen is now
      // stale in the same way the vehicles were. Left alone, the boxes keep
      // showing figures the database no longer holds — and the next blur on one
      // would save the cleared figure straight back.
      //
      // REFETCHED, not cleared from a copy of the rule here: the server decides
      // which products go, and a second copy of that map in TypeScript is the
      // drift this codebase has already been bitten by twice (the 'Standalone'
      // check that silently stopped matching after 0067). One request, and the
      // totals and savings come back generated rather than guessed.
      try {
        setPremium(await getLeadPremium(lead.id))
      } catch {
        // Quiet, matching the initial fetch: the switch itself succeeded, and
        // an error toast here would report a failure that did not happen.
      }
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        setPendingPolicyType({
          value,
          message: String(err.response.data?.detail ?? 'This will delete properties.'),
        })
        return
      }
      toast.error(firstValidationMessage(err) ?? 'Could not change the policy type')
    }
  }

  /**
   * Save one edited field. Local state moves first so the field keeps what was
   * typed, then the patch goes out carrying only that field — sending the whole
   * vehicle would overwrite answers the broker never touched.
   *
   * On failure the row is put back as it was, rather than left showing a value
   * the server rejected. A quote the broker believes is saved and isn't is
   * worse than a visible error.
   */
  async function saveVehicle(vehicleId: string, patch: Partial<QuoteVehicle>) {
    const before = vehicles.find((v) => v.id === vehicleId)
    setVehicles((list) => list.map((v) => (v.id === vehicleId ? { ...v, ...patch } : v)))
    try {
      await updateVehicle(vehicleId, toPatch(patch))
    } catch (err) {
      if (before) {
        setVehicles((list) => list.map((v) => (v.id === vehicleId ? before : v)))
      }
      toast.error(firstValidationMessage(err) ?? 'Could not save the vehicle')
    }
  }

  /**
   * Add a car to the quote.
   *
   * The row is created server-side FIRST and only then shown: every field saves
   * against a real vehicle id, so a row that existed only on screen would 404
   * on the broker's first keystroke.
   */
  async function addVehicle() {
    if (!applicantId) return
    try {
      const created = await addDriverVehicle(applicantId)
      setVehicles((list) => [...list, fromRow(created)])
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not add the vehicle')
    }
  }

  /**
   * Remove a car from the quote — sold, or never theirs.
   *
   * Taken off screen only once the delete has actually succeeded, so a car that
   * failed to delete is never shown as gone while it still exists.
   */
  async function removeVehicle(vehicleId: string) {
    setPendingVehicles((s) => new Set(s).add(vehicleId))
    try {
      await deleteVehicle(vehicleId)
      setVehicles((list) => list.filter((v) => v.id !== vehicleId))
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not remove the vehicle')
    } finally {
      setPendingVehicles((s) => {
        const next = new Set(s)
        next.delete(vehicleId)
        return next
      })
    }
  }

  /**
   * Remove a sub-driver and their licensing record. Confirmed first, and the
   * confirmation names them, since this deletes a real person's row.
   *
   * The applicant has no × at all — the backend rejects it too, but a control
   * that only ever errors should not be on screen.
   */
  async function removeDriver(d: Driver) {
    const who = d.relationship_to_applicant ?? 'this driver'
    try {
      await deleteLeadDriver(d.id)
      setDrivers((list) => list.filter((x) => x.id !== d.id))
      toast.success(`${who} removed`)
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not remove the driver')
    }
  }

  /**
   * Add a spouse or child to the policy. Appends locally rather than
   * refetching the list: the backend returns the created row, and the order
   * is already known (applicant first, then creation order), so a round trip
   * would only confirm what we have.
   */
  async function addDriver(relationship: string) {
    try {
      const created = await addLeadDriver(lead.id, relationship)
      setDrivers((list) => [...list, created])
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not add the driver')
      throw err
    }
  }

  /**
   * A driver's own details — their name, phone, email and licensing dates —
   * go straight onto that person's record. They are facts about the PERSON,
   * not about this quote, which is why they save here rather than into New
   * Policy's draft: quote the same customer again next year and their
   * licensing is already on file.
   *
   * The local list is updated too, so the row keeps showing what was typed
   * even though the value now lives in the database. Without it, a re-render
   * would reset the field to the stale driver object still held here.
   */
  async function saveDriverField(
    driverId: string,
    // Includes the address columns. They were left out while the address was
    // only ever displayed on the auto side, and the omission was invisible
    // there — the field held its own copy of what was typed. The property tab
    // reads the applicant's address from THIS state, so a patch that never
    // reached it left the Primary showing the previous address.
    patch: Partial<
      Pick<
        Driver,
        | 'name'
        | 'phone'
        | 'email'
        | 'g1_date'
        | 'g2_date'
        | 'g_date'
        // What Meta matches on, hashed as `db` and `ge` — see
        // backend/meta_capi.py::build_user_data.
        | 'date_of_birth'
        | 'gender'
        | 'street_address'
        | 'city'
        | 'province'
        | 'postal_code'
      >
    >,
  ) {
    const before = drivers.find((d) => d.id === driverId)
    setDrivers((list) => list.map((d) => (d.id === driverId ? { ...d, ...patch } : d)))
    try {
      await updateDriver(driverId, patch)
    } catch (err) {
      // Put the row back rather than leave a value on screen the server
      // rejected — the same rule the vehicle save follows.
      if (before) {
        setDrivers((list) => list.map((d) => (d.id === driverId ? before : d)))
      }
      toast.error(firstValidationMessage(err) ?? 'Could not save the driver')
    }
  }


  // THE RENEWAL WHATSAPP'S "SET UP REMINDER". The tabbed form opened this
  // from its Contact History tab; the paper has no tabs, so the form is
  // opened here instead. The caller is told at once so the URL param is
  // cleared and a refresh does not reopen a dialog already dismissed.
  const [addingReminder, setAddingReminder] = useState(false)
  useEffect(() => {
    if (!openReminder) return
    setAddingReminder(true)
    onReminderOpened?.()
  }, [openReminder, onReminderOpened])

  const dialogs = (
    <>
    <AddReminderDialog
      open={addingReminder}
      onOpenChange={setAddingReminder}
      onAdd={async ({ kind, dueAt, note }) => {
        try {
          await createReminder(lead.id, {
            // datetime-local is a wall-clock string with no zone; converted
            // so a timestamptz does not land hours out (as ContactHistoryPage).
            due_at: new Date(dueAt).toISOString(),
            note,
            kind,
          })
          toast.success('Reminder set')
        } catch (err) {
          toast.error(firstValidationMessage(err) ?? 'Could not set the reminder')
        }
      }}
    />
    <AddDriverDialog
      open={addingDriver}
      onOpenChange={setAddingDriver}
      onAdd={(relationship) => addDriver(relationship)}
    />

    {/* The server's own refusal, turned into a decision. It names the count
        because it counted — deriving that here would describe what this
        component last loaded rather than what is stored. */}
    <Dialog
      open={pendingPolicyType !== null}
      onOpenChange={(open) => !open && setPendingPolicyType(null)}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Change the policy type?</DialogTitle>
          <DialogDescription>{pendingPolicyType?.message}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => setPendingPolicyType(null)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() =>
              pendingPolicyType && void changePolicyType(pendingPolicyType.value, true)
            }
          >
            Change and delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <RemoveDriverDialog
      driver={removingDriver}
      onOpenChange={(open) => !open && setRemovingDriver(null)}
      onRemove={removeDriver}
    />
    </>
  )

  // THE PAPER. The customer detail panel is drawn as a DASH-style report on
  // one sheet with no tabs (routes/leads/classic/), over the data and
  // handlers this component owns.
  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-auto">
      <ClassicPaper
        leadName={lead.name}
        drivers={drivers}
        driversLoading={driversLoading}
        driverError={driverError}
        vehicles={vehicles}
        pendingVehicles={pendingVehicles}
        onSaveDriverField={saveDriverField}
        onSaveVehicle={saveVehicle}
        onAddVehicle={addVehicle}
        onRemoveVehicle={removeVehicle}
        onAddDriver={() => setAddingDriver(true)}
        onDriversChanged={reloadDriversAndVehicles}
        onRemoveDriver={setRemovingDriver}
        policyType={policyType}
        onSetPolicyType={changePolicyType}
        premium={premium}
        onSavePremium={savePremium}
        stage={stageValue(lead.stage, lead.sub_stage)}
        onStageChange={onStageChange}
        onEditIssuedDate={onEditIssuedDate}
        stageSaving={stageSaving}
        answeredCount={lead.answered_count}
        noAnswerCount={lead.no_answer_count}
        callCountSaving={callCountSaving}
        onAdjustCallCount={onAdjustCallCount}
        onRemind={onRemind}
      />
      {dialogs}
    </div>
  )
}

/**
 * Confirming the removal of a driver.
 *
 * A real dialog rather than window.confirm: the browser's own box carries the
 * page's URL and its own buttons, which reads as the site misbehaving rather
 * than as part of the app.
 */
function RemoveDriverDialog({
  driver,
  onOpenChange,
  onRemove,
}: {
  driver: Driver | null
  onOpenChange: (open: boolean) => void
  onRemove: (driver: Driver) => void
}) {
  const who = driver?.relationship_to_applicant ?? 'this driver'
  return (
    <Dialog open={driver !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-sm">Remove the {who}?</DialogTitle>
          <DialogDescription className="text-xs">
            Their licensing details on this quote will be removed.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            className="h-(--height-control)"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            size="sm"
            className="h-(--height-control)"
            onClick={() => {
              if (driver) onRemove(driver)
              onOpenChange(false)
            }}
          >
            Remove {who}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
