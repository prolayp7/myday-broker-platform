import { useEffect, useState } from 'react'
import { Phone, X } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { getLeadPhones, deleteLeadPhone, firstValidationMessage, type LeadPhone } from '@/data/api'

// Every number this customer can be reached on, iPhone-contacts style.
//
// THE LIST ARRIVES READY. The merge and the deduplication happen on the
// server: a customer's numbers live in three tables (the lead, its drivers,
// lead_phone) and this screen holds only one of them. Sorting it out here
// would also mean mobile doing the same thing separately, and the two
// eventually disagreeing about what numbers a customer has.
//
// WHAT IS DELETABLE. Only secondary numbers, and only when they are not also
// the lead's or a driver's — deleting the lead_phone row for a number the lead
// still holds would not stop it matching, so the control would promise
// something it cannot do. The server decides; this renders `deletable`.

const SOURCE_LABEL: Record<LeadPhone['source'], string> = {
  lead: 'Primary',
  driver: 'Driver',
  secondary: 'Also reached on',
}

export default function LeadNumbers({ leadId }: { leadId: string }) {
  const [phones, setPhones] = useState<LeadPhone[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    getLeadPhones(leadId)
      .then((p) => !cancelled && setPhones(p))
      .catch(() => !cancelled && setPhones([]))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [leadId])

  async function remove(phone: LeadPhone) {
    if (!phone.phone_id) return
    const previous = phones
    // Optimistic: the row goes now, and comes back if the server refuses.
    setPhones((p) => p.filter((x) => x.phone_id !== phone.phone_id))
    try {
      await deleteLeadPhone(leadId, phone.phone_id)
    } catch (err) {
      setPhones(previous)
      toast.error(firstValidationMessage(err) ?? 'Could not remove that number')
    }
  }

  if (loading) return null
  if (!phones.length) return null

  return (
    <div className="space-y-1">
      {phones.map((phone) => (
        <div
          key={phone.phone_id ?? phone.phone}
          className="flex items-center gap-2 rounded-md border bg-secondary px-2 py-1.5 text-sm"
        >
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium">{phone.phone}</div>
            <div className="text-muted-foreground text-2xs">
              {SOURCE_LABEL[phone.source]}
            </div>
          </div>

          <Button variant="ghost" size="icon" asChild>
            <a href={`tel:${phone.phone}`} aria-label={`Call ${phone.phone}`}>
              <Phone className="size-3.5" />
            </a>
          </Button>

          {phone.deletable && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Remove ${phone.phone}`}
              onClick={() => remove(phone)}
            >
              <X className="size-3.5" />
            </Button>
          )}
        </div>
      ))}
    </div>
  )
}
