import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import {
  firstValidationMessage,
  getMyProfile,
  initialsOf,
  updateMyPhone,
  type MyProfile,
} from '@/data/api'

// The broker's own profile, from GET /my-profile.
//
// WHAT THIS SCREEN IS FOR: a broker answering "what does the system think
// about me". Licence dates, lead caps and their work extension are all facts
// they otherwise have no way to see without asking an admin.
//
// READ-ONLY EXCEPT THE PHONE. Name, licence numbers and caps are all
// ADMIN decisions — a broker editing their own RIBO number or daily cap
// would route around the person whose job it is to set them. Their own
// contact number is the one thing here that is genuinely theirs to correct.
//
// NO COMMISSION FIGURES. The broker's share, the brokerage's and the
// principal's are all deliberately absent — earnings live on My Earnings,
// and the split between the three is not something this screen discusses.
//
// NOT SHOWN AT ALL, on purpose: password_hash and totp_secret (never leave
// the server), failed_login_count and locked_until (security internals), and
// is_blocked_by_admin / blocked_reason — telling a broker why an admin
// blocked them belongs in a conversation with that admin, not in a panel
// they open themselves.
//
// NO SECURITY BLOCK EITHER. Two-factor enrolment and password changes both
// happen elsewhere — the password from this same avatar menu — so restating
// their state here would be a panel that reports on controls it does not
// own, and a broker cannot act on it from this screen anyway.

/**
 * A date the broker reads, not an ISO string.
 *
 * Accepts both shapes the API returns: plain dates (licence and E&O expiry)
 * and timestamps (the pending-cap effective dates). A bare date is pinned to
 * local midnight — `new Date('2026-11-30')` alone parses as UTC and renders
 * as the 29th for anyone west of Greenwich, which is everyone here.
 */
const day = (iso: string | null) => {
  if (!iso) return '—'
  const d = new Date(iso.includes('T') ? iso : `${iso}T00:00:00`)
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleDateString('en-CA', { year: 'numeric', month: 'short', day: 'numeric' })
}

/**
 * How close an expiry is, as something the screen can colour.
 *
 * A LAPSED RIBO LICENCE OR E&O POLICY IS A REGULATORY PROBLEM, not a piece
 * of trivia — a broker writing business on an expired licence is the kind of
 * thing that ends a brokerage. Nothing in the app surfaces these dates
 * today, so this screen is the first place either becomes visible.
 */
function expiryState(iso: string | null): 'expired' | 'soon' | 'ok' {
  if (!iso) return 'ok'
  const at = new Date(iso.includes('T') ? iso : `${iso}T00:00:00`).getTime()
  if (Number.isNaN(at)) return 'ok'
  const days = Math.floor((at - Date.now()) / 86_400_000)
  if (days < 0) return 'expired'
  if (days <= 60) return 'soon'
  return 'ok'
}

export function ProfileDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [profile, setProfile] = useState<MyProfile | null>(null)
  const [failed, setFailed] = useState(false)

  // Editing the phone is a mode, not a separate screen: one field, and the
  // rest of the panel keeps its shape while it is being changed.
  const [editingPhone, setEditingPhone] = useState(false)
  const [phone, setPhone] = useState('')
  const [saving, setSaving] = useState(false)

  // Fetched on OPEN, not on mount: this dialog lives in AppShell, so mounting
  // it would put a request on every screen in the app whether or not anyone
  // ever opens the menu. Re-fetching each time also means a cap or licence
  // an admin changed mid-session is current when the broker looks.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setFailed(false)
    getMyProfile()
      .then((p) => {
        if (cancelled) return
        setProfile(p)
        setPhone(p.phone ?? '')
        setEditingPhone(false)
      })
      .catch(() => !cancelled && setFailed(true))
    return () => {
      cancelled = true
    }
  }, [open])

  async function savePhone() {
    setSaving(true)
    try {
      const updated = await updateMyPhone(phone.trim())
      setProfile(updated)
      setPhone(updated.phone ?? '')
      setEditingPhone(false)
      toast('Phone number updated')
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not save your phone number')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Profile</DialogTitle>
        </DialogHeader>

        {failed ? (
          <p className="text-muted-foreground text-sm">
            Could not load your profile. Close this and try again.
          </p>
        ) : !profile ? (
          <p className="text-muted-foreground text-sm">Loading…</p>
        ) : (
          <ProfileBody
            profile={profile}
            phone={phone}
            setPhone={setPhone}
            editingPhone={editingPhone}
            setEditingPhone={setEditingPhone}
            saving={saving}
            onSave={savePhone}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function ProfileBody({
  profile: p,
  phone,
  setPhone,
  editingPhone,
  setEditingPhone,
  saving,
  onSave,
}: {
  profile: MyProfile
  phone: string
  setPhone: (v: string) => void
  editingPhone: boolean
  setEditingPhone: (v: boolean) => void
  saving: boolean
  onSave: () => void
}) {
  return (
    <>

        {/* Identity, as a header rather than a list: the name and role are
            what a broker checks first, and putting them in the same two-column
            grid as everything else would bury them in it. */}
        <div className="flex items-center gap-3 pb-1">
          {/* Same photo as the header avatar, from the same signed link on
              this profile — a face in the header and initials here would read
              as two different people. */}
          <Avatar className="size-12">
            {p.photo_url && <AvatarImage src={p.photo_url} alt="" />}
            <AvatarFallback className="text-sm">{initialsOf(p.name)}</AvatarFallback>
          </Avatar>
          <div className="min-w-0">
            <div className="truncate font-semibold">{p.name}</div>
            <div className="text-muted-foreground truncate text-xs">{p.email}</div>
            <div className="mt-1 flex items-center gap-1.5">
              <Badge variant="secondary" className="text-2xs">{p.role}</Badge>
              <span className="text-muted-foreground text-2xs">
                {p.group ? `${p.group.name} · Group ${p.group.group_number}` : '—'}
              </span>
            </div>
          </div>
        </div>

        {/* LICENSING FIRST, above contact details and caps. It is the block
            with a deadline attached — everything else here is static. */}
        <Block title="Licensing">
          <Row label="RIBO licence">{p.ribo_license_number ?? '—'}</Row>
          <Row label="Lines of authority">
            {p.lines_of_authority.length ? p.lines_of_authority.join(', ') : '—'}
          </Row>
          <Row label="Resident province">{p.resident_province ?? '—'}</Row>
          <Row label="Licence expires">
            <Expiry iso={p.license_expiry_date} />
          </Row>
          <Row label="E&O cover">
            {day(p.eo_start_date)} – <Expiry iso={p.eo_expiry_date} />
          </Row>
        </Block>

        <Block title="Contact">
          <Row label="Phone">
            {editingPhone ? (
              <div className="flex items-center gap-1.5">
                <Input
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  // Enter saves and Escape abandons, so the row can be
                  // finished without reaching for the mouse.
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && phone.trim()) onSave()
                    if (e.key === 'Escape') {
                      setPhone(p.phone ?? '')
                      setEditingPhone(false)
                    }
                  }}
                  className="h-7 max-w-40 text-xs"
                  autoFocus
                />
                <Button
                  size="sm"
                  className="h-7 text-xs"
                  // The backend requires a non-empty phone, so an empty box
                  // is stopped here rather than sent to be refused.
                  disabled={saving || !phone.trim()}
                  onClick={onSave}
                >
                  {saving ? 'Saving…' : 'Save'}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-xs"
                  disabled={saving}
                  onClick={() => {
                    setPhone(p.phone ?? '')
                    setEditingPhone(false)
                  }}
                >
                  Cancel
                </Button>
              </div>
            ) : (
              <span className="flex items-center gap-2">
                {phone || '—'}
                {/* The ONE editable field on this screen, so it says so
                    rather than relying on the broker discovering it. */}
                <button
                  type="button"
                  onClick={() => setEditingPhone(true)}
                  className="text-primary text-2xs hover:underline"
                >
                  Edit
                </button>
              </span>
            )}
          </Row>
          <Row label="Date of birth">{day(p.date_of_birth)}</Row>
          <Row label="Address">{p.address ?? '—'}</Row>
        </Block>

        <Block title="Lead capacity">
          <Row label="Daily cap">
            {p.daily_cap ?? '—'}
            {/* A pending cap is a fact about the broker's near future, and
                without it a raise agreed with an admin is invisible until the
                morning it takes effect. */}
            {p.pending_daily_cap != null && (
              <span className="text-muted-foreground ml-1.5 text-2xs">
                → {p.pending_daily_cap} on {day(p.pending_daily_cap_effective_at)}
              </span>
            )}
          </Row>
          <Row label="Maximum cap">{p.max_cap ?? '—'}</Row>
          <Row label="Status">{p.availability_status ?? '—'}</Row>
          <Row label="Ready for more">{p.ready_for_more ? 'Yes' : 'No'}</Row>
        </Block>

    </>
  )
}

// ── small shared pieces ───────────────────────────────────────────────────

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t pt-3">
      <h3 className="text-muted-foreground mb-1.5 text-2xs font-semibold tracking-wide uppercase">
        {title}
      </h3>
      <dl className="space-y-1">{children}</dl>
    </section>
  )
}

/** Label left, value right — one line each, so the panel scans vertically. */
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 text-xs">
      <dt className="text-muted-foreground shrink-0">{label}</dt>
      <dd className="min-w-0 text-right">{children}</dd>
    </div>
  )
}

/** An expiry date that says how worried to be about it. */
function Expiry({ iso }: { iso: string | null }) {
  const state = expiryState(iso)
  return (
    <span
      className={cn(
        state === 'expired' && 'text-error font-semibold',
        state === 'soon' && 'text-warning font-medium',
      )}
    >
      {day(iso)}
      {state === 'expired' && ' · expired'}
      {state === 'soon' && ' · expiring soon'}
    </span>
  )
}
