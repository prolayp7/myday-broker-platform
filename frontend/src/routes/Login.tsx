import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import axios from 'axios'
import { toast } from 'sonner'
import { Info, ShieldCheck } from 'lucide-react'
import QRCode from 'react-qr-code'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuth } from '@/auth/AuthContext'
import { homeRoute, mobileEquivalent } from '@/routes/mobile/isPhone'
import { changePassword, firstValidationMessage, login as loginRequest, verifyLogin } from '@/data/api'

// Screen 1 — sign in. Two steps: password, then TOTP (Google Authenticator/
// Authy) — never SMS, see the auth design decision. A broker with no
// totp_secret yet (first login, or an admin reset it after a lost device)
// gets a QR to scan as part of the SAME step-2 screen; there's no separate
// onboarding-wizard path for this.

// The shared Input and Button are h-9 (36px), which is comfortable with a
// mouse and under Apple's 44px minimum for a finger. Raising them in the
// components themselves would re-space 15 and 24 other files, so this screen
// opts in the way the mobile screens already do — a min-h override per
// element, released back to the component's own height from `sm:` up.
//
// Text size is deliberately not touched: Input is already text-base (16px) on
// mobile, and anything under 16px makes iOS zoom the page on focus and never
// zoom back.
const TOUCH_FIELD = 'min-h-[44px] sm:min-h-0'
const TOUCH_BUTTON = 'min-h-[44px] sm:min-h-9'

const credSchema = z.object({
  email: z.string().trim().min(1, 'Enter your email'),
  password: z.string().min(1, 'Enter your password'),
})
type CredValues = z.infer<typeof credSchema>

const codeSchema = z.object({
  code: z.string().regex(/^\d{6}$/, 'Enter the 6-digit code'),
})
type CodeValues = z.infer<typeof codeSchema>

const newPasswordSchema = z
  .object({
    currentPassword: z.string().min(1, 'Enter your current password'),
    newPassword: z.string().min(8, 'At least 8 characters'),
    confirmPassword: z.string().min(1, 'Confirm your new password'),
  })
  .refine((v) => v.newPassword === v.confirmPassword, {
    message: "Passwords don't match",
    path: ['confirmPassword'],
  })
type NewPasswordValues = z.infer<typeof newPasswordSchema>

type Step =
  | { name: 'credentials' }
  | { name: 'password-change'; pendingToken: string; email: string; password: string }
  | { name: 'code'; pendingToken: string; totpUri: string | null }

export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const { login: setSession } = useAuth()
  const [step, setStep] = useState<Step>({ name: 'credentials' })

  function onSignedIn() {
    toast.success('Signed in')
    // homeRoute(), not a hardcoded '/my-day': a broker signing in on a phone
    // belongs on the mobile companion. PhoneRedirect only fires once per page
    // load and has already run by now, so without this a phone login lands on
    // the desktop CRM and stays there.
    //
    // A requested destination still wins — a bookmarked deep link should
    // survive the login it triggered — but on a phone it is translated to the
    // companion's version first. RequireAuth saves the DESKTOP path it
    // bounced, so without this a phone opening /leads while logged out signed
    // in and landed on the desktop /leads. mobileEquivalent returns null for
    // anything the companion has no screen for (every admin path), leaving
    // those deep links exactly as they were.
    const from = (location.state as { from?: string } | null)?.from
    navigate(mobileEquivalent(from) ?? from ?? homeRoute(), { replace: true })
  }

  return (
    // ONE login for both devices, because there is exactly one copy of the auth
    // logic and it stays that way. Only the SHELL is responsive — a second
    // login screen would mean a security fix applied to one and forgotten in
    // the other, which is the kind of divergence that ends badly.
    //
    // min-h-dvh, not min-h-screen: vh on mobile Safari is the viewport with
    // the URL bar collapsed, so a centred card sits visibly off-centre and the
    // page carries a scroll it does not need.
    <div className="bg-background flex min-h-dvh items-center justify-center overflow-y-auto px-6 py-6 sm:p-4">
      {/* w-full up to 24rem rather than a fixed w-96 (384px), which overflows a
          375px iPhone. On a phone the card drops its border and shadow — a
          floating card on a small screen is a desktop idiom; iOS puts the form
          directly on the background. */}
      <div className="bg-card w-full max-w-sm overflow-hidden rounded-lg border-0 shadow-none sm:border sm:shadow-sm">
        {/* p-0 left the form flush against the card's own edge; with the
            border also dropped on a phone, the fields ran edge to edge with
            nothing but the parent's padding holding them off the screen. */}
        <div className="p-5 sm:p-6">
          {/* Brand header */}
          <div className="mb-5 flex items-center gap-3">
            <img src="/favicon.svg" alt="" className="size-8 rounded" aria-hidden />
            <div>
              <div className="leading-tight font-semibold">MyDay CRM</div>
              <div className="text-muted-foreground text-xs">Sales Management</div>
            </div>
          </div>

          {step.name === 'credentials' && (
            <CredentialsStep
              onResult={(result) => {
                if (result.status === 'password_change_required') {
                  setStep({
                    name: 'password-change',
                    pendingToken: result.pending_token,
                    email: result.email,
                    password: result.password,
                  })
                } else {
                  setStep({ name: 'code', pendingToken: result.pending_token, totpUri: result.totp_uri })
                }
              }}
            />
          )}

          {step.name === 'password-change' && (
            <PasswordChangeStep
              pendingToken={step.pendingToken}
              email={step.email}
              onBack={() => setStep({ name: 'credentials' })}
              onChanged={(pendingToken, totpUri) => setStep({ name: 'code', pendingToken, totpUri })}
            />
          )}

          {step.name === 'code' && (
            <CodeStep
              pendingToken={step.pendingToken}
              totpUri={step.totpUri}
              onBack={() => setStep({ name: 'credentials' })}
              onVerified={(accessToken, refreshToken) => {
                setSession(accessToken, refreshToken)
                onSignedIn()
              }}
            />
          )}
        </div>
      </div>
    </div>
  )
}

type CredentialsResult =
  | { status: 'password_change_required'; pending_token: string; email: string; password: string }
  | { status: 'enrollment_required' | 'totp_required'; pending_token: string; totp_uri: string | null }

function CredentialsStep({ onResult }: { onResult: (result: CredentialsResult) => void }) {
  const [submitting, setSubmitting] = useState(false)
  const [lockedMessage, setLockedMessage] = useState<string | null>(null)
  const form = useForm<CredValues>({
    resolver: zodResolver(credSchema),
    defaultValues: { email: '', password: '' },
  })
  const { register, handleSubmit, formState, setError } = form

  async function onSubmit(values: CredValues) {
    setSubmitting(true)
    setLockedMessage(null)
    try {
      const resp = await loginRequest(values.email, values.password)
      if (resp.status === 'password_change_required') {
        onResult({
          status: 'password_change_required',
          pending_token: resp.pending_token,
          email: values.email,
          password: values.password,
        })
      } else {
        onResult({ status: resp.status, pending_token: resp.pending_token, totp_uri: resp.totp_uri })
      }
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      const detail = firstValidationMessage(err)
      if (status === 403) {
        // Account exists and password was right, but is blocked/archived/
        // deactivated — a different situation from "wrong credentials".
        setLockedMessage(detail ?? 'This account cannot sign in right now.')
      } else if (status === 401) {
        // Covers both a genuinely wrong password AND a locked-out account —
        // the backend returns the identical message for both on purpose
        // (see services/auth.py), so this UI can't and shouldn't try to
        // tell them apart either.
        setError('password', { message: detail ?? 'Invalid email or password' })
      } else {
        toast.error('Could not reach the backend — is it running?')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-3">
      {lockedMessage && (
        <div className="flex items-start gap-2 rounded-md bg-error-muted p-3 text-sm text-error">
          <Info className="mt-0.5 size-4 shrink-0" />
          <span>{lockedMessage}</span>
        </div>
      )}

      <div className="space-y-1.5">
        <Label htmlFor="email" className="text-xs font-normal text-muted-foreground">
          Email <span className="text-error">*</span>
        </Label>
        <Input
          id="email"
          type="email"
          placeholder="you@example.com"
          // Lets the keychain fill the pair, and keeps iOS from
          // auto-capitalising the first letter of an address.
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          className={TOUCH_FIELD}
          {...register('email')}
        />
        {formState.errors.email && (
          <p className="text-xs text-error">{formState.errors.email.message}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="password" className="text-xs font-normal text-muted-foreground">
          Password <span className="text-error">*</span>
        </Label>
        <Input
          id="password"
          type="password"
          placeholder="Password"
          autoComplete="current-password"
          className={TOUCH_FIELD}
          {...register('password')}
        />
        {formState.errors.password && (
          <p className="text-xs text-error">{formState.errors.password.message}</p>
        )}
      </div>

      <Button type="submit" className={`mt-2 w-full ${TOUCH_BUTTON}`} disabled={submitting}>
        {submitting ? 'Signing in…' : 'Log in'}
      </Button>

      <p className="pt-1 text-xs text-muted-foreground">
        Forgot your password? Ask your administrator to reset it.
      </p>
    </form>
  )
}

function PasswordChangeStep({
  pendingToken,
  email,
  onBack,
  onChanged,
}: {
  pendingToken: string
  email: string
  onBack: () => void
  onChanged: (pendingToken: string, totpUri: string | null) => void
}) {
  const [submitting, setSubmitting] = useState(false)
  const form = useForm<NewPasswordValues>({
    resolver: zodResolver(newPasswordSchema),
    defaultValues: { currentPassword: '', newPassword: '', confirmPassword: '' },
  })
  const { register, handleSubmit, formState, setError } = form

  async function onSubmit(values: NewPasswordValues) {
    setSubmitting(true)
    try {
      await changePassword(values.currentPassword, values.newPassword, pendingToken)
      // The password just changed — re-login with the NEW password to get
      // the real next step (enrollment_required or totp_required). The old
      // pending_token is spent; this is a fresh call, not a retry.
      const resp = await loginRequest(email, values.newPassword)
      if (resp.status === 'password_change_required') {
        // Shouldn't happen (must_change_password was just cleared), but
        // guards against a stale/racing state rather than looping forever.
        toast.error('Something went wrong — please log in again')
        onBack()
        return
      }
      toast.success('Password updated')
      onChanged(resp.pending_token, resp.totp_uri)
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      const detail = firstValidationMessage(err)
      if (status === 401) {
        setError('currentPassword', { message: detail ?? 'Current password is incorrect' })
      } else {
        toast.error('Could not reach the backend — is it running?')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-3">
      <div className="flex items-start gap-2 rounded-md bg-info-muted p-3 text-sm text-info">
        <Info className="mt-0.5 size-4 shrink-0" />
        <span>Set your own password to continue — this replaces the one your administrator gave you.</span>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="currentPassword" className="text-xs font-normal text-muted-foreground">
          Current password <span className="text-error">*</span>
        </Label>
        <Input
          id="currentPassword"
          type="password"
          className={TOUCH_FIELD}
          {...register('currentPassword')}
        />
        {formState.errors.currentPassword && (
          <p className="text-xs text-error">{formState.errors.currentPassword.message}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="newPassword" className="text-xs font-normal text-muted-foreground">
          New password <span className="text-error">*</span>
        </Label>
        <Input
          id="newPassword"
          type="password"
          placeholder="Min. 8 characters"
          className={TOUCH_FIELD}
          {...register('newPassword')}
        />
        {formState.errors.newPassword && (
          <p className="text-xs text-error">{formState.errors.newPassword.message}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="confirmPassword" className="text-xs font-normal text-muted-foreground">
          Confirm new password <span className="text-error">*</span>
        </Label>
        <Input
          id="confirmPassword"
          type="password"
          className={TOUCH_FIELD}
          {...register('confirmPassword')}
        />
        {formState.errors.confirmPassword && (
          <p className="text-xs text-error">{formState.errors.confirmPassword.message}</p>
        )}
      </div>

      <Button type="submit" className={`mt-2 w-full ${TOUCH_BUTTON}`} disabled={submitting}>
        {submitting ? 'Updating…' : 'Set password & continue'}
      </Button>

      <div className="flex justify-end pt-1">
        <Button type="button" variant="ghost" size="sm" className={TOUCH_BUTTON} onClick={onBack}>
          Back
        </Button>
      </div>
    </form>
  )
}

function CodeStep({
  pendingToken,
  totpUri,
  onBack,
  onVerified,
}: {
  pendingToken: string
  totpUri: string | null
  onBack: () => void
  onVerified: (accessToken: string, refreshToken: string) => void
}) {
  const [submitting, setSubmitting] = useState(false)
  const form = useForm<CodeValues>({
    resolver: zodResolver(codeSchema),
    defaultValues: { code: '' },
  })
  const { register, handleSubmit, formState, setError } = form
  const isEnrolling = totpUri !== null

  async function onSubmit(values: CodeValues) {
    setSubmitting(true)
    try {
      const session = await verifyLogin(pendingToken, values.code)
      onVerified(session.access_token, session.refresh_token)
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      const detail = firstValidationMessage(err)
      if (status === 401) {
        setError('code', { message: detail ?? 'Incorrect code' })
      } else {
        toast.error('Could not reach the backend — is it running?')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-3">
      {isEnrolling ? (
        <>
          <div className="flex items-start gap-2 rounded-md bg-info-muted p-3 text-sm text-info">
            <ShieldCheck className="mt-0.5 size-4 shrink-0" />
            <span>First time signing in — set up your authenticator app to continue.</span>
          </div>

          <ol className="list-inside list-decimal space-y-1.5 text-sm">
            <li>
              Open <span className="font-medium">Google Authenticator</span> (or Authy) on your phone
            </li>
            <li>Tap + and scan the code below</li>
            <li>Enter the 6-digit code it shows you</li>
          </ol>

          <div className="flex justify-center rounded-md bg-muted p-4">
            <QRCode value={totpUri} size={160} />
          </div>
        </>
      ) : (
        <div className="flex items-start gap-2 rounded-md bg-info-muted p-3 text-sm text-info">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" />
          <span>Enter the 6-digit code from your authenticator app.</span>
        </div>
      )}

      <div className="space-y-1.5">
        <Label htmlFor="code" className="text-xs font-normal text-muted-foreground">
          6-digit code <span className="text-error">*</span>
        </Label>
        <Input
          id="code"
          inputMode="numeric"
          maxLength={6}
          placeholder="000000"
          autoFocus
          // one-time-code lets iOS and Android offer the running code from the
          // authenticator app straight above the keyboard, so the broker taps
          // once instead of switching apps and memorising six digits that
          // expire in thirty seconds.
          autoComplete="one-time-code"
          className={`text-center text-lg tracking-[0.4em] ${TOUCH_FIELD}`}
          {...register('code')}
        />
        {formState.errors.code && (
          <p className="text-xs text-error">{formState.errors.code.message}</p>
        )}
      </div>

      <Button type="submit" className={`mt-2 w-full ${TOUCH_BUTTON}`} disabled={submitting}>
        {submitting ? 'Verifying…' : isEnrolling ? 'Confirm & log in' : 'Verify & continue'}
      </Button>

      <div className="flex justify-end pt-1">
        <Button type="button" variant="ghost" size="sm" className={TOUCH_BUTTON} onClick={onBack}>
          Back
        </Button>
      </div>
    </form>
  )
}
