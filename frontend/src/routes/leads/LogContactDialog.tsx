import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { toast } from 'sonner'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { STAGES } from '@/domain/stages'
import { logContact } from '@/data/api'

// Log contact = focused modal (SAP: interrupting data-entry task → Dialog).
const schema = z.object({
  outcome: z.string().min(1),
  note: z.string().trim().min(1, 'Note is required'),
  followUp: z.string().optional(),
  stage: z.string().min(1),
})
type Values = z.infer<typeof schema>

const OUTCOMES = [
  { key: 'connected', label: 'Connected' },
  { key: 'noanswer', label: 'No answer' },
  { key: 'wrong', label: 'Wrong number' },
  { key: 'notinservice', label: 'Not in service' },
]

export function LogContactDialog({
  open,
  onOpenChange,
  leadId,
  currentStage,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  leadId: string
  currentStage: string
}) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    values: {
      outcome: 'connected',
      note: '',
      followUp: '',
      stage: currentStage,
    },
  })
  const { register, handleSubmit, setValue, watch, formState } = form

  const onSubmit = async (v: Values) => {
    await logContact(leadId, v)
    onOpenChange(false)
    form.reset()
    toast.success('Saved — activity logged')
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Log contact</DialogTitle>
        </DialogHeader>

        <form
          id="log-contact-form"
          onSubmit={handleSubmit(onSubmit)}
          className="space-y-3"
        >
          <div className="space-y-1.5">
            <Label className="text-xs font-normal text-muted-foreground">Outcome</Label>
            <Select
              value={watch('outcome')}
              onValueChange={(v) => setValue('outcome', v)}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {OUTCOMES.map((o) => (
                  <SelectItem key={o.key} value={o.key}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="note" className="text-xs font-normal text-muted-foreground">Note</Label>
            <Textarea
              id="note"
              rows={3}
              placeholder="Note (required)…"
              {...register('note')}
            />
            {formState.errors.note && (
              <p className="text-xs text-error">{formState.errors.note.message}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="followUp" className="text-xs font-normal text-muted-foreground">Next follow-up</Label>
            <Input id="followUp" type="datetime-local" {...register('followUp')} />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs font-normal text-muted-foreground">Stage</Label>
            <Select
              value={watch('stage')}
              onValueChange={(v) => setValue('stage', v)}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STAGES.map((s) => (
                  <SelectItem key={s.key} value={s.key}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </form>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="log-contact-form">
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
