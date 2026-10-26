import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, TriangleAlert } from 'lucide-react'
import { toast } from 'sonner'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  getCommissionRates,
  saveCommissionRates,
  firstValidationMessage,
} from '@/data/api'

// The whole commission configuration: two rates and the broker's share.
//
// The screen this replaces (Insurer Rates, deleted in 0083) held three rates
// per carrier across twenty carriers — sixty numbers, none of which was ever
// set to differ from the others in a way that mattered. These two are the
// entire rate card.
//
// NOTHING IS STORED PER POLICY. Every projection is these two numbers times
// the premiums, computed when a report is opened — which is why changing one
// changes every month, and why the warning below is on the screen rather than
// buried in a comment.

export default function CommissionRates() {
  const [auto, setAuto] = useState('')
  const [property, setProperty] = useState('')
  const [share, setShare] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    getCommissionRates()
      .then((r) => {
        if (cancelled) return
        setAuto(r.auto_rate_pct)
        setProperty(r.property_rate_pct)
        setShare(r.broker_share_pct)
      })
      .catch(() => toast.error('Could not load the commission rates'))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [])

  async function save() {
    setSaving(true)
    try {
      const saved = await saveCommissionRates({
        auto_rate_pct: auto,
        property_rate_pct: property,
        broker_share_pct: share,
      })
      // Take the values back from the server rather than keeping what was
      // typed: the database stores numeric, so "15." and "15.0" both come
      // back as the figure actually held.
      setAuto(saved.auto_rate_pct)
      setProperty(saved.property_rate_pct)
      setShare(saved.broker_share_pct)
      toast('Commission rates saved')
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not save the rates')
    } finally {
      setSaving(false)
    }
  }

  return (
    <AppShell section="Admin · Commission rates">
      <div className="mx-auto max-w-xl space-y-6 p-6">
        <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
          <Link to="/admin">
            <ArrowLeft /> Admin
          </Link>
        </Button>

        <Card>
          <CardHeader>
            <CardTitle>Commission rates</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            {loading ? (
              <p className="text-muted-foreground text-sm">Loading…</p>
            ) : (
              <>
                <div className="space-y-1.5">
                  <label className="text-sm font-medium" htmlFor="auto-rate">
                    Auto rate
                  </label>
                  <div className="flex items-center gap-2">
                    <Input
                      id="auto-rate"
                      type="number"
                      min={0}
                      max={100}
                      step="0.1"
                      value={auto}
                      onChange={(e) => setAuto(e.target.value)}
                      className="w-32"
                    />
                    <span className="text-muted-foreground text-sm">%</span>
                  </div>
                  <p className="text-muted-foreground text-xs">
                    Applied to the auto premium.
                  </p>
                </div>

                <div className="space-y-1.5">
                  <label className="text-sm font-medium" htmlFor="property-rate">
                    Property rate
                  </label>
                  <div className="flex items-center gap-2">
                    <Input
                      id="property-rate"
                      type="number"
                      min={0}
                      max={100}
                      step="0.1"
                      value={property}
                      onChange={(e) => setProperty(e.target.value)}
                      className="w-32"
                    />
                    <span className="text-muted-foreground text-sm">%</span>
                  </div>
                  {/* Condo is named explicitly. It has no premium of its own —
                      it rides on the home columns — so an admin looking for a
                      condo rate needs to be told it is this one, not that the
                      setting is missing. */}
                  <p className="text-muted-foreground text-xs">
                    Applied to home, condo and tenant premiums.
                  </p>
                </div>

                <div className="space-y-1.5">
                  <label className="text-sm font-medium" htmlFor="broker-share">
                    Broker share
                  </label>
                  <div className="flex items-center gap-2">
                    <Input
                      id="broker-share"
                      type="number"
                      min={0}
                      max={100}
                      step="0.1"
                      value={share}
                      onChange={(e) => setShare(e.target.value)}
                      className="w-32"
                    />
                    <span className="text-muted-foreground text-sm">%</span>
                  </div>
                  <p className="text-muted-foreground text-xs">
                    What a broker keeps of the commission. The same for every broker;
                    it is what My Day shows as Commission earned.
                  </p>
                </div>

                {/* On the screen, not in a comment. Nothing is stored per
                    policy, so a rate change moves every figure that has ever
                    been reported — and a number that moves without warning is
                    read as a bug. */}
                <div className="border-warning/30 bg-warning/5 flex items-start gap-2 rounded-lg border p-3 text-sm">
                  <TriangleAlert className="text-warning mt-0.5 size-4 shrink-0" />
                  <span className="text-muted-foreground">
                    Changing a rate changes every projection, including months
                    already reported.
                  </span>
                </div>

                <div className="flex justify-end">
                  <Button onClick={save} disabled={saving || !auto || !property || !share}>
                    {saving ? 'Saving…' : 'Save rates'}
                  </Button>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </AppShell>
  )
}
