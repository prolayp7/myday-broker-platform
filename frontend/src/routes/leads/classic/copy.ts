// What a broker copies off the paper is pasted into an insurer's quoting
// system, which wants RAW values: no label, no formatting.
//
//   dates     mmddyyyy              1987-01-01        → 01011987
//   licence   no dashes or spaces   B0023-64708-70101 → B00236470870101
//   phone     digits only           (416) 555-0182    → 4165550182
//   postal    no space              M1P 0A9           → M1P0A9
//   address   one line              street, city, PROV POSTAL

/** How a field's text becomes its raw copy. */
export type CopyAs = 'digits' | 'compact'

const ISO_DATE = /\b(\d{4})-(\d{2})-(\d{2})\b/g

export function rawDate(iso: string | null | undefined): string {
  return (iso ?? '').replace(ISO_DATE, '$2$3$1')
}

export function rawText(text: string, as?: CopyAs): string {
  if (as === 'digits') return text.replace(/\D/g, '')
  if (as === 'compact') return text.replace(/[\s-]/g, '').toUpperCase()
  // Anything else: dates inside the selection still go out as mmddyyyy.
  return text.replace(ISO_DATE, '$2$3$1')
}

export function rawAddress(d: {
  street_address: string | null
  city: string | null
  province: string | null
  postal_code: string | null
}): string {
  const postal = rawText(d.postal_code ?? '', 'compact')
  return [d.street_address, d.city, [d.province, postal].filter(Boolean).join(' ')]
    .filter(Boolean)
    .join(', ')
}

/** Ctrl+C anywhere on the paper copies the same raw value the button would.
 *  A field marks how it copies with data-copy="digits|compact". */
export function onPaperCopy(e: React.ClipboardEvent) {
  const el = document.activeElement
  let text = ''
  let as: CopyAs | undefined
  if (el instanceof HTMLInputElement && el.selectionStart !== null && el.selectionEnd !== null) {
    text = el.value.slice(el.selectionStart, el.selectionEnd)
    as = el.dataset.copy as CopyAs | undefined
  } else {
    const selection = window.getSelection()
    text = selection?.toString() ?? ''
    // Selected text (a table cell, not an input): the cell says how it copies.
    const node = selection?.anchorNode
    const holder = (node instanceof Element ? node : node?.parentElement)?.closest<HTMLElement>(
      '[data-copy]',
    )
    as = holder?.dataset.copy as CopyAs | undefined
  }
  if (!text) return
  const raw = rawText(text, as)
  if (raw !== text) {
    e.clipboardData.setData('text/plain', raw)
    e.preventDefault()
  }
}
