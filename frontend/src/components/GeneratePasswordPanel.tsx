import { useState } from 'react'
import { Check, KeyRound, TriangleAlert } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { firstValidationMessage, generatePassword } from '@/data/api'

// Issuing a broker's temporary password — the SAME action whether they were
// onboarded a minute ago or forgot their password a year later. There is no
// separate "forgot password" feature; onboarding is simply the first press.
//
// Shared by the onboarding success screen and the broker detail screen so the
// two cannot drift into showing a credential differently.
//
// The password is returned once and stored nowhere readable, so an admin who
// loses this screen presses the button again rather than being stuck.

export function GeneratePasswordPanel({
  brokerId,
  brokerName,
  /** Onboarding already says who was created; the detail screen has not. */
  showName = true,
}: {
  brokerId: string
  brokerName: string
  showName?: boolean
}) {
  const [password, setPassword] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  async function issue() {
    setWorking(true)
    try {
      setPassword(await generatePassword(brokerId))
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not generate a password')
    } finally {
      setWorking(false)
    }
  }

  if (!password) {
    return (
      <div className="space-y-3">
        <p className="text-muted-foreground text-sm">
          {showName ? `${brokerName} cannot log in yet. ` : ''}
          Generating a password also clears their two-factor setup and any
          lockout, so this is the one action to use whether they are new or
          have lost access.
        </p>
        <Button onClick={issue} disabled={working}>
          <KeyRound />
          {working ? 'Generating…' : 'Generate password'}
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="text-success flex items-center gap-2 text-sm font-medium">
        <Check className="size-4" />
        Password generated
      </div>

      <div className="bg-muted flex items-center justify-between gap-3 rounded-lg border p-3">
        <code className="font-mono text-base tracking-wide">{password}</code>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            navigator.clipboard.writeText(password)
            toast('Copied')
          }}
        >
          Copy
        </Button>
      </div>

      {/* Said plainly because it is recoverable but not retrievable: the
          password is hashed the moment it is issued, so leaving this screen
          means generating another one rather than looking it up. */}
      <div className="border-warning/40 bg-warning/10 flex items-start gap-2 rounded-lg border p-3 text-sm">
        <TriangleAlert className="text-warning mt-0.5 size-4 shrink-0" />
        <span className="text-muted-foreground">
          Shown once. Share it before leaving this screen — if you lose it,
          generate another.
        </span>
      </div>

      <p className="text-muted-foreground text-xs">
        {brokerName} will be asked to set their own password and scan a new
        two-factor code on their next login.
      </p>
    </div>
  )
}
