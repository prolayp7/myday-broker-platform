import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import { Download, Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { getMvrReport, unlockMvrReport, type MvrMonth } from '@/data/api'
import { MvrTable } from './MvrTable'
import { apiDetail, downloadRegisterCsv, registerMonths } from './mvrFormat'

// The head-office view of the MVR register: a shareable link, opened with a
// password — no username, no CRM account, no 2FA (decided 2026-10-20). Every
// broker's lines, licence in full, read only.
//
// OUTSIDE AppShell and RequireAuth, the way /m sits outside the desktop
// shell: the person opening this has no CRM login, and the CRM's nav would
// only offer them screens that send them to one.
//
// The unlock token lives in sessionStorage: closing the tab locks it again,
// and it never reaches the broker session in localStorage.

const TOKEN_KEY = 'mvr-report-token'
const months = registerMonths()

function readToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

function writeToken(token: string | null) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // Storage blocked: the report still works for this page load.
  }
}

export default function MvrReport() {
  const [token, setToken] = useState<string | null>(readToken)

  useEffect(() => {
    document.title = 'MVR Records · PolicyScanner'
  }, [])

  // Stable, because Report's fetch effect depends on it.
  const lock = useCallback(() => {
    writeToken(null)
    setToken(null)
  }, [])

  return token ? (
    <Report token={token} onLocked={lock} />
  ) : (
    <Gate
      onUnlocked={(t) => {
        writeToken(t)
        setToken(t)
      }}
    />
  )
}

function Gate({ onUnlocked }: { onUnlocked: (token: string) => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!password) {
      setError('Enter the password to continue.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      onUnlocked(await unlockMvrReport(password))
    } catch (err) {
      setError(apiDetail(err, 'The report could not be opened. Try again.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bg-background grid min-h-screen place-items-center p-4">
      <form onSubmit={submit} className="bg-card flex w-full max-w-sm flex-col gap-4 rounded-lg border p-6">
        <div className="space-y-1">
          <h1 className="text-lg font-semibold">MVR Records</h1>
          <p className="text-muted-foreground text-sm">
            PolicyScanner Brokerage. Records of Licensed Information for the Authorized Requester
            Agreement.
          </p>
        </div>
        <label className="text-muted-foreground flex flex-col gap-1.5 text-xs font-medium">
          Password
          <Input
            type="password"
            autoFocus
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="text-destructive text-xs">{error}</p>}
        <Button type="submit" disabled={busy}>
          {busy ? 'Opening…' : 'Open'}
        </Button>
      </form>
    </div>
  )
}

function Report({ token, onLocked }: { token: string; onLocked: () => void }) {
  const [month, setMonth] = useState(months[0].value)
  const [brokerId, setBrokerId] = useState('')
  const [data, setData] = useState<MvrMonth | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setFailed(false)
    getMvrReport(token, month, brokerId || undefined)
      .then((d) => !cancelled && setData(d))
      .catch((err) => {
        if (cancelled) return
        // Expired, or the admin changed the password: back to the gate.
        if (axios.isAxiosError(err) && err.response?.status === 401) {
          onLocked()
          return
        }
        setData(null)
        setFailed(true)
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [token, month, brokerId, onLocked])

  const label = months.find((m) => m.value === month)?.label ?? month
  const brokerName = data?.brokers.find((b) => b.broker_id === brokerId)?.name

  return (
    <div className="bg-background min-h-screen">
      <header className="bg-card border-b px-4">
        <div className="mx-auto flex min-h-13 max-w-screen-2xl flex-wrap items-center gap-3 py-2">
          <span className="font-bold">MVR Records</span>
          <span className="text-muted-foreground text-sm">
            PolicyScanner Brokerage · Records of Licensed Information
          </span>
          <Button size="sm" variant="outline" className="ml-auto" onClick={onLocked}>
            <Lock className="size-3.5" /> Lock
          </Button>
        </div>
      </header>

      <main className="mx-auto max-w-screen-2xl space-y-4 p-4 sm:p-6">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <label className="text-muted-foreground flex items-center gap-2">
            Month
            <select
              value={month}
              onChange={(e) => {
                setData(null)
                setMonth(e.target.value)
              }}
              className="border-input bg-card text-foreground rounded border px-2 py-1 outline-none"
            >
              {months.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-muted-foreground flex items-center gap-2">
            Broker
            <select
              value={brokerId}
              onChange={(e) => {
                setData(null)
                setBrokerId(e.target.value)
              }}
              className="border-input bg-card text-foreground rounded border px-2 py-1 outline-none"
            >
              <option value="">All brokers</option>
              {data?.brokers.map((b) => (
                <option key={b.broker_id} value={b.broker_id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
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
        </div>

        <div className="bg-card overflow-hidden rounded border">
          <div className="flex items-baseline justify-between gap-4 border-b px-4 py-3">
            <div>
              <div className="font-semibold">{label}</div>
              <div className="text-muted-foreground text-xs">
                {brokerName ? `MVRs pulled by ${brokerName}` : 'MVRs pulled by all brokers'}
              </div>
            </div>
            <div className="text-lg font-semibold tabular-nums">{data ? data.pulls.length : '—'}</div>
          </div>
          {loading ? (
            <p className="text-muted-foreground p-8 text-center text-sm">Loading…</p>
          ) : failed ? (
            <p className="text-muted-foreground p-8 text-center text-sm">Could not load this month.</p>
          ) : !data?.pulls.length ? (
            <p className="text-muted-foreground p-8 text-center text-sm">
              {brokerName ? `${brokerName} did not pull any MVRs in ${label}.` : `No MVRs were pulled in ${label}.`}
            </p>
          ) : (
            <MvrTable pulls={data.pulls} />
          )}
        </div>

        <p className="text-muted-foreground text-xs">
          Read only. Lines are added by brokers when they upload an MVR in the CRM.
        </p>
      </main>
    </div>
  )
}
