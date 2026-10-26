import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Input } from '@/components/ui/input'
import {
  getAddressDetail,
  suggestAddresses,
  type AddressSuggestion,
} from '@/data/api'

// The one address lookup field, shared by the applicant and every rental
// property. Lifted out of the retired tabbed form (NewPolicyPage) unchanged
// when the customer detail panel became the Classic paper.

const CONTROL =
  'h-(--height-control) w-full text-xs transition-[color,box-shadow,border-color] hover:border-ring/60'

/** The four columns any address lives in. Named identically on driver and
 *  property, deliberately: it is the same fact about the same kind of thing. */
export interface AddressFields {
  id: string
  street_address: string | null
  city: string | null
  province: string | null
  postal_code: string | null
}

export function AddressRow({
  driver,
  onSave,
}: {
  /**
   * Whatever row the address belongs to — the applicant on the auto side, a
   * property on the other. Typed by its ADDRESS COLUMNS rather than by table,
   * so one lookup field serves both instead of two that drift apart.
   */
  driver: AddressFields
  onSave: (patch: Partial<Omit<AddressFields, 'id'>>) => void
}) {
  // ONE FIELD, FOUR COLUMNS.
  //
  // The address was four inputs, and four inputs that must agree is a shape
  // that keeps producing the same bug: edit the street by hand and City,
  // Province and Postal still describe the previous address, so the row saves
  // a place that does not exist. Each fix held for its own case and the next
  // case found a new gap, because the split itself is what allows the parts to
  // disagree. One field cannot disagree with itself.
  //
  // The COLUMNS stay split — city and postal are real rating factors and are
  // wanted individually for Meta signal matching (see migration 0035). Google
  // returns the parts anyway, so a pick fills all four behind a single input
  // and nothing is lost by not showing four boxes.
  //
  // What is displayed is assembled from those columns rather than stored, so
  // the line on screen is always exactly what is in the database — it cannot
  // drift the way a separate "formatted" column would.
  const displayed = (d: AddressFields) =>
    [d.street_address, d.city, [d.province, d.postal_code].filter(Boolean).join(' ')]
      .filter(Boolean)
      .join(', ')

  const [text, setText] = useState(() => displayed(driver))

  // Follow the record when it changes from outside — a failed save reverting,
  // or values arriving after this row first rendered. Skipped while the field
  // is focused so it is never yanked mid-edit.
  useEffect(() => {
    const active = document.activeElement
    if (active instanceof HTMLInputElement && active.dataset.addressDriverId === driver.id) {
      return
    }
    setText(displayed(driver))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [driver.id, driver.street_address, driver.city, driver.province, driver.postal_code])

  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([])
  // A search has come back for the current text. Distinguishes "found nothing"
  // from "not asked yet", so the empty-state message cannot flash before any
  // request has been sent.
  const [searched, setSearched] = useState(false)
  const [open, setOpen] = useState(false)
  const [picking, setPicking] = useState(false)
  // Read SYNCHRONOUSLY by blur, which fires before React re-renders — state
  // would still say false at the moment the guard is needed.
  const pickingRef = useRef(false)
  // True between focus and the click that caused it finishing — see onMouseUp.
  const enteringRef = useRef(false)
  // Guards an out-of-order response: two queries in flight and the slower one
  // lands last, leaving a list that describes text already deleted.
  const queryRef = useRef('')

  useEffect(() => {
    const q = text.trim()
    queryRef.current = q
    // ONLY WHILE THIS FIELD IS BEING TYPED IN. Without this the effect also
    // runs on mount, so every address already on the page searches itself and
    // opens a dropdown nobody asked for — with two properties on screen, a
    // broker typing into the second one had the FIRST one's suggestions open
    // behind it, and a click could patch the wrong property.
    //
    // Read from the DOM rather than an isFocused state: focus arrives before
    // React re-renders, so state would still say false on the first keystroke.
    const active = document.activeElement
    if (!(active instanceof HTMLInputElement) || active.dataset.addressDriverId !== driver.id) {
      return
    }
    // Matches the backend's own floor — below it no request is sent at all.
    if (q.length < 3) {
      setSuggestions([])
      setSearched(false)
      return
    }
    // 300ms, the same debounce as the lead search box. Every keystroke is a
    // billed call without it.
    const timer = setTimeout(() => {
      suggestAddresses(q)
        .then((rows) => {
          if (queryRef.current !== q) return
          setSuggestions(rows)
          setSearched(true)
          // Opened even when empty — the list renders the "no matches" line,
          // which is the whole reason for showing it.
          setOpen(true)
        })
        // Silent: a failed lookup leaves the broker typing the address by
        // hand, which is what they did before this existed. Not marked as
        // searched, so a network error never claims Google found nothing.
        .catch(() => {
          setSuggestions([])
          setSearched(false)
        })
    }, 300)
    return () => clearTimeout(timer)
  }, [text])

  async function pick(suggestion: AddressSuggestion) {
    setOpen(false)
    pickingRef.current = true
    setPicking(true)
    try {
      const detail = await getAddressDetail(suggestion.place_id)
      const patch = {
        street_address: detail.street_address,
        city: detail.city,
        province: detail.province,
        postal_code: detail.postal_code,
      }
      // Show the assembled line immediately, then ONE save for all four parts.
      setText(
        [patch.street_address, patch.city, [patch.province, patch.postal_code].filter(Boolean).join(' ')]
          .filter(Boolean)
          .join(', '),
      )
      onSave(patch)
    } catch {
      toast.error('Could not load that address — try another')
    } finally {
      setPicking(false)
      // Cleared on a timer: the field's blur fires after this, and clearing
      // the guard now would let that blur through with the search text.
      setTimeout(() => {
        pickingRef.current = false
      }, 300)
    }
  }

  return (
    <div className="relative">
      <Input
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => {
          const next = e.target.value
          enteringRef.current = false
          // Delayed: clicking a suggestion blurs the input BEFORE the click
          // registers, so closing at once would dismiss the list under the
          // cursor every time.
          setTimeout(() => setOpen(false), 150)
          // A pick is in flight or just landed — it writes all four parts, and
          // this handler only knows the search text that opened the dropdown.
          if (pickingRef.current) return
          if (next === displayed(driver)) return
          // TYPED, NOT PICKED. There is no way to split a free-typed line into
          // city and postal without guessing, and a guess here is a wrong
          // rating factor. The whole line is kept as the street and the
          // derived parts are cleared, so what is stored is only ever what is
          // known — never a real street beside a city inferred from a
          // different address.
          const raw = next.trim() || null
          onSave({ street_address: raw, city: null, province: null, postal_code: null })
        }}
        onFocus={() => {
          enteringRef.current = true
          if (suggestions.length > 0) setOpen(true)
        }}
        // Select on entry so typing REPLACES the address rather than appending
        // to it. On mouseUp, not focus: a click fires focus first and then
        // places the caret, collapsing a selection made in onFocus. Limited to
        // the entering click so clicking back in to fix a word still works.
        onMouseUp={(e) => {
          if (!enteringRef.current) return
          enteringRef.current = false
          e.currentTarget.select()
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') setOpen(false)
          else if (e.key === 'Enter') {
            if (open && suggestions.length > 0) {
              e.preventDefault()
              void pick(suggestions[0])
            } else e.currentTarget.blur()
          }
        }}
        data-address-driver-id={driver.id}
        aria-label="Address"
        placeholder="Start typing an address"
        autoComplete="off"
        className={CONTROL}
      />
      {picking && <span className="text-muted-foreground absolute top-2 right-2 text-2xs">…</span>}

      {/* "No matches" is a RESULT, not an absence. Rendering nothing when a
          search comes back empty looks exactly like a broken lookup. */}
      {open && searched && suggestions.length === 0 && (
        <div className="bg-popover border-input text-muted-foreground absolute top-full right-0 left-0 z-50 mt-1 rounded-md border px-2 py-1.5 text-xs shadow-md">
          No matches — keep typing, or enter it by hand
        </div>
      )}
      {open && suggestions.length > 0 && (
        <ul
          role="listbox"
          className="bg-popover border-input absolute top-full right-0 left-0 z-50 mt-1 max-h-56 overflow-auto rounded-md border shadow-md"
        >
          {suggestions.map((s) => (
            <li key={s.place_id}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                // onMouseDown, not onClick: mousedown fires before the input's
                // blur, so the pick is handled even though blur closes the list.
                onMouseDown={(e) => {
                  e.preventDefault()
                  void pick(s)
                }}
                className="hover:bg-accent focus:bg-accent w-full px-2 py-1.5 text-left text-xs outline-none"
              >
                {s.description}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// Radix rejects an empty-string item value, so the "not asked yet" option
// carries a sentinel that is translated back to '' at the boundary below.
