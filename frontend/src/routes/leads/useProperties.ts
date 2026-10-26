import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  addDriverProperty,
  deleteProperty,
  firstValidationMessage,
  getDriverProperties,
  updateProperty,
  type Property,
  type PropertyRole,
} from '@/data/api'

/** What a broker can add under a Homeowner or Condo primary. */
export const RENTAL_ROLES: Exclude<PropertyRole, 'Primary'>[] = ['Rental Dwelling', 'Rented Condo']

/**
 * The applicant's properties and the three things a broker does to them.
 *
 * Kept apart from the paper that draws them so the fetch and the three saves
 * stay one unit of behaviour whatever the page around them looks like.
 */
export function useProperties(applicantId: string | null, policyType: string | null) {
  const [properties, setProperties] = useState<Property[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!applicantId) return
    let cancelled = false
    setLoading(true)
    getDriverProperties(applicantId)
      .then((rows) => {
        if (!cancelled) setProperties(rows)
      })
      .catch(() => {
        if (!cancelled) setProperties([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // policyType TOO, not only the applicant. A policy-type change replaces
    // the primary property and can delete rentals on the server. The paper
    // keeps Home on screen through the change (the old tabbed form remounted
    // it on a tab switch, which hid this), so without a refetch it would keep
    // showing deleted rows, and the next edit would target a deleted id.
    // LeadDetail sets policyType only after the server confirmed the change,
    // so this fetch sees the new rows.
  }, [applicantId, policyType])

  /**
   * Save one edited field. Local state moves first so the field keeps what was
   * typed, then the patch carries only that field — sending the whole property
   * would overwrite answers the broker never touched.
   *
   * On failure the row goes back as it was: a quote the broker believes is
   * saved and isn't is worse than a visible error.
   */
  async function save(propertyId: string, patch: Partial<Property>) {
    const before = properties.find((p) => p.id === propertyId)
    setProperties((list) => list.map((p) => (p.id === propertyId ? { ...p, ...patch } : p)))
    try {
      await updateProperty(propertyId, patch)
    } catch (err) {
      if (before) {
        setProperties((list) => list.map((p) => (p.id === propertyId ? before : p)))
      }
      toast.error(firstValidationMessage(err) ?? 'Could not save the property')
    }
  }

  async function add(role: Exclude<PropertyRole, 'Primary'>) {
    if (!applicantId) return
    // A Rented Condo is rated as a condo; a Rental Dwelling as a house. The
    // applicant's own policy type does not decide what an investment property
    // is, which is why property_type is stored per row.
    const type = role === 'Rented Condo' ? 'Condo' : 'Homeowner'
    try {
      const created = await addDriverProperty(applicantId, role, type)
      setProperties((list) => [...list, created])
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not add the property')
    }
  }

  /** Resolves either way; the caller closes its confirm box in `finally`. */
  async function remove(property: Property) {
    try {
      await deleteProperty(property.id)
      setProperties((list) => list.filter((p) => p.id !== property.id))
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not remove the property')
    }
  }

  const primary = properties.find((p) => p.property_role === 'Primary') ?? null
  const rentals = properties.filter((p) => p.property_role !== 'Primary')
  // A tenant policy covers one home — the backend refuses the add, so the
  // buttons are not offered either. Owner-occupied types only; matches
  // _CAN_HOLD_RENTALS in services/property.py, via the lead-level names.
  const canAddRentals =
    policyType === 'Homeowner Only' ||
    policyType === 'Auto and Homeowner' ||
    policyType === 'Auto and Condo'

  return { properties, primary, rentals, canAddRentals, loading, save, add, remove }
}
