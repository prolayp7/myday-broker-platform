import { useCallback, useEffect, useRef, useState } from 'react'
import { Copy, Download, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useAuth } from '@/auth/AuthContext'
import {
  deleteMvrPull,
  getMvrPulls,
  setMvrReportPassword,
  updateMvrPull,
  uploadMvrs,
  type MvrMonth,
  type MvrPull,
} from '@/data/api'
import { MvrTable } from './MvrTable'
import { apiDetail, downloadRegisterCsv, longDate, registerMonths } from './mvrFormat'

// The MVR register: every MVR pulled, as the MTO "Records of Licensed
// Information" log. The approved wireframe is the spec (2026-10-20).
//
// ONE SCREEN FOR BOTH ROLES. A broker sees their own lines with the licence
// masked; an admin sees everyone's in full, a Broker filter, and the
// head-office link. The backend decides what each gets — the screen only
// decides what controls to show.
//
// DROP ANYWHERE. Brokers save the MVR from the MTO portal and drag it in; the
// whole page is the drop target, with the Upload button for those who would
// rather pick the file. Both dropdowns default on the backend (New Business,
// MVR), so a drop needs no other input.

const months = registerMonths()

export default function MvrLog() {
  const { broker: session } = useAuth()
  const isAdmin = session?.role === 'admin'

  const [month, setMonth] = useState(months[0].value)
  const [brokerId, setBrokerId] = useState('')
  const [data, setData] = useState<MvrMonth | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [toDelete, setToDelete] = useState<MvrPull | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const load = useCallback(() => {
    let cancelled = false
    setLoading(true)
    setFailed(false)
    getMvrPulls(month, brokerId || undefined)
      .then((d) => !cancelled && setData(d))
      .catch(() => {
        if (cancelled) return
        setData(null)
        setFailed(true)
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [month, brokerId])

  useEffect(() => load(), [load])

  const upload = useCallback(
    async (fileList: FileList | File[]) => {
      const files = [...fileList].filter(
        (f) => f.type === 'application/pdf' || /\.pdf$/i.test(f.name),
      )
      if (!files.length) {
        toast.error('Only MVR PDFs can be dropped here')
        return
      }
      setUploading(true)
      try {
        const results = await uploadMvrs(files)
        const logged = results.flatMap((r) => r.pulls)
        const already = results.filter((r) => r.status === 'already_logged')
        const unreadable = results.filter((r) => r.status === 'unreadable')
        if (logged.length) {
          toast.success(
            logged.length === 1
              ? `MVR logged · ${logged[0].driver_name ?? logged[0].licence_masked}`
              : `${logged.length} MVRs logged`,
          )
        }
        already.forEach((r) => toast.info(`${r.file_name}: already in the log`))
        unreadable.forEach((r) => toast.error(`${r.file_name}: ${r.detail ?? 'could not be read'}`))
        // Lines land in the month they were REQUESTED, which may not be the
        // one on screen. Jump there when everything went to one other month,
        // so a broker does not drop a file and see nothing change.
        const landed = new Set(logged.map((p) => p.requested_on.slice(0, 7)))
        if (landed.size === 1 && !landed.has(month)) {
          const [m] = landed
          if (months.some((x) => x.value === m)) setMonth(m)
        } else {
          load()
        }
      } catch (err) {
        toast.error(apiDetail(err, 'The MVRs could not be uploaded'))
      } finally {
        setUploading(false)
      }
    },
    [month, load],
  )

  // The whole window is the drop target. dragenter/leave fire for every child
  // crossed, so a depth counter — not a boolean — decides when the overlay goes.
  useEffect(() => {
    let depth = 0
    const hasFiles = (e: DragEvent) => !!e.dataTransfer?.types.includes('Files')
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth++
      setDragging(true)
    }
    const over = (e: DragEvent) => hasFiles(e) && e.preventDefault()
    const leave = () => {
      depth = Math.max(0, depth - 1)
      if (depth === 0) setDragging(false)
    }
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth = 0
      setDragging(false)
      if (e.dataTransfer?.files.length) upload(e.dataTransfer.files)
    }
    window.addEventListener('dragenter', enter)
    window.addEventListener('dragover', over)
    window.addEventListener('dragleave', leave)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragenter', enter)
      window.removeEventListener('dragover', over)
      window.removeEventListener('dragleave', leave)
      window.removeEventListener('drop', drop)
    }
  }, [upload])

  async function change(pull: MvrPull, changes: Parameters<typeof updateMvrPull>[1]) {
    // Shown at once, put back if the save fails — a dropdown that waits on
    // the network before moving reads as one that did not take the click.
    const before = data
    setData((d) => d && { ...d, pulls: d.pulls.map((p) => (p.id === pull.id ? { ...p, ...changes } : p)) })
    try {
      await updateMvrPull(pull.id, changes)
      toast.success('Saved')
    } catch (err) {
      setData(before)
      toast.error(apiDetail(err, 'That change was not saved'))
    }
  }

  async function confirmDelete() {
    const pull = toDelete
    if (!pull) return
    setToDelete(null)
    try {
      await deleteMvrPull(pull.id)
      setData((d) => d && { ...d, pulls: d.pulls.filter((p) => p.id !== pull.id) })
      toast.success(`Deleted · ${pull.driver_name ?? pull.licence_masked}`)
    } catch (err) {
      toast.error(apiDetail(err, 'That MVR was not deleted'))
    }
  }

  const label = months.find((m) => m.value === month)?.label ?? month
  const brokerName = data?.brokers.find((b) => b.broker_id === brokerId)?.name

  return (
    <AppShell section="MVR Log">
      <div className="mx-auto w-full max-w-screen-2xl space-y-4 p-6">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <label className="text-muted-foreground flex items-center gap-2">
            Month
            <select
              value={month}
              onChange={(e) => {
                setData(null)
                setMonth(e.target.value)
              }}
              className="border-input text-foreground rounded border bg-transparent px-2 py-1 outline-none"
            >
              {months.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>

          {isAdmin && (
            <label className="text-muted-foreground flex items-center gap-2">
              Broker
              <select
                value={brokerId}
                onChange={(e) => {
                  setData(null)
                  setBrokerId(e.target.value)
                }}
                className="border-input text-foreground rounded border bg-transparent px-2 py-1 outline-none"
              >
                <option value="">All brokers</option>
                {data?.brokers.map((b) => (
                  <option key={b.broker_id} value={b.broker_id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          {isAdmin && (
            <Button
              size="sm"
              variant="outline"
              className="ml-auto"
              disabled={!data?.pulls.length}
              onClick={() =>
                data && downloadRegisterCsv(data.pulls, `MVR register ${month}${brokerName ? ` ${brokerName}` : ''}.csv`)
              }
            >
              <Download className="size-3.5" /> Download Excel
            </Button>
          )}
        </div>

        <div className="bg-card flex flex-wrap items-center justify-end gap-3 rounded border border-dashed px-4 py-4">
          <span className="text-muted-foreground text-sm">
            {uploading ? 'Reading MVRs…' : 'Drop MVR PDFs anywhere on this page, or'}
          </span>
          <input
            ref={fileInput}
            type="file"
            accept="application/pdf"
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files?.length) upload(e.target.files)
              e.target.value = ''
            }}
          />
          <Button size="sm" disabled={uploading} onClick={() => fileInput.current?.click()}>
            <Upload className="size-3.5" /> Upload MVR
          </Button>
        </div>

        <div className="bg-card overflow-hidden rounded border">
          <div className="flex items-baseline justify-between gap-4 border-b px-4 py-3">
            <div>
              <div className="font-semibold">{label}</div>
              <div className="text-muted-foreground text-xs">
                {isAdmin
                  ? brokerName
                    ? `MVRs pulled by ${brokerName}`
                    : 'MVRs pulled by all brokers'
                  : 'MVRs you pulled'}
              </div>
            </div>
            {/* An em dash until the count is known — 0 is a statement. */}
            <div className="text-lg font-semibold tabular-nums">{data ? data.pulls.length : '—'}</div>
          </div>

          {loading ? (
            <p className="text-muted-foreground p-8 text-center text-sm">Loading…</p>
          ) : failed ? (
            <p className="text-muted-foreground p-8 text-center text-sm">Could not load this month.</p>
          ) : !data?.pulls.length ? (
            <p className="text-muted-foreground p-8 text-center text-sm">
              {isAdmin ? `No MVRs were pulled in ${label}.` : `You did not pull any MVRs in ${label}.`}
            </p>
          ) : (
            <MvrTable pulls={data.pulls} editable={{ onChange: change, onDelete: setToDelete }} />
          )}
        </div>

        <p className="text-muted-foreground text-xs">
          Each MVR is kept on record for one year from the date it was pulled. The PDF itself is not
          stored.
        </p>

        {isAdmin && <HeadOfficeLink />}
      </div>

      {dragging && (
        <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center bg-black/30 p-4">
          <div className="bg-card border-primary flex flex-col items-center gap-1 rounded-lg border-2 border-dashed px-12 py-10 text-center">
            <strong className="text-lg">Drop to log MVR</strong>
            <span className="text-muted-foreground text-sm">Each PDF becomes a line in the register</span>
          </div>
        </div>
      )}

      <Dialog open={!!toDelete} onOpenChange={(o) => !o && setToDelete(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete this MVR record?</DialogTitle>
            <DialogDescription>
              It will be removed from the MVR log and the head-office report. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          {toDelete && (
            <div className="bg-secondary flex flex-col gap-0.5 rounded px-3 py-2.5 text-sm">
              <strong>{toDelete.driver_name ?? toDelete.licence_masked}</strong>
              <span className="text-muted-foreground text-xs">
                Pulled {longDate(toDelete.requested_on)} · ARIS {toDelete.aris_order_number}
              </span>
            </div>
          )}
          <DialogFooter>
            {/* Cancel first and focused: Enter or Esc never deletes. */}
            <Button variant="outline" autoFocus onClick={() => setToDelete(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmDelete}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  )
}

/** The admin's handle on the head-office report: the link to share, and the
 *  password that opens it. Changing the password shuts out anyone holding the
 *  old one at once (backend: the token carries the password's version). */
function HeadOfficeLink() {
  const link = `${window.location.origin}/mvr-report`
  const [password, setPassword] = useState('')
  const [saving, setSaving] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(link)
      toast.success('Link copied')
    } catch {
      toast.error('Could not copy — select the link and copy it')
    }
  }

  async function save() {
    setSaving(true)
    try {
      await setMvrReportPassword(password)
      setPassword('')
      toast.success('Password changed — share the new one with head office')
    } catch (err) {
      toast.error(apiDetail(err, 'The password was not changed'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="bg-card space-y-3 rounded border px-4 py-4">
      <div>
        <div className="font-semibold">Head office link</div>
        <div className="text-muted-foreground text-xs">
          Opens the full register for every broker, read only, with the password below. No login
          needed.
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <code className="bg-secondary rounded px-2 py-1 text-xs select-all">{link}</code>
        <Button size="sm" variant="outline" onClick={copy}>
          <Copy className="size-3.5" /> Copy link
        </Button>
      </div>
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (password.length >= 8) save()
        }}
      >
        <Input
          type="password"
          autoComplete="new-password"
          placeholder="New password (8+ characters)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-64"
        />
        <Button size="sm" type="submit" disabled={saving || password.length < 8}>
          Change password
        </Button>
      </form>
    </section>
  )
}
