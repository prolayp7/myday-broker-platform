import { useEffect, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { toast } from 'sonner'
import axios from 'axios'
import {
  Archive,
  ArrowLeft,
  Ban,
  Check,
  AlertTriangle,
  MoreHorizontal,
  PauseCircle,
  Pencil,
  Plus,
  RotateCcw,
  Trash2,
  UserPlus,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
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
import {
  getBrokerGroups,
  getBrokers,
  getLeadForms,
  createLeadForm,
  deleteLeadForm,
  renameBrokerGroup,
  deleteBrokerGroup,
  archiveBroker,
  moveBroker,
  type BrokerGroup,
  type Broker,
  type LeadForm,
  firstValidationMessage,
} from '@/data/api'
import { pausedForLabel } from '@/domain/broker'

// Group detail / management screen. Real backend data.
//
// From here an admin manages one broker group: rename it, view/add/remove its
// lead-form mapping, manage its brokers, and delete the group — but ONLY once
// it's empty (all brokers moved out first — enforced by the backend via FK,
// mirrored here).
//
// Brokers are never hard-deleted. "Deactivate" moves a broker into the single
// global Recycle Bin group (reuses the normal move-broker mechanism, fully
// reversible). This screen, when it IS the Recycle Bin, shows "Restore"
// (move to a real group) and "Archive permanently" (one-way lock, kept
// forever for compliance) instead of the normal Deactivate/Move actions.

const MAX_BROKERS_PER_GROUP = 10

function groupLabel(g: BrokerGroup): string {
  return g.name || `Group ${g.group_number}`
}

export default function BrokerGroupDetail() {
  const { groupId } = useParams<{ groupId: string }>()
  const navigate = useNavigate()

  const [group, setGroup] = useState<BrokerGroup | null>(null)
  const [allGroups, setAllGroups] = useState<BrokerGroup[]>([])
  const [brokers, setBrokers] = useState<Broker[]>([])
  // A group can have SEVERAL forms routing to it — a split test, seasonal
  // variants, different campaigns feeding one team. lead_form.group_id is
  // deliberately not unique (migrations/0006). This used to keep only the
  // first match, which silently hid every other mapping while it carried on
  // routing leads.
  const [leadForms, setLeadForms] = useState<LeadForm[]>([])
  const [loading, setLoading] = useState(true)

  const [renameOpen, setRenameOpen] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [deleteGroupOpen, setDeleteGroupOpen] = useState(false)
  const [moveTarget, setMoveTarget] = useState<Broker | null>(null)
  const [archiveTarget, setArchiveTarget] = useState<Broker | null>(null)

  async function loadAll() {
    if (!groupId) return
    setLoading(true)
    try {
      const [groups, brokerList, leadForms] = await Promise.all([
        getBrokerGroups(),
        getBrokers(groupId),
        getLeadForms(),
      ])
      setAllGroups(groups)
      setGroup(groups.find((g) => g.id === groupId) ?? null)
      setBrokers(brokerList)
      setLeadForms(leadForms.filter((f) => f.group_id === groupId))
    } catch {
      toast.error('Could not reach the backend — is it running?')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupId])

  if (loading) {
    return (
      <AppShell section="Admin · Lead Management">
        <div className="mx-auto max-w-4xl p-6">
          <p className="text-muted-foreground">Loading…</p>
        </div>
      </AppShell>
    )
  }

  if (!group) {
    return (
      <AppShell section="Admin · Lead Management">
        <div className="mx-auto max-w-4xl p-6">
          <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
            <Link to="/admin/broker-groups">
              <ArrowLeft /> Lead Management
            </Link>
          </Button>
          <p className="text-muted-foreground mt-4">Group not found.</p>
        </div>
      </AppShell>
    )
  }

  // Every broker in a real group counts toward its 10-cap — "deactivating"
  // moves a broker OUT (into the Recycle Bin), so a real group never holds an
  // inactive broker. A plain row count is the headcount, matching the backend
  // count_brokers exactly.
  const headcount = brokers.length
  const isFull = headcount >= MAX_BROKERS_PER_GROUP

  // Only brokers gate deleting a group now. Its form mappings no longer have
  // to be stripped first — they cannot be, since the backend refuses to remove
  // a group's last form — so deleting the group removes them itself (see
  // services/broker_group.delete_broker_group).
  const isEmpty = brokers.length === 0

  const isRecycleBin = group.is_recycle_bin
  const recycleBin = allGroups.find((g) => g.is_recycle_bin)

  async function handleDeactivate(b: Broker) {
    if (!recycleBin) {
      toast.error('No Recycle Bin group is set up')
      return
    }
    try {
      await moveBroker(b.id, recycleBin.id)
      toast(`${b.name} deactivated`)
      loadAll()
    } catch {
      toast.error('Could not deactivate the broker')
    }
  }

  async function handleArchive(b: Broker) {
    try {
      await archiveBroker(b.id)
      toast(`${b.name} archived`)
      setArchiveTarget(null)
      loadAll()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      if (status === 409) toast.error('That broker is already archived')
      else if (status === 400) toast.error('Move this broker to the Recycle Bin before archiving')
      else toast.error('Could not archive the broker')
    }
  }

  async function removeForm(form: LeadForm) {
    try {
      await deleteLeadForm(form.id)
      toast('Form mapping removed')
      loadAll()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      // The backend refuses a group's last form: with none, the group still
      // exists and still has brokers, but nothing routes to it and leads just
      // stop arriving. The Remove button is hidden in that case anyway — this
      // catches any other path to it.
      if (status === 409) {
        toast.error(
          firstValidationMessage(err) ??
            "This is the group's only form — map another before removing it",
        )
      } else toast.error('Could not remove the form mapping')
    }
  }

  return (
    <AppShell section={`Admin · ${groupLabel(group)}`}>
      <div className="mx-auto max-w-4xl space-y-6 p-6">
        <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
          <Link to="/admin/broker-groups">
            <ArrowLeft /> Lead Management
          </Link>
        </Button>

        {/* Header */}
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-semibold">{groupLabel(group)}</h1>
              <Button
                variant="ghost"
                size="icon"
                className="text-muted-foreground size-7"
                onClick={() => setRenameOpen(true)}
              >
                <Pencil className="size-4" />
              </Button>
            </div>
            <p className="text-muted-foreground mt-1 text-sm">
              {isRecycleBin ? (
                'Deactivated brokers, held here until restored or archived.'
              ) : (
                <>
                  Community: {group.community?.name ?? '—'} ·{' '}
                  <span className={isFull ? 'text-warning font-medium' : ''}>
                    {headcount} / {MAX_BROKERS_PER_GROUP} brokers
                  </span>
                </>
              )}
            </p>
          </div>
          {!isRecycleBin && (
            isFull ? (
              <Button disabled title="This group is full — create another group to add more brokers">
                <UserPlus /> Group full
              </Button>
            ) : (
              <Button asChild>
                {/* Carries the group, so the wizard opens on the broker form
                    rather than asking which community and group — the admin
                    is already standing in the answer to both. */}
                <Link to={`/admin/onboard-broker?group=${group.id}`}>
                  <UserPlus /> Onboard Broker
                </Link>
              </Button>
            )
          )}
        </div>

        {/* Lead form mapping — not for the Recycle Bin: leads must never
            route to deactivated brokers (backend rejects it too). */}
        {!isRecycleBin && (
        <section className="space-y-2">
          <h2 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
            {leadForms.length > 1 ? 'Lead forms' : 'Lead form'}
          </h2>
          <div className="rounded-lg border bg-card">
            {leadForms.length === 0 ? (
              <div className="flex items-center justify-between gap-4 p-4">
                <div className="text-muted-foreground flex items-center gap-2 text-sm">
                  <AlertTriangle className="text-warning size-4 shrink-0" />
                  No form mapped — leads won't route to this group yet.
                </div>
                <Button variant="outline" size="sm" onClick={() => setFormOpen(true)}>
                  Map a form
                </Button>
              </div>
            ) : (
              <>
                {leadForms.map((f) => (
                  <div
                    key={f.id}
                    className="flex items-center justify-between gap-4 border-b p-4 last:border-0"
                  >
                    <div className="flex items-start gap-2 text-sm">
                      <Check className="text-success mt-0.5 size-4 shrink-0" />
                      <span>
                        <span className="font-medium">{f.name}</span>{' '}
                        <span className="text-muted-foreground">({f.platform_form_id})</span>
                        <span className="text-muted-foreground block text-xs">
                          Name confirmed with Meta — active when mapped.
                        </span>
                      </span>
                    </div>
                    {/* No Remove on the last one: a group with no form receives
                        nothing, silently. The backend refuses it too. */}
                    {leadForms.length > 1 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-muted-foreground hover:text-error"
                        onClick={() => removeForm(f)}
                      >
                        Remove
                      </Button>
                    )}
                  </div>
                ))}
                <div className="p-3">
                  <Button variant="outline" size="sm" onClick={() => setFormOpen(true)}>
                    <Plus /> Map another form
                  </Button>
                </div>
              </>
            )}
          </div>
        </section>
        )}

        {/* Brokers */}
        <section className="space-y-2">
          <h2 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
            Brokers
          </h2>
          <div className="overflow-hidden rounded-lg border bg-card">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b text-left">
                  <th className="px-4 py-2.5 font-medium">Name</th>
                  <th className="px-4 py-2.5 font-medium">Role</th>
                  <th className="px-4 py-2.5 font-medium">Status</th>
                  <th className="px-4 py-2.5 text-right font-medium">Daily cap</th>
                  <th className="px-4 py-2.5"></th>
                </tr>
              </thead>
              <tbody>
                {isEmpty ? (
                  <tr>
                    <td colSpan={5} className="text-muted-foreground p-8 text-center">
                      No brokers in this group.
                    </td>
                  </tr>
                ) : (
                  brokers.map((b) => (
                    <tr key={b.id} className="border-b last:border-0">
                      <td className="px-4 py-2.5">
                        <Link
                          to={`/admin/broker-groups/${group.id}/brokers/${b.id}`}
                          className="font-medium hover:underline"
                        >
                          {b.name}
                        </Link>
                      </td>
                      <td className="text-muted-foreground px-4 py-2.5 capitalize">{b.role}</td>
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          {b.is_archived ? (
                            <span className="text-muted-foreground inline-flex items-center gap-1 font-medium">
                              <Archive className="size-3" /> Archived
                            </span>
                          ) : (
                            <span className="text-success">Active</span>
                          )}
                          {!b.is_archived && b.is_blocked_by_admin && (
                            <span className="text-error inline-flex items-center gap-1 text-xs font-medium">
                              <Ban className="size-3" /> Blocked
                            </span>
                          )}
                          {/* The broker stopped their OWN leads. Shown beside
                              Blocked, never instead of it — an admin asking
                              "why is this broker getting nothing" needs to
                              know which of the two it is, and they are set by
                              different people for different reasons.

                              Without this the answer is invisible, which is
                              the specific blindness that made the outage
                              take an afternoon to find. */}
                          {!b.is_archived && b.self_paused && (
                            // The SAME red the broker sees on their own
                            // control in the header. One state, one colour —
                            // an admin and a broker looking at "not receiving
                            // leads" should not be reading two different
                            // signals for it.
                            //
                            // WITH THE DURATION, because the pause no longer
                            // clears itself overnight. "Paused" alone cannot
                            // tell an hour apart from a fortnight, and the
                            // fortnight is the one somebody has to act on.
                            // This is the safety net for a forgotten pause:
                            // it has to be legible from the roster, or an
                            // admin would have to open all fifty profiles to
                            // find it. Omitted when the date is unknown —
                            // see pausedForLabel.
                            <span className="text-error inline-flex items-center gap-1 text-xs font-medium">
                              <PauseCircle className="size-3" /> Paused
                              {pausedForLabel(b.self_paused_at) && (
                                <span className="font-normal opacity-80">
                                  · {pausedForLabel(b.self_paused_at)}
                                </span>
                              )}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-2.5 text-right">{b.daily_cap}</td>
                      <td className="px-4 py-2.5 text-right">
                        {b.is_archived ? (
                          <Button variant="ghost" size="icon" className="text-muted-foreground size-7" asChild>
                            <Link to={`/admin/broker-groups/${group.id}/brokers/${b.id}`}>
                              <MoreHorizontal className="size-4" />
                            </Link>
                          </Button>
                        ) : (
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button variant="ghost" size="icon" className="text-muted-foreground size-7">
                                <MoreHorizontal className="size-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem asChild>
                                <Link to={`/admin/broker-groups/${group.id}/brokers/${b.id}`}>
                                  Edit details
                                </Link>
                              </DropdownMenuItem>
                              {isRecycleBin ? (
                                <>
                                  <DropdownMenuItem onClick={() => setMoveTarget(b)}>
                                    <RotateCcw /> Restore to a group
                                  </DropdownMenuItem>
                                  <DropdownMenuItem
                                    className="text-error"
                                    onClick={() => setArchiveTarget(b)}
                                  >
                                    <Archive /> Archive permanently
                                  </DropdownMenuItem>
                                </>
                              ) : (
                                <>
                                  <DropdownMenuItem onClick={() => handleDeactivate(b)}>
                                    Deactivate
                                  </DropdownMenuItem>
                                  <DropdownMenuItem onClick={() => setMoveTarget(b)}>
                                    Move to another group
                                  </DropdownMenuItem>
                                </>
                              )}
                            </DropdownMenuContent>
                          </DropdownMenu>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* Danger zone — delete group. Hidden for the Recycle Bin: it's
            system infrastructure and the backend refuses to delete it. */}
        {!isRecycleBin && (
        <section className="space-y-2">
          <h2 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
            Danger zone
          </h2>
          <div className="border-error/30 flex items-center justify-between gap-4 rounded-lg border bg-card p-4">
            <div className="text-sm">
              <p className="font-medium">Delete this group</p>
              <p className="text-muted-foreground">
                {/* Only brokers block this now — the group's form mappings go
                    with it, and cannot be removed beforehand anyway. */}
                {isEmpty
                  ? 'This group is empty and can be deleted. Its form mappings go with it.'
                  : 'Move all brokers to another group first — a group must be empty to delete.'}
              </p>
            </div>
            <Button
              variant="destructive"
              disabled={!isEmpty}
              onClick={() => setDeleteGroupOpen(true)}
            >
              <Trash2 /> Delete group
            </Button>
          </div>
        </section>
        )}
      </div>

      <RenameDialog
        open={renameOpen}
        current={groupLabel(group)}
        onOpenChange={setRenameOpen}
        onRename={async (name) => {
          try {
            await renameBrokerGroup(group.id, name)
            toast('Group renamed')
            loadAll()
          } catch (err) {
            const status = axios.isAxiosError(err) ? err.response?.status : null
            if (status === 409) toast.error('A group with that name already exists in this community')
            else toast.error('Could not rename the group')
          }
        }}
      />

      <MapFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        onMap={async (platformFormId) => {
          try {
            await createLeadForm(platformFormId, group.id)
            toast('Form mapped')
            loadAll()
          } catch (err) {
            const status = axios.isAxiosError(err) ? err.response?.status : null
            if (status === 422) toast.error(firstValidationMessage(err) ?? 'Meta rejected this form — check the form ID')
            else if (status === 409) toast.error('That form is already mapped to a group')
            else toast.error('Could not map the form')
          }
        }}
      />

      <MoveBrokerDialog
        broker={moveTarget}
        options={allGroups.filter((g) => g.id !== group.id && !g.is_recycle_bin)}
        onOpenChange={(o) => !o && setMoveTarget(null)}
        onMove={async (b, targetGroupId, targetLabel) => {
          try {
            await moveBroker(b.id, targetGroupId)
            toast(`${b.name} moved to ${targetLabel}`)
            setMoveTarget(null)
            loadAll()
          } catch (err) {
            const status = axios.isAxiosError(err) ? err.response?.status : null
            if (status === 409) toast.error('That group is full (10 brokers max)')
            else toast.error('Could not move the broker')
          }
        }}
      />

      <DeleteGroupDialog
        open={deleteGroupOpen}
        groupName={groupLabel(group)}
        onOpenChange={setDeleteGroupOpen}
        onDelete={async () => {
          try {
            await deleteBrokerGroup(group.id)
            toast('Group deleted')
            navigate('/admin/broker-groups')
          } catch {
            toast.error('Could not delete the group')
          }
        }}
      />

      <ArchiveConfirmDialog
        broker={archiveTarget}
        onOpenChange={(o) => !o && setArchiveTarget(null)}
        onConfirm={() => archiveTarget && handleArchive(archiveTarget)}
      />
    </AppShell>
  )
}

function RenameDialog({
  open,
  current,
  onOpenChange,
  onRename,
}: {
  open: boolean
  current: string
  onOpenChange: (o: boolean) => void
  onRename: (name: string) => void
}) {
  const [name, setName] = useState(current)
  return (
    <Dialog open={open} onOpenChange={(o) => { setName(current); onOpenChange(o) }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Rename group</DialogTitle>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label className="text-muted-foreground text-xs font-normal">Group name</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!name.trim()}
            onClick={() => {
              onRename(name.trim())
              onOpenChange(false)
            }}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function MapFormDialog({
  open,
  onOpenChange,
  onMap,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  onMap: (platformFormId: string) => void
}) {
  const [id, setId] = useState('')
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setId('')
        onOpenChange(o)
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Map a lead form</DialogTitle>
          <DialogDescription>
            Paste the form ID exactly from Meta Ads Manager (Instant Forms Library). Meta confirms
            it's real and pulls the form's name automatically.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-muted-foreground text-xs font-normal">Form ID</Label>
            <Input value={id} onChange={(e) => setId(e.target.value)} placeholder="e.g. 811203456789012" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!id.trim()}
            onClick={() => {
              onMap(id.trim())
              onOpenChange(false)
            }}
          >
            Map form
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function MoveBrokerDialog({
  broker,
  options,
  onOpenChange,
  onMove,
}: {
  broker: Broker | null
  options: BrokerGroup[]
  onOpenChange: (o: boolean) => void
  onMove: (b: Broker, targetGroupId: string, targetLabel: string) => void
}) {
  const [targetId, setTargetId] = useState('')
  const target = options.find((g) => g.id === targetId)
  return (
    <Dialog open={!!broker} onOpenChange={(o) => { setTargetId(''); onOpenChange(o) }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Move broker</DialogTitle>
          <DialogDescription>
            {broker && `Move ${broker.name} into another group (subject to that group's 10-broker cap).`}
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
                  {groupLabel(g)} ({g.community?.name ?? '—'}) — {g.broker_count} / {MAX_BROKERS_PER_GROUP}
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
            disabled={!target || !broker}
            onClick={() => broker && target && onMove(broker, target.id, groupLabel(target))}
          >
            Move
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ArchiveConfirmDialog({
  broker,
  onOpenChange,
  onConfirm,
}: {
  broker: Broker | null
  onOpenChange: (o: boolean) => void
  onConfirm: () => void
}) {
  return (
    <Dialog open={!!broker} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Archive {broker?.name}?</DialogTitle>
          <DialogDescription>
            This is permanent. {broker?.name} will be locked read-only forever — no further edits,
            moves, or restores. All their data is kept for compliance.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              onConfirm()
              onOpenChange(false)
            }}
          >
            Archive permanently
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function DeleteGroupDialog({
  open,
  groupName,
  onOpenChange,
  onDelete,
}: {
  open: boolean
  groupName: string
  onOpenChange: (o: boolean) => void
  onDelete: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete group?</DialogTitle>
          <DialogDescription>
            {groupName} will be permanently deleted. This can't be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              onDelete()
              onOpenChange(false)
            }}
          >
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
