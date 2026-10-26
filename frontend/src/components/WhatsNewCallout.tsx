import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Button } from '@/components/ui/button'
import { CURRENT, markSeen, shouldShow } from '@/lib/whatsNew'

// A COACHMARK: the whole screen dims, the one control that changed stays lit
// above the dimming, and a small note sits under it with a caret pointing up.
// The note shakes once on arrival to draw the eye, then holds still.
//
// NOT A POPOVER. The popover primitive has no overlay and zooms in like a
// menu — the wrong feel for "look here". And not a Dialog: that centres
// itself and stops the screen to say one sentence. A coachmark points AT the
// thing, which is the whole point of showing it beside the button rather
// than in the middle.
//
// MEASURED, NOT ANCHORED BY RADIX. The note is positioned from the child's
// own getBoundingClientRect, recomputed on resize, and portaled to <body> so
// the header's stacking cannot clip it. The child is wrapped in a span that
// lifts it above the overlay while the mark is open — that lift is what makes
// the button read as spotlit rather than dimmed with everything else.
//
// ONLY "GOT IT" DISMISSES. Clicking the overlay or pressing Escape hides it
// for this visit and it returns next login: a stray click while reaching for
// something else must not count as having read it.
//
// Reduced motion is honoured for free: index.css zeroes every animation under
// prefers-reduced-motion, so the shake simply does not run there.

export function WhatsNewCallout({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(() => shouldShow())
  const anchorRef = useRef<HTMLSpanElement>(null)
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null)

  // Position under the button, right-aligned to its right edge, so the note
  // hangs from the control it is about and never runs off the window edge.
  useLayoutEffect(() => {
    if (!open) return
    const measure = () => {
      const r = anchorRef.current?.getBoundingClientRect()
      // +14: room for the arrow above the note to sit clear of the button.
      if (r) setPos({ top: r.bottom + 14, right: window.innerWidth - r.right })
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  function dismiss() {
    markSeen()
    setOpen(false)
  }

  return (
    <>
      {/* relative + z-[60] only while open: above the z-50 overlay, so the
          button is the one thing on screen that is not dimmed. Inline-flex so
          the wrapper adds no height to the bar. */}
      <span ref={anchorRef} className={open ? 'relative z-coachmark inline-flex' : 'inline-flex'}>
        {children}
      </span>

      {open &&
        pos &&
        createPortal(
          <>
            {/* The dimming. Same bg-black/50 the app's dialogs use, so it
                reads as the familiar "something needs you" rather than a new
                effect. Clicking it closes for now — not dismisses. */}
            <div
              className="animate-in fade-in-0 fixed inset-0 z-50 bg-black/50 duration-200"
              onClick={() => setOpen(false)}
              aria-hidden
            />

            {/* The note. Fixed at the measured spot. THE ARROW is a rotated
                square sharing the note's surface and border, so note and
                arrow read as one shape pointing up at the button — sized and
                placed to sit under the button's centre, not a faint notch in
                the corner. animate-nudge is the one-shot shake (index.css). */}
            {/* role="note", not "dialog": a dialog implies modality and a
                focus trap to a screen reader, and this deliberately steals no
                focus — the broker's cursor stays where they put it. It is a
                hint that happens to have a button. */}
            <div
              role="note"
              aria-label={CURRENT.text}
              style={{ top: pos.top, right: pos.right }}
              className="animate-in fade-in-0 slide-in-from-top-1 animate-nudge fixed z-coachmark duration-300"
            >
              <span
                aria-hidden
                className="bg-popover border-border absolute -top-2 right-3 size-4 rotate-45 border-t border-l"
              />
              <div className="bg-popover text-popover-foreground border-border flex items-center gap-3 rounded-lg border px-3.5 py-2.5 shadow-popover">
                <span className="text-sm">{CURRENT.text}</span>
                <Button type="button" size="sm" className="h-7 shrink-0" onClick={dismiss}>
                  Got it
                </Button>
              </div>
            </div>
          </>,
          document.body,
        )}
    </>
  )
}
