import { useState } from 'react'
import { Plus } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { createManualLead, firstValidationMessage, type MyLead } from '@/data/api'

// Capturing a lead that did not arrive through an ad — a referral, a walk-in,
// someone who called the office. The broker takes the call and types three
// things while the customer is still on the line.
//
// Three fields, because three is what creating a lead actually requires. There
// is no source picker (everything entered here is manual by definition — a
// question with one answer is not worth asking), no policy type (that belongs
// to the quote, not the capture), and no "referred by" (there is nowhere to
// store a relationship yet, and a field whose value is discarded is worse than
// no field). Everything else about the lead gets filled in by working it.
//
// A dialog rather than a screen: this is a task done between calls, and a route
// change would cost the broker their place in the list they were working.

export interface NewLeadDraft {
  name: string
  phone: string
  email: string
}

const EMPTY: NewLeadDraft = { name: '', phone: '', email: '' }

export function AddLeadDialog({ onCreate }: { onCreate?: (lead: MyLead) => void }) {
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [draft, setDraft] = useState<NewLeadDraft>(EMPTY)

  // A lead needs a name and some way to reach them. Email alone is enough for
  // a web enquiry; a phone alone is enough for someone who just called — so
  // either will do, rather than demanding both from a broker mid-conversation.
  const canSave = draft.name.trim() !== '' && (draft.phone.trim() !== '' || draft.email.trim() !== '')

  function set(patch: Partial<NewLeadDraft>) {
    setDraft((d) => ({ ...d, ...patch }))
  }

  function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSave || saving) return
    void save()
  }

  async function save() {
    setSaving(true)
    try {
      // Only what was actually given. Sending '' for the missing one would be
      // a value the backend has to interpret; omitting it says plainly that it
      // was not collected, and the server stores its own placeholder.
      const created = await createManualLead({
        name: draft.name.trim(),
        phone: draft.phone.trim() || undefined,
        email: draft.email.trim() || undefined,
      })
      onCreate?.(created)
      toast(`${created.name} added`)
      setDraft(EMPTY)
      setOpen(false)
    } catch (err) {
      // Kept OPEN on failure, with what was typed still in it: the customer is
      // usually still on the phone, and a dialog that closes on error would
      // mean asking them for everything again.
      toast.error(firstValidationMessage(err) ?? 'Could not add the lead')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        // Closing discards: a half-typed lead reopening later would look like
        // a saved record that isn't one.
        if (!next) setDraft(EMPTY)
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="h-(--height-control) shrink-0">
          <Plus /> Add
        </Button>
      </DialogTrigger>

      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle className="text-base">Add lead</DialogTitle>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="new-lead-name" className="text-xs">
              Name
            </Label>
            <Input
              id="new-lead-name"
              value={draft.name}
              onChange={(e) => set({ name: e.target.value })}
              placeholder="Full name"
              // The customer is usually on the phone — start typing immediately.
              autoFocus
              className="h-(--height-control) text-sm"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="new-lead-phone" className="text-xs">
              Phone
            </Label>
            <Input
              id="new-lead-phone"
              type="tel"
              inputMode="tel"
              value={draft.phone}
              onChange={(e) => set({ phone: e.target.value })}
              placeholder="416-555-0182"
              className="h-(--height-control) text-sm"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="new-lead-email" className="text-xs">
              Email
            </Label>
            <Input
              id="new-lead-email"
              type="email"
              value={draft.email}
              onChange={(e) => set({ email: e.target.value })}
              placeholder="name@example.com"
              className="h-(--height-control) text-sm"
            />
          </div>

          <p className="text-muted-foreground text-2xs">
            A phone number or an email — whichever you have.
          </p>

          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="ghost" size="sm" className="h-(--height-control)">
                Cancel
              </Button>
            </DialogClose>
            <Button type="submit" size="sm" className="h-(--height-control)" disabled={!canSave || saving}>
              Add lead
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
