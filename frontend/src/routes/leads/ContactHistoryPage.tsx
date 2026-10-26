import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  Bell,
  CalendarPlus,
  Check,
  ChevronDown,
  ChevronRight,
  MessageSquare,
  Phone,
  Plus,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import {
  completeReminder,
  createReminder,
  dismissReminder,
  firstValidationMessage,
  getLeadContacts,
  getLeadReminders,
  reminderCalendarUrl,
  updateContactNotes,
  type Contact,
  type Reminder,
} from '@/data/api'
import { isPlaceholderContact } from '@/domain/leadStages'
import LeadNumbers from '@/components/LeadNumbers'
import { AddReminderDialog } from './AddReminderDialog'

// Contact History — the lead's whole timeline, on real data.
//
// Every row is a contact a broker logged themselves: outcome, when, and which
// broker. Nothing about what was SAID, so the note they type is the only thing
// on the row that records what came of it — which is why any row can be
// annotated after the fact.

const OUTCOME_CLASSES: Record<string, string> = {
  Answered: 'text-success bg-success-muted',
  'Call Back Requested': 'text-info bg-info-muted',
  'No Answer': 'text-muted-foreground bg-muted',
  Busy: 'text-warning bg-warning-muted',
  'Wrong Number': 'text-warning bg-warning-muted',
  'Not Interested': 'text-error bg-error-muted',
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  })
}

/**
 * Which day-heading a row belongs under. Brokers think in days, and a full
 * date on every row is the same string repeated down the screen.
 */
function dayLabel(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)

  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  if (same(d, today)) return 'Today'
  if (same(d, yesterday)) return 'Yesterday'
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function formatDue(iso: string): string {
  const d = new Date(iso)
  const day = dayLabel(iso)
  const time = formatTime(iso)
  return day === 'Today' || day === 'Yesterday'
    ? `${day}, ${time}`
    : `${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}, ${time}`
}

/**
 * A bordered panel with a tinted title strip — the same Section the Auto tab
 * builds every one of its groups from (NewPolicyPage.tsx). Copied rather than
 * imported because that one is not exported; a tab that invents its own panel
 * shape is what makes a screen look like it came from somewhere else.
 */
function Section({
  title,
  action,
  children,
}: {
  title: React.ReactNode
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="border-input min-w-0 rounded-md border">
      <div className="bg-muted/60 border-input flex items-center justify-between gap-2 rounded-t-md border-b px-3 py-1.5">
        <h3 className="text-muted-foreground text-2xs font-semibold tracking-wide uppercase">
          {title}
        </h3>
        {action}
      </div>
      <div className="p-3">{children}</div>
    </section>
  )
}

export function ContactHistoryPage({
  leadId,
  leadPhone,
  openReminder = false,
  onReminderOpened,
}: {
  leadId: string
  /** The customer's number, for the Call button. May be the "(not provided)"
   *  placeholder — lead.phone is NOT NULL, so a lead captured without one
   *  carries that literal string. */
  leadPhone: string | null
  /** Open the Add Reminder dialog on arrival — see LeadDetail's prop. */
  openReminder?: boolean
  /** Fired once it has been opened, so the URL param can be cleared. */
  onReminderOpened?: () => void
}) {
  const [contacts, setContacts] = useState<Contact[]>([])
  const [reminders, setReminders] = useState<Reminder[]>([])
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  const [remindersOpen, setRemindersOpen] = useState(false)
  const [addingReminder, setAddingReminder] = useState(false)
  // Which row's note is being edited, or null. The ID rather than a boolean, so
  // only one editor is ever open — two half-typed notes is a way to lose one.
  const [editingNote, setEditingNote] = useState<string | null>(null)
  const [noteDraft, setNoteDraft] = useState('')
  const [savingNote, setSavingNote] = useState(false)

  const load = useCallback(async () => {
    setFailed(false)
    try {
      // In parallel: neither read needs the other, and the timeline is the
      // slower of the two.
      const [c, r] = await Promise.all([getLeadContacts(leadId), getLeadReminders(leadId)])
      setContacts(c)
      setReminders(r)
    } catch {
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [leadId])

  useEffect(() => {
    setLoading(true)
    load()
  }, [load])

  // ARRIVED FROM THE RENEWAL WHATSAPP. The "Set Up Reminder" button promises a
  // form, so opening the lead and stopping there would make the broker hunt
  // for it. Told the caller immediately so the URL param is cleared and a
  // refresh does not reopen a dialog they already dismissed.
  useEffect(() => {
    if (!openReminder) return
    setAddingReminder(true)
    onReminderOpened?.()
  }, [openReminder, onReminderOpened])

  // A lead captured without a phone carries the literal "(not provided)" —
  // a tel: link with nothing to dial is worse than no link at all.
  const callable = Boolean(leadPhone) && !isPlaceholderContact(leadPhone)

  const openReminders = reminders.filter((r) => r.status === 'Open')
  const nextReminder = openReminders[0]

  async function saveNote(contactId: string) {
    setSavingNote(true)
    try {
      const updated = await updateContactNotes(contactId, noteDraft.trim() || null)
      setContacts((list) => list.map((c) => (c.id === contactId ? updated : c)))
      setEditingNote(null)
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not save the note')
    } finally {
      setSavingNote(false)
    }
  }

  async function closeReminder(id: string, how: 'complete' | 'dismiss') {
    try {
      const updated = how === 'complete' ? await completeReminder(id) : await dismissReminder(id)
      setReminders((list) => list.map((r) => (r.id === id ? updated : r)))
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not update the follow-up')
    }
  }

  if (loading) {
    return <p className="text-muted-foreground p-1 text-xs">Loading…</p>
  }

  if (failed) {
    return (
      <p className="text-error p-1 text-xs">
        Could not load this lead&apos;s contact history — reload to try again.
      </p>
    )
  }

  // Rows arrive newest-first from the backend; grouping preserves that order
  // and only inserts a heading when the day changes.
  const days = [...new Set(contacts.map((c) => dayLabel(c.occurred_at)))]

  return (
    // Width capped to match New Policy's own max-w-(--width-form). Left
    // unbounded, the timeline spans the whole pane and each row's timestamp
    // ends up stranded against the far edge.
    // Padded on the container, not on each child: the buttons and both panels
    // then share ONE left edge. Padding only the button row (the first
    // attempt) fixed the buttons and left them out of line with the panels
    // underneath, which is worse than the flush version was.
    <div className="bg-card flex min-w-0 max-w-(--width-form) flex-col gap-2 p-2">
      {/* Actions, above the record of what was already done. gap-1.5 rather
          than gap-1: at gap-1 the Call and WhatsApp pills touch and the pair
          reads as one control. */}
      <div className="flex items-center gap-1.5">
        <Button
          size="sm"
          className="h-(--height-control) gap-1.5"
          disabled={!callable}
          title={callable ? undefined : 'This lead has no phone number'}
          asChild={callable}
        >
          {callable ? (
            <a href={`tel:${leadPhone}`}>
              <Phone className="size-3.5" />
              Call
            </a>
          ) : (
            <>
              <Phone className="size-3.5" />
              Call
            </>
          )}
        </Button>
        <Button variant="outline" size="sm" className="h-(--height-control) gap-1.5">
          <MessageSquare className="size-3.5" />
          WhatsApp
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground ml-auto h-(--height-control) gap-1 text-xs"
          onClick={() => setAddingReminder(true)}
        >
          <Plus className="size-3.5" />
          Follow-up
        </Button>
      </div>

      {/* Every number this customer can also be reached on (driver numbers,
          confirmed secondaries). Renders nothing when there are none beyond
          the primary already shown on the button above. */}
      <LeadNumbers leadId={leadId} />

      {/* Collapsed to its count by default: what is outstanding belongs above
          the history, but expanded it competes with the timeline on a screen
          whose subject IS the timeline. */}
      <Section
        title={
          <button
            type="button"
            onClick={() => setRemindersOpen((o) => !o)}
            className="flex items-center gap-1.5 text-2xs font-semibold tracking-wide uppercase"
          >
            {remindersOpen ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
            <Bell className="size-3" />
            {openReminders.length} Follow-up{openReminders.length === 1 ? '' : 's'}
            {nextReminder && !remindersOpen && (
              <span className="text-muted-foreground normal-case">— next: {nextReminder.note}</span>
            )}
          </button>
        }
        action={
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground h-(--height-control) gap-1 text-2xs"
            onClick={() => setAddingReminder(true)}
          >
            <Plus className="size-3" />
            Add
          </Button>
        }
      >
        {reminders.length === 0 ? (
          <p className="text-muted-foreground text-2xs">
            No follow-ups on this lead yet.
          </p>
        ) : remindersOpen ? (
          <ul className="divide-input divide-y">
            {reminders.map((r) => {
              const closed = r.status !== 'Open'
              return (
                <li
                  key={r.id}
                  className="group/rem flex items-center gap-2.5 py-1.5 first:pt-0 last:pb-0"
                >
                  <span
                    className={cn(
                      'shrink-0 rounded px-1.5 py-0.5 text-2xs font-medium',
                      // A renewal is an insurer's deadline that cannot be
                      // moved — miss it and the customer auto-renewed
                      // elsewhere for a year. It should not look like an
                      // ordinary callback.
                      r.kind === 'Renewal'
                        ? 'text-warning bg-warning-muted'
                        : 'text-muted-foreground bg-muted',
                    )}
                  >
                    {r.kind}
                  </span>

                  <span
                    className={cn(
                      'min-w-0 flex-1 truncate text-xs',
                      closed && 'text-muted-foreground line-through',
                    )}
                  >
                    {r.note}
                  </span>

                  {/* Chased repeatedly is a different conversation from chased
                      once, and nothing else on screen says so. */}
                  {r.attempt_count > 0 && (
                    <span className="text-muted-foreground shrink-0 text-2xs">
                      chased {r.attempt_count}x
                    </span>
                  )}

                  {/* On hover, so a list of follow-ups stays a list rather
                      than a grid of buttons. A closed one keeps only the
                      calendar link: the backend refuses to complete or snooze
                      it, and a button that always errors should not be shown. */}
                  <span className="flex shrink-0 items-center gap-2 opacity-0 transition-opacity group-hover/rem:opacity-100 focus-within:opacity-100">
                    {!closed && (
                      <button
                        type="button"
                        onClick={() => closeReminder(r.id, 'complete')}
                        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-2xs font-medium"
                      >
                        <Check className="size-3" />
                        Done
                      </button>
                    )}
                    <a
                      href={reminderCalendarUrl(r.id)}
                      className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-2xs font-medium"
                    >
                      <CalendarPlus className="size-3" />
                      Calendar
                    </a>
                    {!closed && (
                      <button
                        type="button"
                        onClick={() => closeReminder(r.id, 'dismiss')}
                        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-2xs font-medium"
                      >
                        <X className="size-3" />
                        Dismiss
                      </button>
                    )}
                  </span>

                  <span className="text-muted-foreground w-28 shrink-0 text-right text-2xs">
                    {formatDue(r.due_at)}
                  </span>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="text-muted-foreground text-2xs">
            {openReminders.length} open, {reminders.length - openReminders.length} closed
          </p>
        )}
      </Section>

      <Section title="Contact history">
        {contacts.length === 0 ? (
          <p className="text-muted-foreground text-2xs">
            Nobody has contacted this lead yet. Calls placed from here are recorded and
            filed automatically.
          </p>
        ) : (
          days.map((day) => (
            <div key={day} className="mt-3 first:mt-0">
              <h4 className="text-muted-foreground mb-1 text-2xs font-semibold">{day}</h4>
              <ul className="divide-input divide-y">
                {contacts
                  .filter((c) => dayLabel(c.occurred_at) === day)
                  .map((c) => (
                    <li key={c.id} className="group px-1 py-2">
                      <div className="flex items-start gap-2.5">
                        <ContactIcon />

                        <div className="min-w-0 flex-1">
                          <div className="flex items-baseline gap-2">
                            <span
                              className={cn(
                                'rounded-full px-2 py-0.5 text-2xs font-medium whitespace-nowrap',
                                OUTCOME_CLASSES[c.outcome] ?? 'text-muted-foreground bg-muted',
                              )}
                            >
                              {c.outcome}
                            </span>

                            {/* The stage the lead was in AT THE TIME — the
                                context that makes an old call readable. */}
                            <span className="text-muted-foreground text-2xs">
                              · {c.stage_at_contact}
                            </span>

                            <span className="text-muted-foreground ml-auto shrink-0 text-2xs whitespace-nowrap">
                              {formatTime(c.occurred_at)}
                              {c.broker ? ` · ${c.broker.name.split(' ')[0]}` : ''}
                            </span>
                          </div>

                          {/* The broker's own words — the only thing here that
                              says what came of the call, and only present
                              because someone typed it. Click to edit. */}
                          {c.notes && editingNote !== c.id && (
                            <button
                              type="button"
                              onClick={() => {
                                setEditingNote(c.id)
                                setNoteDraft(c.notes ?? '')
                              }}
                              className="hover:bg-muted/50 -mx-1 mt-1 block w-full rounded px-1 text-left text-xs"
                            >
                              {c.notes}
                            </button>
                          )}

                          {editingNote === c.id ? (
                            <div className="mt-1.5 space-y-1.5">
                              <Textarea
                                autoFocus
                                rows={2}
                                value={noteDraft}
                                onChange={(e) => setNoteDraft(e.target.value)}
                                placeholder="What came of this call?"
                                className="text-xs"
                              />
                              <div className="flex gap-1">
                                <Button
                                  size="sm"
                                  className="h-(--height-control) text-2xs"
                                  disabled={savingNote}
                                  onClick={() => saveNote(c.id)}
                                >
                                  {savingNote ? 'Saving…' : 'Save'}
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-(--height-control) text-2xs"
                                  onClick={() => setEditingNote(null)}
                                >
                                  Cancel
                                </Button>
                              </div>
                            </div>
                          ) : (
                            // Actions on hover: every row carrying a visible
                            // button strip turns a list into a control panel.
                            <div className="mt-1 flex items-center gap-3 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                              {!c.notes && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    setEditingNote(c.id)
                                    setNoteDraft('')
                                  }}
                                  className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-2xs font-medium"
                                >
                                  <Plus className="size-3" />
                                  Add note
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    </li>
                  ))}
              </ul>
            </div>
          ))
        )}
      </Section>

      <AddReminderDialog
        open={addingReminder}
        onOpenChange={setAddingReminder}
        onAdd={async ({ kind, dueAt, note }) => {
          try {
            await createReminder(leadId, {
              // datetime-local gives a local wall-clock string with no zone;
              // the backend stores timestamptz, so it is converted here rather
              // than landing five hours out.
              due_at: new Date(dueAt).toISOString(),
              note,
              kind,
            })
            await load()
          } catch (err) {
            toast.error(firstValidationMessage(err) ?? 'Could not set the follow-up')
          }
        }}
      />
    </div>
  )
}

/**
 * The leading icon. Direction when the call connected, a struck-through
 * handset when it did not, and a speech bubble for a contact that was never a
 * call — the fastest way to tell the two kinds of row apart without spending a
 * text label on every one.
 */
function ContactIcon() {
  return (
    <span className="bg-muted text-muted-foreground mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full">
      <MessageSquare className="size-3" />
    </span>
  )
}
