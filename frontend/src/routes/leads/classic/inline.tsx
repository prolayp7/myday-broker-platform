import { useEffect, useRef, useState } from 'react'
import { DateField } from '@/components/DateField'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

// The three print-like controls the Classic paper is made of.
//
// Each one LOOKS like a bold value printed after its label and BEHAVES like
// the app's ordinary control for the same fact (the shadcn Select, DateField).
// Nothing here owns data: every one takes the saved value and a save handler.
//
// The look is all in classic.css under `.classic` — these components carry
// class names, not styles.

/**
 * A value you click and type over. Saves on blur or Enter when the text
 * changed; Escape puts the saved value back. Quiet on success — the paper
 * simply shows the new value — because a toast per field would be a toast per
 * keystroke on a screen a broker fills end to end.
 */
export function InlineText({
  value,
  onSave,
  required,
  type = 'text',
  label,
  className,
  disabled,
  minSize = 2,
}: {
  value: string | null | undefined
  onSave: (next: string) => void
  /** Shows "required" in the gap while empty. Nothing here enforces it — the
   *  stage gate in services/lead.py does — this only says it ahead of time. */
  required?: boolean
  type?: 'text' | 'tel' | 'email' | 'number'
  label: string
  className?: string
  disabled?: boolean
  minSize?: number
}) {
  const saved = value ?? ''
  const [text, setText] = useState(saved)
  const ref = useRef<HTMLInputElement>(null)
  // ESCAPE MUST CANCEL, NOT SAVE. Escape resets the text and then blurs, but
  // the blur's commit runs before React has applied that reset — it would
  // still see the typed text and save exactly what the broker meant to
  // discard. A ref is read synchronously, so the commit can tell.
  const cancelling = useRef(false)

  // Follow the saved value when it changes from outside (a DASH fill, a
  // reload) — but never while the broker is typing in this very box.
  useEffect(() => {
    if (document.activeElement !== ref.current) setText(saved)
  }, [saved])

  function commit() {
    if (cancelling.current) {
      cancelling.current = false
      setText(saved)
      return
    }
    const next = text.trim()
    if (next === saved) {
      setText(saved)
      return
    }
    onSave(next)
  }

  const placeholder = required ? 'required' : disabled ? '—' : ''
  // Width follows the content, like print: an input sized to its text reads as
  // a word on a line, not as a box waiting to be filled.
  const size = Math.max(text.length, placeholder.length, minSize) + 1

  return (
    <input
      ref={ref}
      value={text}
      type={type === 'number' ? 'text' : type}
      inputMode={type === 'number' ? 'numeric' : undefined}
      size={size}
      disabled={disabled}
      aria-label={label}
      placeholder={placeholder}
      data-required={required && saved === '' ? '' : undefined}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.currentTarget.blur()
        if (e.key === 'Escape') {
          cancelling.current = true
          setText(saved)
          e.currentTarget.blur()
        }
      }}
      className={cn('cl-text', className)}
    />
  )
}

// Radix rejects an empty-string item value, so "not answered" travels as a
// sentinel and is translated back to '' at the boundary.
const CLEAR = '__clear__'

/**
 * A value picked from a list. The app's own Select, with a trigger drawn as
 * plain text so the paper stays a paper; the list that opens is the usual one.
 */
export function InlineSelect({
  value,
  options,
  onChange,
  label,
  placeholder = '—',
  required,
  clearable,
  disabled,
  className,
}: {
  value: string | null | undefined
  options: readonly { value: string; label: string }[] | readonly string[]
  onChange: (next: string) => void
  label: string
  placeholder?: string
  required?: boolean
  /** Offer a "—" row that clears the answer. */
  clearable?: boolean
  disabled?: boolean
  className?: string
}) {
  const items = options.map((o) => (typeof o === 'string' ? { value: o, label: o } : o))
  const current = value ?? ''
  return (
    <Select
      value={current === '' ? (clearable ? CLEAR : '') : current}
      onValueChange={(v) => onChange(v === CLEAR ? '' : v)}
      disabled={disabled}
    >
      <SelectTrigger
        size="sm"
        aria-label={label}
        data-required={required && current === '' ? '' : undefined}
        className={cn('cl-select', className)}
      >
        <SelectValue placeholder={required && current === '' ? 'required' : placeholder} />
      </SelectTrigger>
      <SelectContent position="popper">
        {clearable && <SelectItem value={CLEAR}>—</SelectItem>}
        {items.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/**
 * A date, typed straight through or picked from the calendar — DateField as
 * it is, in print clothing. The calendar button appears on hover.
 */
export function InlineDate({
  value,
  onChange,
  label,
  required,
  className,
}: {
  value: string | null | undefined
  onChange: (iso: string) => void
  label: string
  required?: boolean
  className?: string
}) {
  return (
    <span
      className={cn('cl-date', className)}
      data-required={required && !value ? '' : undefined}
    >
      <DateField value={value ?? ''} onChange={onChange} label={label} />
    </span>
  )
}

/** One printed line: "Label: value". A required field carries a star on its
 *  label, as every intake form does — the gap says "required" too, but the
 *  star is there before AND after the value is filled. */
export function Line({
  label,
  required,
  children,
  className,
}: {
  label: string
  required?: boolean
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn('cl-line', className)}>
      <span className="cl-label">
        {label}
        {required && (
          <span className="cl-star" aria-hidden="true">
            *
          </span>
        )}
        :
      </span>{' '}
      {children}
    </div>
  )
}
