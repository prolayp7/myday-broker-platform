import { useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

// Adding another driver to the lead — a spouse, a child, whoever else will be
// on the policy.
//
// RELATIONSHIP IS THE ONLY QUESTION HERE. Everything else about them —
// licensing dates, vehicles — is filled in on New Policy once their row
// exists; asking for it in this dialog would just split one form into two.
//
import { RELATIONSHIPS } from './driverOptions'

export function AddDriverDialog({
  open,
  onOpenChange,
  onAdd,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onAdd: (relationship: string) => Promise<void>
}) {
  const [selected, setSelected] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  async function submit() {
    if (!selected || saving) return
    setSaving(true)
    try {
      await onAdd(selected)
      // Reset only on success. A failed add keeps the choice on screen so the
      // broker can retry without picking again.
      setSelected(null)
      onOpenChange(false)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!saving) onOpenChange(next)
      }}
    >
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle className="text-sm">Add a driver</DialogTitle>
          <DialogDescription className="text-xs">
            Say who they are to the applicant — their licensing and vehicles go
            on New Policy once they're added.
          </DialogDescription>
        </DialogHeader>

        {/* Buttons rather than a dropdown: seven short options, all visible at
            once, one click each. A select would hide them behind a click and
            add a second one to confirm. */}
        <div className="grid grid-cols-2 gap-1.5">
          {RELATIONSHIPS.filter((r) => r !== 'NA').map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setSelected(r)}
              aria-pressed={selected === r}
              className={cn(
                'h-(--height-control) rounded-md border px-2 text-xs transition-colors',
                selected === r
                  ? 'border-primary bg-primary/10 text-primary font-medium'
                  : 'border-input hover:bg-muted',
              )}
            >
              {r}
            </button>
          ))}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            className="h-(--height-control)"
            disabled={saving}
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            className="h-(--height-control)"
            disabled={!selected || saving}
            onClick={submit}
          >
            {saving ? 'Adding…' : 'Add driver'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
