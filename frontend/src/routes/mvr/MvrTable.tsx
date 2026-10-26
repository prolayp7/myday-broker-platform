import { Trash2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  MVR_BUSINESS_REASONS,
  MVR_INFO_RETRIEVED,
  type MvrBusinessReason,
  type MvrInfoRetrieved,
  type MvrPull,
} from '@/data/api'
import { longDate, reasonLabel, retrievedLabel } from './mvrFormat'

// The register, one line per MVR, in the Excel template's column order with
// the driver's name added after No. — the template has no name column, and a
// broker scanning their own month looks for people, not licence numbers.
//
// NO DRAWER. Every column is on the row (decided 2026-10-20 after a drawer
// was tried in the wireframe): the register is read across, like the sheet.

interface EditableProps {
  onChange: (
    pull: MvrPull,
    changes: { business_reason?: MvrBusinessReason; info_retrieved?: MvrInfoRetrieved },
  ) => void
  onDelete: (pull: MvrPull) => void
}

export function MvrTable({
  pulls,
  editable,
}: {
  pulls: MvrPull[]
  /** Absent on the head-office report, which is read only. */
  editable?: EditableProps
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[72rem] border-collapse text-sm">
        <thead>
          <tr className="bg-secondary/60 text-muted-foreground border-b text-left align-bottom text-[0.68rem] leading-tight tracking-wide uppercase">
            <Th>No.</Th>
            <Th>Driver</Th>
            <Th>Date of<br />Request</Th>
            <Th>Support Document &amp;<br />Reference Number</Th>
            <Th>Business<br />Reason</Th>
            <Th>Information Used<br />to Conduct the Search</Th>
            <Th>Information<br />Retrieved</Th>
            <Th>ARIS Order<br />Number</Th>
            <Th>Completed<br />By</Th>
            <Th>Date Authorized<br />Use Completed</Th>
            <Th>Destruction<br />Date</Th>
            {editable && <Th />}
          </tr>
        </thead>
        <tbody>
          {pulls.map((p, i) => (
            <tr key={p.id} className="hover:bg-secondary/40 border-b last:border-b-0">
              <Td className="text-muted-foreground tabular-nums">{i + 1}</Td>
              <Td className="font-medium">
                {p.driver_name ?? <span className="text-muted-foreground">No name</span>}
              </Td>
              <Td className="tabular-nums">{longDate(p.requested_on)}</Td>
              <Td className="font-mono text-xs tracking-wide">{p.licence_number}</Td>
              <Td>
                {editable ? (
                  <RowSelect
                    label={`Business reason for ${p.driver_name ?? 'this MVR'}`}
                    value={p.business_reason}
                    options={MVR_BUSINESS_REASONS}
                    onChange={(v) => editable.onChange(p, { business_reason: v })}
                  />
                ) : (
                  reasonLabel(p.business_reason)
                )}
              </Td>
              <Td className="font-mono text-xs tracking-wide">{p.licence_masked}</Td>
              <Td>
                {editable ? (
                  <RowSelect
                    label={`Information retrieved for ${p.driver_name ?? 'this MVR'}`}
                    value={p.info_retrieved}
                    options={MVR_INFO_RETRIEVED}
                    onChange={(v) => editable.onChange(p, { info_retrieved: v })}
                  />
                ) : (
                  retrievedLabel(p.info_retrieved)
                )}
              </Td>
              <Td className="font-mono text-xs tabular-nums">{p.aris_order_number}</Td>
              <Td>{p.completed_by}</Td>
              <Td className="tabular-nums">{longDate(p.use_completed_on)}</Td>
              <Td className="tabular-nums">{longDate(p.destruction_on)}</Td>
              {editable && (
                <Td className="w-px text-right">
                  <button
                    type="button"
                    onClick={() => editable.onDelete(p)}
                    aria-label={`Delete ${p.driver_name ?? 'this MVR'}`}
                    title="Delete"
                    className="text-muted-foreground hover:text-destructive hover:border-border rounded-md border border-transparent p-1.5"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </Td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function RowSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
      className="border-input bg-card rounded border px-1.5 py-1 text-sm outline-none"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}

function Th({ children }: { children?: React.ReactNode }) {
  return <th className="px-3 py-2.5 font-semibold">{children}</th>
}

function Td({ children, className }: { children?: React.ReactNode; className?: string }) {
  return <td className={cn('px-3 py-2.5 whitespace-nowrap', className)}>{children}</td>
}
