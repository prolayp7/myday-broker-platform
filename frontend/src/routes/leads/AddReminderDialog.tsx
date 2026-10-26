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
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

// Setting a follow-up. Three fields, because three is all POST
// /leads/{id}/reminders takes: kind, due_at, note.
//
// WIREFRAME — onAdd is a no-op today.

/** Matches the check constraint in migrations/0061_reminder.sql. */
export type ReminderKind = 'Callback' | 'Renewal' | 'Document' | 'Other'

const KINDS: { value: ReminderKind; label: string; hint: string }[] = [
  { value: 'Callback', label: 'Callback', hint: 'Ring them back' },
  { value: 'Renewal', label: 'Renewal', hint: "Their policy expires — chase before it auto-renews" },
  { value: 'Document', label: 'Document', hint: 'Waiting on something from them' },
  { value: 'Other', label: 'Other', hint: '' },
]

export function AddReminderDialog({
  open,
  onOpenChange,
  onAdd,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onAdd: (reminder: { kind: ReminderKind; dueAt: string; note: string }) => void
}) {
  const [kind, setKind] = useState<ReminderKind>('Callback')
  const [dueAt, setDueAt] = useState('')
  const [note, setNote] = useState('')

  // The note is what the reminder IS. The backend requires it too
  // (length(trim(note)) > 0) — an alarm with no reason is one the broker
  // cannot act on by the time it fires.
  const canSave = dueAt !== '' && note.trim() !== ''

  function submit() {
    if (!canSave) return
    onAdd({ kind, dueAt, note: note.trim() })
    setKind('Callback')
    setDueAt('')
    setNote('')
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-sm">Set a follow-up</DialogTitle>
          <DialogDescription className="text-xs">
            It appears on your My Day list when it comes due.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-2xs font-normal text-muted-foreground">Kind</Label>
            <Select value={kind} onValueChange={(v) => setKind(v as ReminderKind)}>
              <SelectTrigger className="h-(--height-control) w-full text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KINDS.map((k) => (
                  <SelectItem key={k.value} value={k.value} className="text-xs">
                    {k.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {/* Names what the chosen kind means rather than leaving four words
                to be guessed at — Renewal in particular is not just another
                callback, and the difference is worth a sentence. */}
            {KINDS.find((k) => k.value === kind)?.hint && (
              <p className="text-muted-foreground text-2xs">
                {KINDS.find((k) => k.value === kind)?.hint}
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="reminder-due" className="text-2xs font-normal text-muted-foreground">
              Due
            </Label>
            {/* datetime-local, not a date: "call back after 2pm" is a real
                follow-up and a date alone cannot say it. */}
            <Input
              id="reminder-due"
              type="datetime-local"
              value={dueAt}
              onChange={(e) => setDueAt(e.target.value)}
              className="h-(--height-control) text-xs"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="reminder-note" className="text-2xs font-normal text-muted-foreground">
              What to do
            </Label>
            <Textarea
              id="reminder-note"
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Send the combined auto + tenant quote"
              className="text-xs"
            />
          </div>
        </div>

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
            size="sm"
            className="h-(--height-control)"
            disabled={!canSave}
            onClick={submit}
          >
            Set follow-up
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
