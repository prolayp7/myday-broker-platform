import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { useParams, Link } from 'react-router-dom'
import { toast } from 'sonner'
import axios from 'axios'
import { useConfirm } from '@/components/ConfirmDialog'
import { BrokerDocumentsSection } from '@/components/BrokerDocumentsSection'
import { GeneratePasswordPanel } from '@/components/GeneratePasswordPanel'
import { Archive, ArrowLeft, Ban, MoreHorizontal, PauseCircle, Pencil, RotateCcw, Trash2 } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { EoTermSummary, MultiSelectField, SelectField, TextField } from '@/routes/leads/fields'
import {
  CANADA_PROVINCE_ITEMS,
  ROLE_ITEMS,
  LINE_OF_AUTHORITY_ITEMS,
  type LineOfAuthority,
} from '@/domain/broker'
import {
  getBrokerGroups,
  getBrokers,
  updateBroker,
  moveBroker,
  blockBroker,
  scheduleBrokerUnpause,
  setBrokerDailyCap,
  setBrokerMaxCap,
  archiveBroker,
  resumeBrokerLeads,
  firstValidationMessage,
  type Broker,
  type BrokerGroup,
} from '@/data/api'
import { pausedForLabel } from '@/domain/broker'

// Broker detail — view & edit an existing broker. Real backend data.
// Reached as /admin/broker-groups/:groupId/brokers/:brokerId from the group
// detail screen. (Creating a broker happens in the Onboard Broker wizard,
// not here.)

function groupLabel(g: BrokerGroup): string {
  return g.name || `Group ${g.group_number}`
}

interface BrokerForm {
  name: string
  email: string
  phone: string
  role: string
  dateOfBirth: string
  address: string
  riboLicenseNumber: string
  linesOfAuthority: LineOfAuthority[]
  residentProvince: string
  licenseExpiryDate: string
  eoStartDate: string
  eoExpiryDate: string
}

// mm-dd-yyyy for display, matching the rest of the app; '—' when unset.
function displayDate(iso: string | null): string {
  if (!iso) return '—'
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  return m ? `${m[2]}-${m[3]}-${m[1]}` : '—'
}

// mm-dd-yyyy for a full ISO timestamp (scheduled-change fields, which carry
// a time component unlike the plain date fields above); '—' when unset.
function displayTimestampDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })
}

export default function BrokerDetail() {
  const { groupId, brokerId } = useParams<{ groupId: string; brokerId: string }>()
  const confirm = useConfirm()

  const [broker, setBroker] = useState<Broker | null>(null)
  const [group, setGroup] = useState<BrokerGroup | null>(null)
  const [allGroups, setAllGroups] = useState<BrokerGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  // The house split — what a broker with no override of their own is paid.
  // Used to fill the edit form and to tell, on save, whether the admin
  // actually moved them off it.
  const [moveOpen, setMoveOpen] = useState(false)
  const [blockOpen, setBlockOpen] = useState(false)
  const [archiveOpen, setArchiveOpen] = useState(false)
  const [unpauseOpen, setUnpauseOpen] = useState(false)
  const [capOpen, setCapOpen] = useState(false)

  async function loadAll() {
    if (!groupId || !brokerId) return
    setLoading(true)
    try {
      const [groups, brokerList] = await Promise.all([
        getBrokerGroups(),
        getBrokers(groupId),
      ])
      setAllGroups(groups)
      setGroup(groups.find((g) => g.id === groupId) ?? null)
      setBroker(brokerList.find((b) => b.id === brokerId) ?? null)
    } catch {
      toast.error('Could not reach the backend — is it running?')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupId, brokerId])

  if (loading) {
    return (
      <AppShell section="Admin · Broker">
        <div className="mx-auto max-w-3xl p-6">
          <p className="text-muted-foreground">Loading…</p>
        </div>
      </AppShell>
    )
  }

  if (!broker || !group) {
    return (
      <AppShell section="Admin · Broker">
        <div className="mx-auto max-w-3xl p-6">
          <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
            <Link to="/admin/broker-groups">
              <ArrowLeft /> Lead Management
            </Link>
          </Button>
          <p className="text-muted-foreground mt-4">Broker not found.</p>
        </div>
      </AppShell>
    )
  }

  const isInRecycleBin = group.is_recycle_bin
  const isArchived = broker.is_archived

  async function scheduleUnpause(effectiveAt: string) {
    if (!broker) return
    try {
      await scheduleBrokerUnpause(broker.id, effectiveAt)
      toast(`${broker.name}'s unpause scheduled — leads resume that day`)
      setUnpauseOpen(false)
      loadAll()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      if (status === 400) toast.error(firstValidationMessage(err) ?? 'This broker isn’t currently blocked')
      else if (status === 422) toast.error(firstValidationMessage(err) ?? 'Pick a valid future date')
      else toast.error('Could not schedule the unpause')
    }
  }

  async function handleResumeLeads() {
    if (!broker) return

    // CONFIRMED, though it is the "switch something ON" direction. Resuming is
    // not consequence-free: it starts leads landing on somebody who may still
    // be on vacation, which is the exact failure the persistent pause exists
    // to prevent. A mis-click here undoes the feature for that broker, and
    // they may not notice for a day.
    const ok = await confirm({
      title: `Switch ${broker.name}'s leads back on?`,
      description:
        'They will start receiving new leads again straight away, including any waiting in their group. Only do this if you know they are back — they stopped their own leads, and nothing turns this on by itself.',
      confirmText: 'Switch back on',
      cancelText: 'Cancel',
    })
    if (!ok) return

    try {
      const result = await resumeBrokerLeads(broker.id)
      if (result.backlog_drained) {
        toast(`${broker.name} is receiving leads again`)
      } else {
        // NOT A ROLLBACK — the resume was saved; only the handover of the
        // leads already waiting failed. Said plainly because the admin is the
        // one person who can go and reassign them.
        toast.warning(
          `${broker.name} is receiving new leads, but the ones already waiting ` +
            'could not be handed out — reassign them from the Leads screen.',
        )
      }
      loadAll()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      if (status === 400) {
        toast.error(
          firstValidationMessage(err) ?? `${broker.name} hasn't stopped their own leads`,
        )
      } else {
        toast.error('Could not switch their leads back on')
      }
    }
  }

  async function handleArchive() {
    if (!broker) return
    try {
      await archiveBroker(broker.id)
      toast(`${broker.name} archived — this is permanent`)
      loadAll()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      if (status === 400) toast.error('Move this broker to the Recycle Bin before archiving')
      else toast.error('Could not archive the broker')
    }
  }

  async function handleMoveToRecycleBin() {
    if (!broker) return
    const recycleBin = allGroups.find((g) => g.is_recycle_bin)
    if (!recycleBin) {
      toast.error('No Recycle Bin group configured')
      return
    }
    // Deactivating is serious — it pulls the broker out of their group and
    // out of round-robin eligibility immediately. Confirm before acting,
    // rather than firing on a single click (matches the app's existing
    // useConfirm pattern, no new dialog invented).
    const ok = await confirm({
      title: `Deactivate ${broker.name}?`,
      description:
        'They’ll be moved to the Recycle Bin and stop receiving leads immediately. This can be reversed later by restoring them to a group.',
      confirmText: 'Deactivate',
      cancelText: 'Cancel',
    })
    if (!ok) return

    try {
      await moveBroker(broker.id, recycleBin.id)
      toast(`${broker.name} deactivated — moved to Recycle Bin`)
      setEditing(false) // was left open on stale data before this fix
      loadAll()
    } catch {
      toast.error('Could not move the broker')
    }
  }

  return (
    <AppShell section={`Admin · ${broker.name}`}>
      <div className="mx-auto max-w-3xl space-y-6 p-6">
        <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
          <Link to={`/admin/broker-groups/${group.id}`}>
            <ArrowLeft /> {groupLabel(group)}
          </Link>
        </Button>

        {/* Header */}
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold">{broker.name}</h1>
              {isArchived && (
                <span className="text-muted-foreground inline-flex items-center gap-1 text-sm font-medium">
                  <Archive className="size-3.5" /> Archived
                </span>
              )}
              {!isArchived && broker.is_blocked_by_admin && (
                <span className="text-error inline-flex items-center gap-1 text-sm font-medium">
                  <Ban className="size-3.5" /> Blocked
                </span>
              )}
              {!isArchived && isInRecycleBin && (
                <span className="text-warning inline-flex items-center gap-1 text-sm font-medium">
                  <Trash2 className="size-3.5" /> In Recycle Bin
                </span>
              )}
            </div>
            <p className="text-muted-foreground mt-1 text-sm capitalize">
              {broker.role} · {groupLabel(group)} ({group.community?.name ?? '—'})
            </p>
            {/* The hardcoded green "Receiving leads" line that used to sit
                here is GONE. It was a mock that always claimed the broker was
                in the rotation, which was survivable while a self-pause lasted
                at most one night — now it would sit in green permanently above
                the red banner below saying the exact opposite, on the same
                screen. A wrong answer is worse than no answer here.

                Not replaced with a computed one: doing it properly means
                mirroring all seven of assign_lead's eligibility conditions,
                and a second half-right copy of that predicate is how the two
                drift apart. The banners below already name every reason a
                broker is not receiving leads. */}
          </div>
          {!editing && !isArchived && (
            <div className="flex items-center gap-2">
              <Button onClick={() => setEditing(true)}>
                <Pencil /> Edit
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="icon">
                    <MoreHorizontal className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => setMoveOpen(true)}>
                    {isInRecycleBin ? (
                      <>
                        <RotateCcw /> Restore to a group
                      </>
                    ) : (
                      'Move to another group'
                    )}
                  </DropdownMenuItem>
                  {!isInRecycleBin && (
                    <DropdownMenuItem onClick={handleMoveToRecycleBin}>
                      <Trash2 /> Deactivate
                    </DropdownMenuItem>
                  )}
                  {broker.is_blocked_by_admin ? (
                    !broker.scheduled_unpause_at && (
                      <DropdownMenuItem onClick={() => setUnpauseOpen(true)}>
                        Schedule unpause…
                      </DropdownMenuItem>
                    )
                  ) : (
                    <DropdownMenuItem variant="destructive" onClick={() => setBlockOpen(true)}>
                      Block broker
                    </DropdownMenuItem>
                  )}
                  {!isArchived && !isInRecycleBin && (
                    <DropdownMenuItem onClick={() => setCapOpen(true)}>
                      Schedule cap change…
                    </DropdownMenuItem>
                  )}
                  {isInRecycleBin && (
                    <DropdownMenuItem variant="destructive" onClick={() => setArchiveOpen(true)}>
                      <Archive /> Archive permanently
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}
        </div>

        {isArchived && (
          <div className="border-border bg-muted/50 flex items-start gap-2 rounded-lg border p-3 text-sm">
            <Archive className="text-muted-foreground mt-0.5 size-4 shrink-0" />
            <span className="text-muted-foreground">
              This broker is archived and permanently locked — kept read-only for compliance.
              {broker.archived_at && ` Archived ${new Date(broker.archived_at).toLocaleDateString()}.`}
            </span>
          </div>
        )}

        {!isArchived && isInRecycleBin && (
          <div className="border-warning/40 bg-warning/10 flex items-start gap-2 rounded-lg border p-3 text-sm">
            <Trash2 className="text-warning mt-0.5 size-4 shrink-0" />
            <span className="text-muted-foreground">
              This broker is in the Recycle Bin — not part of any working group. Restore them to a
              group, or archive permanently.
            </span>
          </div>
        )}

        {!isArchived && broker.is_blocked_by_admin && (
          <div className="border-error/30 bg-error/5 flex items-start gap-2 rounded-lg border p-3 text-sm">
            <Ban className="text-error mt-0.5 size-4 shrink-0" />
            <span>
              <span className="font-medium">Blocked by admin.</span>{' '}
              <span className="text-muted-foreground">
                {broker.blocked_reason || 'No reason given.'}
              </span>
            </span>
          </div>
        )}

        {/* THE BROKER'S OWN PAUSE — its own banner, directly below the admin
            block and never folded into it. The two are set by different people
            for different reasons, a broker can be under both at once, and an
            admin asking "why is this one getting nothing" has to be told which
            it is.

            IT BELONGS AT THE TOP, not only as a row further down, because this
            state persists now: the thing standing between a broker and a
            fortnight of lost commission is an admin seeing it without going
            looking for it.

            The SAME red as the header control and the roster badge. One state,
            one colour. */}
        {!isArchived && broker.self_paused && (
          <div className="border-error/30 bg-error/5 flex items-start justify-between gap-3 rounded-lg border p-3 text-sm">
            <div className="flex items-start gap-2">
              <PauseCircle className="text-error mt-0.5 size-4 shrink-0" />
              <span>
                <span className="font-medium">Not taking leads.</span>{' '}
                <span className="text-muted-foreground">
                  {broker.name.split(' ')[0]} stopped their own leads
                  {pausedForLabel(broker.self_paused_at) === 'today'
                    ? ' today'
                    : pausedForLabel(broker.self_paused_at)
                      ? ` ${pausedForLabel(broker.self_paused_at)} ago`
                      : ''}
                  . This stays off until they, or you, switch it back on.
                </span>
              </span>
            </div>
            {/* The action sits IN the banner rather than in the ⋯ menu: the
                menu is for things an admin goes looking for, and this is a
                thing they react to. */}
            <Button size="sm" variant="outline" className="shrink-0" onClick={handleResumeLeads}>
              Switch back on
            </Button>
          </div>
        )}

        {editing ? (
          <BrokerEditForm
            broker={broker}
            onCancel={() => setEditing(false)}
            onMoveClick={() => setMoveOpen(true)}
            onDeactivateClick={handleMoveToRecycleBin}
            onCapClick={() => setCapOpen(true)}
            onSave={async (values) => {
              try {
                await updateBroker(broker.id, {
                  name: values.name,
                  email: values.email,
                  phone: values.phone,
                  role: values.role,
                  date_of_birth: values.dateOfBirth || null,
                  address: values.address || null,
                  ribo_license_number: values.riboLicenseNumber || null,
                  lines_of_authority: values.linesOfAuthority,
                  resident_province: values.residentProvince,
                  license_expiry_date: values.licenseExpiryDate || null,
                  eo_start_date: values.eoStartDate || null,
                  eo_expiry_date: values.eoExpiryDate || null,
                })
                setEditing(false)
                toast('Broker updated')
                loadAll()
              } catch (err) {
                const status = axios.isAxiosError(err) ? err.response?.status : null
                if (status === 409) toast.error('A broker with that email already exists')
                else if (status === 422) toast.error(firstValidationMessage(err) ?? 'Some fields are invalid')
                else toast.error('Could not update the broker')
              }
            }}
          />
        ) : (
          <BrokerReadView broker={broker} />
        )}

        {/* OUTSIDE the read/edit switch, deliberately. Uploading is its own
            action that saves immediately — it does not belong to the profile
            form's Save, and hiding it behind Edit would suggest it did. It
            also stays reachable while the form is open, which is exactly when
            an admin is looking at the licence fields these document. */}
        <BrokerDocumentsSection brokerId={broker.id} />
      </div>

      <MoveBrokerDialog
        open={moveOpen}
        brokerName={broker.name}
        options={allGroups.filter(
          (g) =>
            g.id !== group.id &&
            !g.is_recycle_bin &&
            // Same-community only — a broker moves within their own community's
            // market (the backend enforces this too; this just keeps invalid
            // destinations out of the picker). Deactivate/Restore use the
            // Recycle Bin via a different path, unaffected by this filter.
            g.community_id === group.community_id,
        )}
        onOpenChange={setMoveOpen}
        onMove={async (targetGroupId, targetLabel) => {
          try {
            await moveBroker(broker.id, targetGroupId)
            toast(`${broker.name} moved to ${targetLabel}`)
            setMoveOpen(false)
            loadAll()
          } catch (err) {
            const status = axios.isAxiosError(err) ? err.response?.status : null
            if (status === 409) toast.error('That group is full (10 brokers max)')
            else toast.error('Could not move the broker')
          }
        }}
      />

      <BlockBrokerDialog
        open={blockOpen}
        brokerName={broker.name}
        onOpenChange={setBlockOpen}
        onBlock={async (reason) => {
          try {
            await blockBroker(broker.id, reason)
            toast(`${broker.name} blocked`)
            setBlockOpen(false)
            loadAll()
          } catch {
            toast.error('Could not block the broker')
          }
        }}
      />

      <ScheduleUnpauseDialog
        open={unpauseOpen}
        brokerName={broker.name}
        onOpenChange={setUnpauseOpen}
        onSchedule={scheduleUnpause}
      />

      <ScheduleCapDialog
        open={capOpen}
        broker={broker}
        onOpenChange={setCapOpen}
        onSaved={() => {
          setCapOpen(false)
          loadAll()
        }}
      />

      <ArchiveConfirmDialog
        open={archiveOpen}
        brokerName={broker.name}
        onOpenChange={setArchiveOpen}
        onConfirm={async () => {
          await handleArchive()
          setArchiveOpen(false)
        }}
      />
    </AppShell>
  )
}

function BlockBrokerDialog({
  open,
  brokerName,
  onOpenChange,
  onBlock,
}: {
  open: boolean
  brokerName: string
  onOpenChange: (o: boolean) => void
  onBlock: (reason: string) => void
}) {
  const [reason, setReason] = useState('')
  return (
    <Dialog open={open} onOpenChange={(o) => { setReason(''); onOpenChange(o) }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Block {brokerName}?</DialogTitle>
          <DialogDescription>
            A block is an admin hold — separate from deactivating. The broker keeps their group and
            status, but is flagged as blocked with the reason below.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label className="text-muted-foreground text-xs font-normal">
            Reason <span className="text-error">*</span>
          </Label>
          <Input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Expired RIBO license"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="destructive" disabled={!reason.trim()} onClick={() => onBlock(reason.trim())}>
            Block broker
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// Earliest selectable day for any scheduled change — the backend rejects
// effective_at unless it's strictly in the future (see schemas/broker.py's
// SetDailyCapRequest/SetMaxCapRequest/ScheduleUnpauseRequest), and every
// scheduled change only ever takes effect at the NEXT reset regardless of
// which future day is picked, so "tomorrow" is also the earliest day that's
// meaningfully different from "right now".
//
// Built from LOCAL date components, never toISOString() — toISOString is UTC,
// and in the Ontario evening (UTC-4/-5) local-tomorrow rendered in UTC lands a
// day late, which would make the date picker skip tomorrow entirely.
function tomorrowIso(): string {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

// A picked yyyy-mm-dd as LOCAL midnight, as an ISO timestamp for the backend.
// Never `new Date(dateStr)` — a date-only string parses as UTC midnight, which
// is ~8 PM the PREVIOUS evening in Ontario, so an admin scheduling "tomorrow"
// after 8 PM would send a timestamp already in the past and get rejected by
// the backend's future-date validator. Local midnight (admin machines run
// Ontario time) is always genuinely in the future for a tomorrow-or-later pick.
function toLocalMidnightIso(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number)
  return new Date(y, m - 1, d).toISOString()
}

function ScheduleUnpauseDialog({
  open,
  brokerName,
  onOpenChange,
  onSchedule,
}: {
  open: boolean
  brokerName: string
  onOpenChange: (o: boolean) => void
  onSchedule: (effectiveAtIso: string) => void
}) {
  const [date, setDate] = useState('')
  return (
    <Dialog open={open} onOpenChange={(o) => { setDate(''); onOpenChange(o) }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Schedule {brokerName}'s unpause</DialogTitle>
          <DialogDescription>
            {brokerName} stays blocked until this date, then starts receiving leads again from that
            morning — the same way a new broker's first day works.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label className="text-muted-foreground text-xs font-normal">
            Unpause from <span className="text-error">*</span>
          </Label>
          <Input type="date" min={tomorrowIso()} value={date} onChange={(e) => setDate(e.target.value)} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!date} onClick={() => onSchedule(toLocalMidnightIso(date))}>
            Schedule unpause
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ScheduleCapDialog({
  open,
  broker,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  broker: Broker
  onOpenChange: (o: boolean) => void
  onSaved: () => void
}) {
  const [dailyCap, setDailyCap] = useState(String(broker.daily_cap))
  const [maxCap, setMaxCap] = useState(String(broker.max_cap))
  const [date, setDate] = useState('')
  const [saving, setSaving] = useState(false)

  async function handleSave() {
    if (!date) return
    // Max cap is the TOTAL delivery ceiling (daily guaranteed + overtime), so
    // it can never sit below the daily cap — a broker with max < daily would
    // silently never qualify for overtime, which reads like a bug to an admin.
    if (Number(maxCap) < Number(dailyCap)) {
      toast.error('Max cap can’t be lower than the daily cap')
      return
    }
    setSaving(true)
    const effectiveAt = toLocalMidnightIso(date)
    try {
      // Both changes are deferred to the SAME future date, one call each —
      // daily_cap and max_cap are independent columns/endpoints on the
      // backend (§6), but sharing one date keeps this dialog simple: an
      // admin scheduling "raise the cap for Monday" almost always means
      // both numbers together, not two separate future dates.
      await Promise.all([
        setBrokerDailyCap(broker.id, Number(dailyCap), effectiveAt),
        setBrokerMaxCap(broker.id, Number(maxCap), effectiveAt),
      ])
      toast(`${broker.name}'s cap change scheduled for ${new Date(date).toLocaleDateString()}`)
      onSaved()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      if (status === 422) toast.error(firstValidationMessage(err) ?? 'Some fields are invalid')
      else toast.error('Could not schedule the cap change')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setDailyCap(String(broker.daily_cap))
        setMaxCap(String(broker.max_cap))
        setDate('')
        onOpenChange(o)
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Schedule a cap change for {broker.name}</DialogTitle>
          <DialogDescription>
            Cap changes never take effect the same day — they apply starting the date you choose, so
            today's already-delivered leads are never retroactively over cap.
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label className="text-muted-foreground text-xs font-normal">Daily cap (max 25)</Label>
            <Input type="number" min={0} max={25} value={dailyCap} onChange={(e) => setDailyCap(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label className="text-muted-foreground text-xs font-normal">Max cap — overtime (max 25)</Label>
            <Input type="number" min={0} max={25} value={maxCap} onChange={(e) => setMaxCap(e.target.value)} />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label className="text-muted-foreground text-xs font-normal">
            Effective from <span className="text-error">*</span>
          </Label>
          <Input type="date" min={tomorrowIso()} value={date} onChange={(e) => setDate(e.target.value)} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!date || saving} onClick={handleSave}>
            Schedule change
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ArchiveConfirmDialog({
  open,
  brokerName,
  onOpenChange,
  onConfirm,
}: {
  open: boolean
  brokerName: string
  onOpenChange: (o: boolean) => void
  onConfirm: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Archive {brokerName} permanently?</DialogTitle>
          <DialogDescription>
            This cannot be undone. {brokerName}'s record and all their data stay in the system
            forever for compliance, but the record becomes permanently read-only — no more edits,
            moves, or restoring.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm}>
            <Archive /> Archive permanently
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * Issuing a broker a password from their own screen.
 *
 * CONFIRM FIRST, unlike the onboarding screen where the panel is shown
 * directly. There the broker has no password to lose; here they may be
 * working fine, and this replaces their password AND their 2FA. A misclick
 * would lock them out of their own account with no way back except an admin
 * relaying a new password.
 */
function BrokerAccessSection({
  brokerId,
  brokerName,
}: {
  brokerId: string
  brokerName: string
}) {
  const confirm = useConfirm()
  const [confirmed, setConfirmed] = useState(false)

  if (confirmed) {
    return <GeneratePasswordPanel brokerId={brokerId} brokerName={brokerName} showName={false} />
  }

  return (
    <div className="flex items-end justify-between gap-4">
      <div className="text-xs">
        <p className="text-muted-foreground">
          Use this when {brokerName} cannot sign in — a forgotten password, or
          a lost phone holding their two-factor code.
        </p>
      </div>
      <button
        type="button"
        onClick={async () => {
          // Names both things it replaces. An admin reaching for this because
          // of a forgotten password may not realise it also clears 2FA, and
          // finding that out afterwards is a second phone call.
          const ok = await confirm({
            title: `Generate a new password for ${brokerName}?`,
            description:
              'Their current password stops working immediately, and their two-factor setup is cleared — they will scan a new code on their next login.',
            confirmText: 'Generate',
            cancelText: 'Cancel',
          })
          if (ok) setConfirmed(true)
        }}
        className="border-input shrink-0 rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-muted"
      >
        Generate password…
      </button>
    </div>
  )
}

function ReadRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-0.5">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="text-sm">{value || '—'}</p>
    </div>
  )
}

function ReadSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">{title}</h2>
      <div className="grid grid-cols-1 gap-4 rounded-lg border bg-card p-4 sm:grid-cols-2">
        {children}
      </div>
    </section>
  )
}

function BrokerReadView({
  broker,
}: {
  broker: Broker
  /** What a broker with no override of their own is paid — so the profile can
   *  show real numbers instead of blanks for the ordinary case. */
}) {
  return (
    <div className="space-y-6">
      <ReadSection title="General information">
        <ReadRow label="Name" value={broker.name} />
        <ReadRow label="Email" value={broker.email} />
        <ReadRow label="Phone" value={broker.phone} />
        <ReadRow label="Role" value={broker.role} />
        <ReadRow label="Date of birth" value={displayDate(broker.date_of_birth)} />
        <ReadRow label="Address" value={broker.address ?? ''} />
      </ReadSection>

      <ReadSection title="Licensing & compliance">
        <ReadRow label="RIBO license #" value={broker.ribo_license_number ?? ''} />
        <ReadRow label="Lines of authority" value={broker.lines_of_authority.join(', ')} />
        <ReadRow label="Resident province" value={broker.resident_province} />
        <ReadRow label="License expiry" value={displayDate(broker.license_expiry_date)} />
        <ReadRow label="E&O start" value={displayDate(broker.eo_start_date)} />
        <ReadRow label="E&O expiry" value={displayDate(broker.eo_expiry_date)} />
      </ReadSection>


      <ReadSection title="Operational">
        {/* Read-only — no action triggers here. Cap changes are only
            reachable via Edit (its own "Round robin" section) or the ⋯ menu;
            this view is purely informational. */}
        <ReadRow
          label="Daily cap"
          value={
            broker.pending_daily_cap != null
              ? `${broker.daily_cap} → ${broker.pending_daily_cap} from ${displayTimestampDate(broker.pending_daily_cap_effective_at)}`
              : String(broker.daily_cap)
          }
        />
        <ReadRow
          label="Max cap (overtime)"
          value={
            broker.pending_max_cap != null
              ? `${broker.max_cap} → ${broker.pending_max_cap} from ${displayTimestampDate(broker.pending_max_cap_effective_at)}`
              : String(broker.max_cap)
          }
        />
        <ReadRow label="Ready for overtime today" value={broker.ready_for_more ? 'Yes' : 'No'} />
        {/* Beside "Ready for overtime today" on purpose: the two flags a
            broker owns themselves, read together — one expires with the day,
            this one does not. */}
        <ReadRow
          label="Stopped their own leads"
          value={
            broker.self_paused
              ? pausedForLabel(broker.self_paused_at) === 'today'
                ? 'Yes — since today'
                : pausedForLabel(broker.self_paused_at)
                  ? `Yes — ${pausedForLabel(broker.self_paused_at)} ago`
                  : 'Yes'
              : 'No'
          }
        />
        {broker.scheduled_unpause_at && (
          <ReadRow label="Unpause scheduled" value={displayTimestampDate(broker.scheduled_unpause_at)} />
        )}
        {broker.eligible_from && (
          <ReadRow label="Starts receiving leads" value={displayTimestampDate(broker.eligible_from)} />
        )}

        {/* MOCK — every admin action, inside Operational, not a separate
            section. DISABLED here on purpose: this is the read-only view —
            click Edit to enable these (they become live in BrokerEditForm's
            "Round robin" section below). Same 5 real actions already in the
            ⋯ menu today: Move, Deactivate, Block/Unpause, Schedule cap
            change, Archive. */}
        <div className="col-span-full flex flex-wrap gap-2 border-t pt-3">
          <button type="button" disabled className="border-input text-muted-foreground cursor-not-allowed rounded-md border px-3 py-1.5 text-sm font-medium opacity-50">
            Move to another group
          </button>
          <button type="button" disabled className="border-input text-muted-foreground cursor-not-allowed rounded-md border px-3 py-1.5 text-sm font-medium opacity-50">
            Deactivate
          </button>
          <button type="button" disabled className="border-input text-muted-foreground cursor-not-allowed rounded-md border px-3 py-1.5 text-sm font-medium opacity-50">
            Block broker
          </button>
          <button type="button" disabled className="border-input text-muted-foreground cursor-not-allowed rounded-md border px-3 py-1.5 text-sm font-medium opacity-50">
            Schedule cap change…
          </button>
          <button type="button" disabled className="border-error text-error cursor-not-allowed rounded-md border px-3 py-1.5 text-sm font-medium opacity-50">
            Archive permanently
          </button>
        </div>
      </ReadSection>
    </div>
  )
}

function BrokerEditForm({
  broker,
  onCancel,
  onMoveClick,
  onDeactivateClick,
  onCapClick,
  onSave,
}: {
  broker: Broker
  /** What a broker with no override of their own is paid. Fills the form when
   *  this broker is on the standard terms, and tells the save whether the
   *  admin actually moved them off it. */
  onCancel: () => void
  onMoveClick: () => void
  onDeactivateClick: () => void
  onCapClick: () => void
  onSave: (values: BrokerForm) => void
}) {
  const { control, handleSubmit } = useForm<BrokerForm>({
    defaultValues: {
      name: broker.name, email: broker.email, phone: broker.phone, role: broker.role,
      dateOfBirth: broker.date_of_birth ?? '', address: broker.address ?? '',
      riboLicenseNumber: broker.ribo_license_number ?? '',
      linesOfAuthority: broker.lines_of_authority as LineOfAuthority[],
      residentProvince: broker.resident_province,
      licenseExpiryDate: broker.license_expiry_date ?? '',
      eoStartDate: broker.eo_start_date ?? '', eoExpiryDate: broker.eo_expiry_date ?? '',
      // This broker's own split if they have one, otherwise the house split.
      // Null on the broker row means "on the standard terms", so the form
      // shows what they are ACTUALLY paid rather than three empty boxes that
      // read as "no commission".
    },
  })

  // Watched so the button enables and disables as the admin types.

  return (
    <form onSubmit={handleSubmit(onSave)} className="space-y-6">
      <section className="space-y-3">
        <h2 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
          General information
        </h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField control={control} name="name" label="Name" required />
          <TextField control={control} name="email" label="Email" type="email" required />
          <TextField control={control} name="phone" label="Phone" type="tel" required />
          <SelectField control={control} name="role" label="Role" items={ROLE_ITEMS} required />
          <TextField control={control} name="dateOfBirth" label="Date of birth" type="date" />
          <TextField control={control} name="address" label="Address" />
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
          Licensing & compliance
        </h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField control={control} name="riboLicenseNumber" label="RIBO license #" />
          <MultiSelectField
            control={control}
            name="linesOfAuthority"
            label="Lines of authority"
            items={LINE_OF_AUTHORITY_ITEMS}
          />
          <SelectField
            control={control}
            name="residentProvince"
            label="Resident province"
            items={CANADA_PROVINCE_ITEMS}
          />
          <TextField control={control} name="licenseExpiryDate" label="License expiry" type="date" />
          <TextField control={control} name="eoStartDate" label="E&O start" type="date" />
          <TextField control={control} name="eoExpiryDate" label="E&O expiry" type="date" />
        </div>
        <EoTermSummary control={control} startName="eoStartDate" endName="eoExpiryDate" />
      </section>


      {/* Sign-in access. The ONE action that issues a broker a password —
          the same one onboarding uses, so recovering a forgotten password is
          not a separate feature with its own code path.

          Behind a confirm because it invalidates a WORKING broker's password
          and 2FA: a misclick locks someone out of their own account until an
          admin relays a new password to them. */}
      <section className="space-y-3">
        <h2 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
          Sign-in access
        </h2>
        <BrokerAccessSection brokerId={broker.id} brokerName={broker.name} />
      </section>

      {/* Daily cap / max cap are NOT plain editable fields here — they're
          system-managed and only ever change via the deferred "Schedule cap
          change…" flow (effective a future date, never mid-day). See
          backend/docs/round-robin-allocation-design.md §6. Both caps are set
          together in ONE dialog (ScheduleCapDialog enforces max ≥ daily), so
          there's ONE trigger here, opening the same dialog the ⋯ menu uses. */}
      <section className="space-y-3">
        <h2 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
          Round robin
        </h2>
        <div className="flex items-end justify-between gap-4">
          <div className="grid flex-1 grid-cols-2 gap-4">
            <div className="space-y-1">
              <div className="text-muted-foreground text-xs">Daily cap</div>
              <div className="text-sm font-medium">
                {broker.daily_cap}
                {broker.pending_daily_cap != null && (
                  <span className="text-muted-foreground ml-1 text-xs font-normal">
                    → {broker.pending_daily_cap} from{' '}
                    {displayTimestampDate(broker.pending_daily_cap_effective_at)}
                  </span>
                )}
              </div>
            </div>
            <div className="space-y-1">
              <div className="text-muted-foreground text-xs">Max cap (overtime)</div>
              <div className="text-sm font-medium">
                {broker.max_cap}
                {broker.pending_max_cap != null && (
                  <span className="text-muted-foreground ml-1 text-xs font-normal">
                    → {broker.pending_max_cap} from{' '}
                    {displayTimestampDate(broker.pending_max_cap_effective_at)}
                  </span>
                )}
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={onCapClick}
            className="border-input shrink-0 rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-muted"
          >
            Schedule cap change…
          </button>
        </div>

        {/* Move is wired to the REAL MoveBrokerDialog (same one the ⋯ menu
            uses) — reused here, not rebuilt. The other 3 actions are still
            mock placeholders, wired one at a time next. */}
        <div className="flex flex-wrap gap-2 border-t pt-3">
          <button
            type="button"
            onClick={onMoveClick}
            className="border-input rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-muted"
          >
            Move to another group
          </button>
          <button
            type="button"
            onClick={onDeactivateClick}
            className="border-input rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-muted"
          >
            Deactivate
          </button>
          <button type="button" className="border-input rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-muted">
            Block broker
          </button>
          <button type="button" className="border-error text-error rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-error/5">
            Archive permanently
          </button>
        </div>
      </section>

      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit">Save changes</Button>
      </div>
    </form>
  )
}

function MoveBrokerDialog({
  open,
  brokerName,
  options,
  onOpenChange,
  onMove,
}: {
  open: boolean
  brokerName: string
  options: BrokerGroup[]
  onOpenChange: (o: boolean) => void
  onMove: (targetGroupId: string, targetLabel: string) => void
}) {
  const [targetId, setTargetId] = useState('')
  const target = options.find((g) => g.id === targetId)
  return (
    <Dialog open={open} onOpenChange={(o) => { setTargetId(''); onOpenChange(o) }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Move broker</DialogTitle>
          <DialogDescription>
            Move {brokerName} into another group (subject to that group's 10-broker cap).
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label className="text-muted-foreground text-xs font-normal">Destination group</Label>
          {options.length === 0 ? (
            <p className="text-muted-foreground text-sm">No other groups exist yet.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {options.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  onClick={() => setTargetId(g.id)}
                  className={
                    'rounded-lg border px-3 py-2 text-left text-sm transition ' +
                    (targetId === g.id ? 'border-primary bg-primary text-primary-foreground' : 'hover:bg-muted')
                  }
                >
                  {groupLabel(g)} ({g.community?.name ?? '—'}) — {g.broker_count} / 10
                </button>
              ))}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!target}
            onClick={() => target && onMove(target.id, groupLabel(target))}
          >
            Move
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
