import * as React from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { type Control, Controller, type FieldValues, type Path, useWatch } from 'react-hook-form'
import { cn } from '@/lib/utils'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  RadioGroup,
  RadioGroupItem,
} from '@/components/ui/radio-group'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import type { SelectItem as SelectItemType } from '@/domain/propertySpec'

// RHF-connected field primitives — the correct control per data type
// (DESIGN_PRINCIPLES §4.2): text/number/tel/email → Input, date → date input,
// fixed list → Select, exactly-2 options → RadioGroup. Labels on top.

function FieldShell({
  label,
  required,
  children,
}: {
  label: string
  required?: boolean
  children: React.ReactNode
}) {
  // The label WRAPS its control rather than pointing at it with htmlFor.
  //
  // Without an association the two are only visually adjacent: clicking the
  // label does not focus the field, and a screen reader announces an
  // unlabelled input. htmlFor would need an id on the control, but the child
  // here is a react-hook-form <Controller> — cloning an id onto that puts it
  // on the controller, not the input, and it never reaches the DOM. Nesting
  // is the association that works regardless of what the child renders.
  return (
    <Label className="block space-y-1.5">
      <span className="block text-xs font-normal text-muted-foreground">
        {label}
        {required && <span className="text-error"> *</span>}
      </span>
      {children}
    </Label>
  )
}

// yyyy-mm-dd (the value stored/submitted, matching native <input type="date">
// and every API payload) <-> mm-dd-yyyy (the only format ever shown to the
// user, everywhere in the app).
function isoToDisplay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  return m ? `${m[2]}-${m[3]}-${m[1]}` : iso
}

function displayToIso(display: string): string {
  const m = /^(\d{2})-(\d{2})-(\d{4})$/.exec(display)
  return m ? `${m[3]}-${m[1]}-${m[2]}` : display
}

// Reformats free-typed digits into mm-dd-yyyy as the user types, inserting
// dashes automatically (e.g. "01152026" -> "01-15-2026").
function maskDateInput(raw: string): string {
  const digits = raw.replace(/\D/g, '').slice(0, 8)
  const mm = digits.slice(0, 2)
  const dd = digits.slice(2, 4)
  const yyyy = digits.slice(4, 8)
  return [mm, dd, yyyy].filter(Boolean).join('-')
}

function DateTextField({
  value,
  onChange,
  onBlur,
}: {
  value: string
  onChange: (isoValue: string) => void
  onBlur: () => void
}) {
  const [display, setDisplay] = React.useState(() => isoToDisplay(value ?? ''))

  React.useEffect(() => {
    setDisplay(isoToDisplay(value ?? ''))
  }, [value])

  return (
    <Input
      value={display}
      placeholder="mm-dd-yyyy"
      inputMode="numeric"
      maxLength={10}
      onChange={(e) => {
        const masked = maskDateInput(e.target.value)
        setDisplay(masked)
        onChange(/^\d{2}-\d{2}-\d{4}$/.test(masked) ? displayToIso(masked) : '')
      }}
      onBlur={onBlur}
    />
  )
}

export function TextField<T extends FieldValues>({
  control,
  name,
  label,
  placeholder,
  type = 'text',
  required,
  min,
  max,
}: {
  control: Control<T>
  name: Path<T>
  label: string
  placeholder?: string
  type?: 'text' | 'number' | 'tel' | 'email' | 'date'
  required?: boolean
  // Only meaningful for type="number" — the native input clamp/step bounds.
  min?: number
  max?: number
}) {
  return (
    <FieldShell label={label} required={required}>
      <Controller
        control={control}
        name={name}
        render={({ field }) =>
          type === 'date' ? (
            <DateTextField value={field.value ?? ''} onChange={field.onChange} onBlur={field.onBlur} />
          ) : (
            <Input
              {...field}
              value={field.value ?? ''}
              type={type}
              placeholder={placeholder}
              min={type === 'number' ? min : undefined}
              max={type === 'number' ? max : undefined}
            />
          )
        }
      />
    </FieldShell>
  )
}

export function SelectField<T extends FieldValues>({
  control,
  name,
  label,
  items,
  required,
  onChangeIntercept,
}: {
  control: Control<T>
  name: Path<T>
  label: string
  items: SelectItemType[]
  required?: boolean
  // when set, the raw model write is suppressed and this runs instead —
  // lets a caller gate the change (e.g. confirm before clearing data).
  onChangeIntercept?: (value: string) => void
}) {
  return (
    <FieldShell label={label} required={required}>
      <Controller
        control={control}
        name={name}
        render={({ field }) => (
          <Select
            value={field.value || undefined}
            onValueChange={(v) =>
              onChangeIntercept ? onChangeIntercept(v) : field.onChange(v)
            }
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="—" />
            </SelectTrigger>
            <SelectContent>
              {items.map((it) => (
                <SelectItem key={it.k} value={it.k}>
                  {it.t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      />
    </FieldShell>
  )
}

// Multi-select (the SAP MultiComboBox equivalent) — a fixed list where more
// than one value can be chosen. Stored as an array of keys. Rendered as a
// popover of checkable rows, with the chosen labels summarised on the trigger.
export function MultiSelectField<T extends FieldValues>({
  control,
  name,
  label,
  items,
  required,
}: {
  control: Control<T>
  name: Path<T>
  label: string
  items: SelectItemType[]
  required?: boolean
}) {
  return (
    <FieldShell label={label} required={required}>
      <Controller
        control={control}
        name={name}
        render={({ field }) => {
          const selected: string[] = Array.isArray(field.value) ? field.value : []
          const toggle = (key: string) =>
            field.onChange(
              selected.includes(key)
                ? selected.filter((k) => k !== key)
                : [...selected, key],
            )
          const summary =
            selected.length === 0
              ? '—'
              : items
                  .filter((it) => selected.includes(it.k))
                  .map((it) => it.t)
                  .join(', ')
          return (
            <Popover>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  className="border-input flex w-full items-center justify-between gap-2 rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs"
                >
                  <span className={cn('truncate', selected.length === 0 && 'text-muted-foreground')}>
                    {summary}
                  </span>
                  <ChevronDown className="text-muted-foreground size-4 shrink-0" />
                </button>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-56 p-1">
                {items.map((it) => {
                  const on = selected.includes(it.k)
                  return (
                    <button
                      key={it.k}
                      type="button"
                      onClick={() => toggle(it.k)}
                      className="hover:bg-muted flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm"
                    >
                      <span className={cn('flex size-4 items-center justify-center rounded border', on ? 'bg-primary border-primary text-primary-foreground' : 'border-input')}>
                        {on && <Check className="size-3" />}
                      </span>
                      {it.t}
                    </button>
                  )
                })}
              </PopoverContent>
            </Popover>
          )
        }}
      />
    </FieldShell>
  )
}

// exactly-2 mutually-exclusive options stored as lowercased labels.
export function TwoOptionField<T extends FieldValues>({
  control,
  name,
  label,
  a = 'Yes',
  b = 'No',
  required,
}: {
  control: Control<T>
  name: Path<T>
  label: string
  a?: string
  b?: string
  required?: boolean
}) {
  const ka = a.toLowerCase()
  const kb = b.toLowerCase()
  return (
    <FieldShell label={label} required={required}>
      <Controller
        control={control}
        name={name}
        render={({ field }) => (
          <RadioGroup
            value={field.value || ''}
            onValueChange={field.onChange}
            className="flex gap-4 pt-1"
          >
            <label className="flex items-center gap-2 text-sm">
              <RadioGroupItem value={ka} /> {a}
            </label>
            <label className="flex items-center gap-2 text-sm">
              <RadioGroupItem value={kb} /> {b}
            </label>
          </RadioGroup>
        )}
      />
    </FieldShell>
  )
}

// One year, matching the backend's MAX_EO_TERM (schemas/broker.py). 366 days so
// a full leap-year term isn't flagged as too long.
const MAX_EO_TERM_DAYS = 366

// Shows the gap between the E&O start and end dates the admin entered, and
// flags the two things the backend will reject (end not after start, term over
// a year) BEFORE submit, so it's caught inline instead of as a 422. A broker
// can join mid-term, so a short term is fine — only "too long"/"backwards" warn.
export function EoTermSummary<T extends FieldValues>({
  control,
  startName,
  endName,
}: {
  control: Control<T>
  startName: Path<T>
  endName: Path<T>
}) {
  const start = useWatch({ control, name: startName }) as string | undefined
  const end = useWatch({ control, name: endName }) as string | undefined
  if (!start || !end) return null

  const startMs = Date.parse(start)
  const endMs = Date.parse(end)
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return null

  const days = Math.round((endMs - startMs) / 86_400_000)

  if (days <= 0) {
    return (
      <p className="text-error text-xs">E&O end date must be after the start date.</p>
    )
  }
  if (days > MAX_EO_TERM_DAYS) {
    return (
      <p className="text-error text-xs">
        E&O term is {formatTermGap(days)} — it can't be longer than one year.
      </p>
    )
  }
  return (
    <p className="text-muted-foreground text-xs">E&O term: {formatTermGap(days)}</p>
  )
}

// Render a day-count as an approximate "Xy Ym Zd" gap for a human reader.
function formatTermGap(days: number): string {
  const years = Math.floor(days / 365)
  const months = Math.floor((days % 365) / 30)
  const rem = days - years * 365 - months * 30
  const parts: string[] = []
  if (years) parts.push(`${years}y`)
  if (months) parts.push(`${months}m`)
  if (rem || parts.length === 0) parts.push(`${rem}d`)
  return parts.join(' ')
}
