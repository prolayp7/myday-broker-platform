import { ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react'
import { DayPicker } from 'react-day-picker'
import { cn } from '@/lib/utils'

// react-day-picker, dressed in our tokens.
//
// The library ships its own stylesheet; we deliberately do NOT import it and
// restyle every part instead, so the calendar inherits the app's colours,
// radius, focus ring and dark mode for free rather than fighting a second
// design system. Sizes track --height-control so a day cell matches the
// controls around it.

export function Calendar({
  className,
  classNames,
  showOutsideDays = true,
  ...props
}: React.ComponentProps<typeof DayPicker>) {
  return (
    <DayPicker
      showOutsideDays={showOutsideDays}
      className={cn('p-2', className)}
      classNames={{
        // The nav arrows are absolutely positioned, so the calendar root has
        // to be their containing block. Without this they anchor to whatever
        // is positioned further up — the popover — and drift out of place.
        root: 'relative',
        months: 'flex flex-col gap-3',
        month: 'flex flex-col gap-3',
        month_caption: 'flex h-(--height-control) items-center justify-center px-8',

        // Month + year dropdowns. Essential here: G1 dates and dates of birth
        // are decades back, and paging month-by-month to reach them is not a
        // usable interaction.
        //
        // Each dropdown renders TWO nodes: the real <select>, and an
        // aria-hidden span that draws the visible label. The library's own
        // stylesheet (which we do not import, so the calendar can inherit our
        // tokens) is what normally hides the select behind the label. Without
        // it both painted — "July ▾ July". So the select is made invisible and
        // stretched over the label, which keeps it clickable and keyboard
        // accessible while the label is what you actually see.
        dropdowns: 'flex items-center gap-1',
        dropdown_root: 'relative inline-flex items-center',
        dropdown: 'absolute inset-0 cursor-pointer opacity-0',
        caption_label:
          'border-input hover:border-ring/60 inline-flex items-center gap-0.5 rounded-md border px-1.5 py-1 text-xs font-medium transition-colors select-none',

        nav: 'absolute inset-x-2 top-2 flex items-center justify-between',
        button_previous:
          'text-muted-foreground hover:bg-accent hover:text-foreground inline-flex size-7 items-center justify-center rounded-md transition-colors disabled:pointer-events-none disabled:opacity-40',
        button_next:
          'text-muted-foreground hover:bg-accent hover:text-foreground inline-flex size-7 items-center justify-center rounded-md transition-colors disabled:pointer-events-none disabled:opacity-40',

        month_grid: 'w-full border-collapse',
        weekdays: 'flex',
        weekday: 'text-muted-foreground w-8 text-2xs font-normal',
        week: 'mt-0.5 flex w-full',

        day: 'size-8 p-0',
        day_button:
          'hover:bg-accent focus-visible:ring-ring/50 size-8 rounded-md text-xs font-normal transition-colors focus-visible:ring-[3px] focus-visible:outline-none',

        selected:
          '[&>button]:bg-primary [&>button]:text-primary-foreground [&>button]:hover:bg-primary [&>button]:font-semibold',
        today: '[&>button]:ring-ring/40 [&>button]:ring-1',
        outside: '[&>button]:text-muted-foreground/50',
        disabled: '[&>button]:pointer-events-none [&>button]:opacity-40',
        // Arrow-key navigation moves this flag from day to day. Unstyled, a
        // keyboard user has no idea where they are in the grid.
        focused: '[&>button]:ring-ring [&>button]:ring-2 [&>button]:outline-none',
        hidden: 'invisible',
        ...classNames,
      }}
      components={{
        // Three orientations are in play: left/right drive the month nav
        // arrows, and down is the caret inside each dropdown label.
        Chevron: ({ orientation, className: chevronClass }) => {
          if (orientation === 'left') return <ChevronLeft className={cn('size-4', chevronClass)} />
          if (orientation === 'right') return <ChevronRight className={cn('size-4', chevronClass)} />
          return <ChevronDown className={cn('text-muted-foreground size-3', chevronClass)} />
        },
      }}
      {...props}
    />
  )
}
