import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import axios from 'axios'
import { toast } from 'sonner'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { changePassword, firstValidationMessage } from '@/data/api'

// Voluntary change for an already-logged-in broker/admin. Hits the same
// POST /auth/change-password as the forced first-login change, but with no
// pending_token — the request carries the broker's normal Bearer token, so
// the backend identifies them from their session.

const schema = z
  .object({
    currentPassword: z.string().min(1, 'Enter your current password'),
    newPassword: z.string().min(8, 'At least 8 characters'),
    confirmPassword: z.string().min(1, 'Confirm your new password'),
  })
  .refine((v) => v.newPassword === v.confirmPassword, {
    message: "Passwords don't match",
    path: ['confirmPassword'],
  })
  .refine((v) => v.newPassword !== v.currentPassword, {
    message: 'New password must be different from your current one',
    path: ['newPassword'],
  })
type Values = z.infer<typeof schema>

export function ChangePasswordDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [submitting, setSubmitting] = useState(false)
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { currentPassword: '', newPassword: '', confirmPassword: '' },
  })
  const { register, handleSubmit, formState, setError, reset } = form

  function close() {
    reset()
    onOpenChange(false)
  }

  async function onSubmit(values: Values) {
    setSubmitting(true)
    try {
      await changePassword(values.currentPassword, values.newPassword)
      toast.success('Password changed')
      close()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      const detail = firstValidationMessage(err)
      if (status === 401) {
        setError('currentPassword', { message: detail ?? 'Current password is incorrect' })
      } else if (status === 400) {
        setError('newPassword', { message: detail ?? 'New password must be different' })
      } else {
        toast.error('Could not change your password — try again')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => (o ? onOpenChange(true) : close())}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Change password</DialogTitle>
          <DialogDescription>Enter your current password, then choose a new one.</DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="cp-current" className="text-muted-foreground text-xs font-normal">
              Current password <span className="text-error">*</span>
            </Label>
            <Input id="cp-current" type="password" {...register('currentPassword')} />
            {formState.errors.currentPassword && (
              <p className="text-error text-xs">{formState.errors.currentPassword.message}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="cp-new" className="text-muted-foreground text-xs font-normal">
              New password <span className="text-error">*</span>
            </Label>
            <Input id="cp-new" type="password" placeholder="Min. 8 characters" {...register('newPassword')} />
            {formState.errors.newPassword && (
              <p className="text-error text-xs">{formState.errors.newPassword.message}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="cp-confirm" className="text-muted-foreground text-xs font-normal">
              Confirm new password <span className="text-error">*</span>
            </Label>
            <Input id="cp-confirm" type="password" {...register('confirmPassword')} />
            {formState.errors.confirmPassword && (
              <p className="text-error text-xs">{formState.errors.confirmPassword.message}</p>
            )}
          </div>

          <DialogFooter className="pt-2">
            <Button type="button" variant="outline" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? 'Changing…' : 'Change password'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
