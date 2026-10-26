import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import axios from 'axios'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  getBrokers,
  getGroupCapacity,
  reassignLead,
  firstValidationMessage,
  type AdminLead,
  type Broker,
  type BrokerGroup,
} from '@/data/api'

// Screen 2 — the one dialog behind both "Reassign" (an assigned lead) and
// "Assign" (a stuck/unassigned lead). Same backend write either way
// (PATCH /leads/{id}/reassign) — only the copy differs. See
// docs/round-robin-allocation-design.md §9.
//
// The "6 / 10 today" load is ADVISORY, not a gate: an admin move deliberately
// ignores the cap (§3), so a broker already at cap is still selectable — the
// bar just turns amber to say "already busy, pick knowingly". Only blocked or
// archived brokers are truly un-pickable.

// Merge the two real sources: getBrokers (cap, name, blocked/archived) +
// getGroupCapacity (delivered_today). Never invents data — a broker with no
// capacity row (shouldn't happen, but be safe) shows 0 delivered.
interface Candidate {
  id: string
  name: string
  daily_cap: number
  delivered_today: number
  blocked: boolean
  archived: boolean
}

export function ReassignDialog({
  lead,
  groups,
  onClose,
  onReassigned,
}: {
  lead: AdminLead
  groups: BrokerGroup[]
  onClose: () => void
  onReassigned: () => void
}) {
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [loading, setLoading] = useState(false)
  const [pickedId, setPickedId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const isAssigned = !!lead.broker
  // Label for the group this lead lives in — brokers are picked within it.
  // (Cross-group reassignment with a group picker + cross-community warning is
  // the next increment; today the dialog moves within the lead's own group.)
  const groupLabel =
    lead.broker_group?.name ?? groups.find((g) => g.id === lead.group_id)?.name ?? 'This group'

  // Load the group's brokers + live delivered-today when the dialog mounts.
  useEffect(() => {
    setPickedId(null)
    setLoading(true)
    Promise.all([getBrokers(lead.group_id), getGroupCapacity(lead.group_id)])
      .then(([brokers, cap]) => {
        const delivered = new Map(cap.brokers.map((b) => [b.id, b.delivered_today]))
        const merged: Candidate[] = brokers
          .filter((b: Broker) => !b.is_archived) // archived brokers never receive leads
          .map((b: Broker) => ({
            id: b.id,
            name: b.name,
            daily_cap: b.daily_cap,
            delivered_today: delivered.get(b.id) ?? 0,
            blocked: b.is_blocked_by_admin,
            archived: b.is_archived,
          }))
        setCandidates(merged)
      })
      .catch(() => toast.error('Could not load this group’s brokers'))
      .finally(() => setLoading(false))
  }, [lead.group_id])

  // Can't reassign a lead to whoever already has it.
  const options = useMemo(
    () => candidates.filter((c) => c.id !== lead.assigned_broker_id),
    [candidates, lead.assigned_broker_id],
  )

  const pickedName = options.find((o) => o.id === pickedId)?.name

  async function handleConfirm() {
    if (!pickedId) return
    setSaving(true)
    try {
      await reassignLead(lead.id, pickedId)
      toast(isAssigned ? `Lead moved to ${pickedName}` : `Lead assigned to ${pickedName}`)
      onReassigned()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      if (status === 400) toast.error(firstValidationMessage(err) ?? 'That broker can’t receive this lead')
      else if (status === 404) toast.error('This lead no longer exists')
      else toast.error('Could not reassign the lead')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isAssigned ? `Reassign ${lead.name}` : `Assign ${lead.name}`}
          </DialogTitle>
          <DialogDescription>
            {isAssigned
              ? 'Move this lead by hand. It won’t count against the new broker’s daily cap — an admin move is a deliberate override, not a system delivery.'
              : 'This lead is waiting — the system found no eligible broker. Placing it by hand overrides that and delivers it now.'}
          </DialogDescription>
        </DialogHeader>

        {isAssigned && lead.broker && (
          <div className="space-y-1.5">
            <div className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
              Currently with
            </div>
            <div className="bg-secondary rounded-md border px-3 py-2 text-sm font-medium">
              {lead.broker.name}
            </div>
          </div>
        )}

        <div className="space-y-1.5">
          <div className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
            {isAssigned ? 'Move to' : 'Assign to'} · {groupLabel}
          </div>

          {loading ? (
            <p className="text-muted-foreground py-4 text-center text-sm">Loading brokers…</p>
          ) : options.length === 0 ? (
            <p className="text-muted-foreground py-4 text-center text-sm">
              No other brokers in this group.
            </p>
          ) : (
            <div className="max-h-60 space-y-1.5 overflow-y-auto">
              {options.map((c) => {
                const atCap = c.delivered_today >= c.daily_cap
                const pct = c.daily_cap > 0 ? Math.min(100, (c.delivered_today / c.daily_cap) * 100) : 100
                const disabled = c.blocked
                return (
                  <button
                    key={c.id}
                    type="button"
                    disabled={disabled}
                    onClick={() => setPickedId(c.id)}
                    className={cn(
                      'flex w-full items-center gap-2.5 rounded-md border px-3 py-2 text-left transition',
                      disabled && 'cursor-not-allowed opacity-50',
                      !disabled && pickedId === c.id && 'border-primary bg-accent',
                      !disabled && pickedId !== c.id && 'hover:bg-muted',
                    )}
                  >
                    <span
                      className={cn(
                        'size-4 shrink-0 rounded-full border',
                        pickedId === c.id ? 'border-[5px] border-primary' : 'border-input',
                      )}
                    />
                    <span className="flex-1 text-sm font-medium">{c.name}</span>
                    {disabled ? (
                      <span className="bg-error-muted text-error rounded-full px-1.5 py-px text-2xs font-semibold">
                        Blocked
                      </span>
                    ) : (
                      <span className="flex items-center gap-2">
                        <span className="bg-muted h-1.5 w-12 overflow-hidden rounded-full">
                          <span
                            className={cn('block h-full', atCap ? 'bg-warning' : 'bg-success')}
                            style={{ width: `${pct}%` }}
                          />
                        </span>
                        <span
                          className={cn(
                            'w-12 text-right text-xs tabular-nums',
                            atCap ? 'text-warning font-semibold' : 'text-muted-foreground',
                          )}
                        >
                          {c.delivered_today} / {c.daily_cap}
                        </span>
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!pickedId || saving} onClick={handleConfirm}>
            {isAssigned ? 'Reassign' : 'Assign'}
            {pickedName ? ` to ${pickedName}` : ''}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
