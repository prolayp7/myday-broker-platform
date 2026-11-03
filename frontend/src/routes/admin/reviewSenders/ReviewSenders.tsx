import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { toast } from 'sonner'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  addReviewSender,
  getReviewSenders,
  setReviewSender,
  firstValidationMessage,
  type ReviewSender,
} from '@/data/api'

// Who may send files to the submission review. The worker reads mail from
// these addresses only. A Fairstar address that is missing or switched off is
// told it is not authorised; an outside address gets no reply.
//
// There is no remove. A sender's files stay attached to their address, and
// switching them off stops new mail just the same.

export default function ReviewSenders() {
  const [senders, setSenders] = useState<ReviewSender[]>([])
  const [email, setEmail] = useState('')
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    let cancelled = false
    getReviewSenders()
      .then((rows) => {
        if (!cancelled) setSenders(rows)
      })
      .catch(() => toast.error('Could not load the review senders'))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [])

  function replace(saved: ReviewSender) {
    setSenders((rows) =>
      [...rows.filter((r) => r.id !== saved.id), saved].sort((a, b) =>
        a.email.localeCompare(b.email),
      ),
    )
  }

  async function add(event: FormEvent) {
    event.preventDefault()
    setAdding(true)
    try {
      replace(await addReviewSender(email))
      setEmail('')
      toast('Address saved')
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not save the address')
    } finally {
      setAdding(false)
    }
  }

  async function toggle(sender: ReviewSender, approved: boolean) {
    try {
      replace(await setReviewSender(sender.id, approved))
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not change this address')
    }
  }

  return (
    <AppShell section="Admin · Review senders">
      <div className="mx-auto max-w-xl space-y-6 p-6">
        <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
          <Link to="/admin">
            <ArrowLeft /> Admin
          </Link>
        </Button>

        <Card>
          <CardHeader>
            <CardTitle>Review senders</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            <p className="text-muted-foreground text-sm">
              Only the addresses switched on here can send files for review. Any other
              Fairstar address is told it is not authorised. Outside addresses get no
              reply.
            </p>

            <form onSubmit={add} className="flex items-center gap-2">
              <Input
                type="email"
                aria-label="Email address"
                placeholder="name@fairstarinsurance.ca"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <Button type="submit" disabled={adding || !email.trim()}>
                {adding ? 'Saving…' : 'Save'}
              </Button>
            </form>

            {loading ? (
              <p className="text-muted-foreground text-sm">Loading…</p>
            ) : senders.length === 0 ? (
              <p className="text-muted-foreground text-sm">No addresses yet.</p>
            ) : (
              <ul className="divide-y">
                {senders.map((sender) => (
                  <li key={sender.id} className="flex items-center justify-between gap-3 py-3">
                    <span className="min-w-0 truncate text-sm">{sender.email}</span>
                    <Switch
                      aria-label={`Allow ${sender.email}`}
                      checked={sender.approved}
                      onCheckedChange={(approved) => toggle(sender, approved)}
                    />
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </AppShell>
  )
}
