import { useEffect, useState, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  Bell,
  ChevronDown,
  CircleDot,
  KeyRound,
  LogOut,
  Monitor,
  Moon,
  Sun,
  User,
  DollarSign,
  PauseCircle,
} from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  getMyProfile,
  setMyAvailability,
  setMyReadyForMore,
  initialsOf,
  type MyProfile,
} from '@/data/api'
import { useAuth } from '@/auth/AuthContext'
import { resolve, useTheme, type Theme } from '@/lib/theme'
import { useConfirm } from '@/components/ConfirmDialog'
import { ChangePasswordDialog } from '@/components/ChangePasswordDialog'
import { ProfileDialog } from '@/components/ProfileDialog'
import { WhatsNewCallout } from '@/components/WhatsNewCallout'

// The SAP ShellBar equivalent: brand mark + nav, the daily lead opt-in,
// notifications, and a profile dropdown. Wraps My Day and Leads (Login is
// bare).
//
// `section` no longer prints in the bar — the highlighted nav tab already says
// where you are, so showing it twice was spending width for nothing. It drives
// the browser tab title instead, which is where a screen name is actually
// useful (finding the right window among several).
//
// Two slots let a screen fold its own chrome INTO this bar instead of spending
// a second row on it — the app-like arrangement, where the object you are
// working on and its actions share the title bar:
//   * `context` — what you're working on (Leads puts the selected lead's name,
//     stage and facts here), sitting after the nav.
//   * `actions` — what you can do to it (Call / WhatsApp), sitting before the
//     shell's own always-present controls so those never move between screens.
export function AppShell({
  section,
  context,
  actions,
  children,
}: {
  section: string
  context?: ReactNode
  actions?: ReactNode
  children: ReactNode
}) {
  const [changePasswordOpen, setChangePasswordOpen] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const navigate = useNavigate()
  const { pathname } = useLocation()
  // Renamed on destructure to sit beside `me` below without colliding: this
  // is the identity decoded from the access token, and only its role is
  // wanted here (to decide whether the Admin tab appears).
  const { logout, broker: session } = useAuth()
  const confirm = useConfirm()
  const [theme, setTheme] = useTheme()

  // Today's Group-2 opt-in, read from the profile the header already fetches
  // for the avatar — so the toggle costs no extra request.
  const [moreToday, setMoreToday] = useState(false)
  const [savingMore, setSavingMore] = useState(false)

  // Whether the broker has stopped their own leads. Same source as moreToday —
  // the profile fetch the header already makes — so neither switch costs a
  // request of its own.
  const [paused, setPaused] = useState(false)
  const [savingPaused, setSavingPaused] = useState(false)

  /** Optimistic, rolled back on failure — the same rule the stage control in
   *  Leads.tsx follows. Leaving a switch showing a state the server rejected
   *  is the worst outcome: the broker believes they are taking extra leads and
   *  they are not.
   *
   *  Toasted on success because switching ON can hand over leads that were
   *  already waiting — the list grows a second later, and the broker should
   *  know why. */
  async function takeMoreToday(next: boolean) {
    const previous = moreToday
    setMoreToday(next)
    setSavingMore(true)
    try {
      await setMyReadyForMore(next)
      toast(
        next
          ? "You'll get extra leads today, once everyone has had their share"
          : 'No more extra leads today',
      )
    } catch {
      setMoreToday(previous)
      toast.error('Could not change that — try again')
    } finally {
      setSavingMore(false)
    }
  }

  /** Stopping and restarting the broker's own leads. Immediate both ways.
   *
   *  STOPPING ASKS FIRST. This is not a preference — it is the broker's own
   *  income, and every lead that arrives while they are off goes to somebody
   *  else and does not come back. A control that can do that on one stray
   *  click is the wrong control, which is why the switch this replaced is
   *  gone: the dialog states the consequence in the words that matter ("new
   *  leads will go to other brokers in your group") before anything is saved.
   *
   *  RESUMING DOES NOT ASK. Turning your own leads back on has no downside to
   *  warn about, and a confirmation there would only be friction in front of
   *  the thing we want to be effortless.
   *
   *  Optimistic and rolled back on failure, like takeMoreToday above — and
   *  here the rollback matters more: a control left showing "paused" when the
   *  server never saved it means the broker believes no leads are coming while
   *  they are still arriving.
   *
   *  A FAILED DRAIN IS NOT A FAILED SAVE. Resuming can succeed while the leads
   *  already waiting could not be handed out; the backend reports that as
   *  backlog_drained: false with a 200, precisely so this function does not
   *  roll the control back over a change that was kept. The broker is told
   *  what actually happened instead. */
  async function setTakingLeads(takingLeads: boolean) {
    const next = !takingLeads

    if (next) {
      const ok = await confirm({
        title: 'Stop receiving new leads?',
        description:
          'New leads will go to other brokers in your group instead, and they will not come back to you. Your leads already in progress are unaffected. You can start again at any time — but this stays off until you do. It does not switch back on overnight.',
        confirmText: 'Stop my leads',
        cancelText: 'Cancel',
      })
      if (!ok) return
    }

    const previous = paused
    setPaused(next)
    setSavingPaused(true)
    try {
      const result = await setMyAvailability(next)
      if (next) {
        // Says the persistent part again, at the moment it takes effect. The
        // dialog above is read once; this is what a broker sees on the way
        // out, and "until you switch back on" is the fact that costs money if
        // it is not remembered.
        toast('Leads stopped. They stay off until you switch back on.')
      } else if (result.backlog_drained) {
        toast("You're receiving leads again")
      } else {
        // The pause change was saved; only the backlog handover failed. Said
        // plainly, because the broker cannot fix it themselves.
        toast.warning(
          "You're receiving new leads, but the ones already waiting could not " +
            'be handed out — ask an admin to reassign them.',
        )
      }
    } catch {
      setPaused(previous)
      toast.error('Could not change that — try again')
    } finally {
      setSavingPaused(false)
    }
  }

  // The REAL signed-in broker, for the avatar and the menu's name line.
  // Identity is not a thing to mock on screen — the header was showing a name
  // and a stock face belonging to nobody.
  //
  // Quiet on failure: a header that cannot name you is a degraded header, not
  // an error worth interrupting the screen for, and every other call on the
  // page will surface the same outage more usefully.
  const [me, setMe] = useState<MyProfile | null>(null)
  useEffect(() => {
    let cancelled = false
    getMyProfile()
      .then((p) => {
        if (cancelled) return
        setMe(p)
        // Seeded from the same fetch, so the switch shows the broker's real
        // choice on load rather than defaulting to off and lying until they
        // touch it.
        setMoreToday(p.ready_for_more)
        // Same reasoning, and it matters more here: a switch defaulting to
        // "taking leads" while the broker is genuinely paused would tell them
        // work is coming when none is.
        setPaused(p.self_paused)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    document.title = `${section} · MyDay CRM`
  }, [section])

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="flex h-(--height-bar) shrink-0 items-center border-b bg-card shadow-xs">
        {/* This first slot is pinned to the SAME width as the lead list below
            it (--width-lead-list), so whatever comes after it — the lead's
            name in `context` — starts at exactly the x where the detail pane
            starts. Without this, the header spans the full window width above
            a list+detail split it knows nothing about, and the name lands
            wherever the brand mark and nav happen to end instead of over the
            pane it belongs to.

            Was --width-stage-rail until the stage filter moved from a left
            rail to a strip across the top; with no rail there, the list is
            what the header has to line up with. */}
        {/* min-w, not w: the box lines up with the list when the nav fits in
            it, and grows when it does not. A fixed width wrapped the labels
            onto two lines ("My / Day") once MVR Log made it four links. */}
        <div className="flex min-w-(--width-lead-list) shrink-0 items-center gap-2.5 pl-(--space-edge)">
          <Link to="/my-day" aria-label="MyDay CRM — home">
            <span className="size-6 rounded-md bg-primary" aria-hidden />
          </Link>

          <nav className="hidden items-center gap-0.5 md:flex">
            <ShellLink to="/my-day" active={pathname === '/my-day'}>
              My Day
            </ShellLink>
            <ShellLink to="/leads" active={pathname.startsWith('/leads')}>
              Leads
            </ShellLink>
            <ShellLink to="/reminders" active={pathname.startsWith('/reminders')}>
              Reminders
            </ShellLink>
            <ShellLink to="/mvr-log" active={pathname.startsWith('/mvr-log')}>
              MVR Log
            </ShellLink>
            {/* Admins only. Every /admin route is gated by require_admin on
                the backend, so a broker seeing this link would only reach a
                403 — and the whole area is theirs to run, not to look at. */}
            {session?.role === 'admin' && (
              <ShellLink to="/admin" active={pathname.startsWith('/admin')}>
                Admin
              </ShellLink>
            )}
          </nav>
        </div>

        {/* Always present, even with no context: this is what keeps the
            right-hand cluster (the opt-in, notifications, avatar) pinned to
            the far edge on every screen, not just Leads. Left padding is left
            to the context content itself (see LeadHeader), not added here —
            it needs to match that content's own layout, not a fixed guess. */}
        <div className="flex min-w-0 flex-1 items-center pr-4">{context}</div>

        <div className="flex shrink-0 items-center gap-1.5 pr-(--space-edge)">
          {actions && (
            <>
              {actions}
              <div className="bg-border mx-1 h-6 w-px" aria-hidden />
            </>
          )}

          {/* THE MAIN TAP: whether this broker is receiving leads at all.
              Sits BEFORE "More today" because it governs it — overtime is
              meaningless while nothing is arriving in the first place.

              Reads "Taking leads" and is ON when leads are flowing, though the
              API speaks in `paused`. A switch that must be turned ON to stop
              something is the wrong way round for a person; the inversion
              lives in setTakingLeads and nowhere else.

              Turning it off is INSTANT, and so is turning it back on — the
              reason this exists rather than the admin block, which needs
              somebody else and only lifts the next morning.

              PERSISTS, unlike "More today" beside it. A pause means away or
              overloaded — states that outlast a night — and clearing it every
              morning made the switch undo itself before the broker was back.
              The guard against forgetting is no longer a nightly undo but
              visibility: an admin sees who is paused and for how long on the
              group roster, and can switch them back on. */}
          {me && me.role !== 'admin' && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={savingPaused}
                  className={cn(
                    'h-8 gap-1.5 px-2.5 text-xs font-medium',
                    // RED, AND ONLY WHEN STOPPED. The paused state is the one
                    // that costs money silently — a broker who does not notice
                    // it loses every lead of the day to a colleague. Border,
                    // text and a tinted fill together, because a colour change
                    // alone is not enough for anyone who cannot distinguish it.
                    paused && 'border-error text-error bg-error/5 hover:bg-error/10',
                  )}
                  aria-label={
                    paused
                      ? 'You are not receiving leads. Open to start again.'
                      : 'You are receiving leads. Open to stop.'
                  }
                >
                  {paused ? (
                    <PauseCircle className="size-3.5" />
                  ) : (
                    <CircleDot className="text-success size-3.5" />
                  )}
                  {/* The words are hidden on a narrow screen ONLY when leads
                      are flowing. Paused always says so in full: a lone red
                      icon is not a message, and this is the state a broker
                      must never fail to notice. */}
                  <span className={cn(!paused && 'hidden sm:inline')}>
                    {paused ? 'Not taking leads' : 'Taking leads'}
                  </span>
                  <ChevronDown className="size-3 opacity-60" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
                  {paused
                    ? 'New leads are going to other brokers in your group, and will until you switch back on.'
                    : "You're in the rotation for new leads."}
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  disabled={savingPaused || !paused}
                  onSelect={() => setTakingLeads(true)}
                  className="gap-2"
                >
                  <CircleDot className="text-success size-4" />
                  <span className="flex-1">Take leads</span>
                  {!paused && <span className="text-muted-foreground text-xs">Current</span>}
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={savingPaused || paused}
                  onSelect={() => setTakingLeads(false)}
                  className="text-error focus:text-error gap-2"
                >
                  <PauseCircle className="size-4" />
                  <span className="flex-1">Stop my leads</span>
                  {paused && <span className="text-muted-foreground text-xs">Current</span>}
                </DropdownMenuItem>

                {/* APPEARANCE. Three items rather than one switch: 'system'
                    is a real third state (follow the OS), and a binary toggle
                    would have to hide it. Same "Current" marker the lead
                    items use, so the menu reads as one idiom. Lives in this
                    menu because it is a preference about the person's own
                    screen, like whether they are taking leads — not a page
                    action. */}
                <DropdownMenuSeparator />
                <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
                  Appearance
                </DropdownMenuLabel>
                {(
                  [
                    ['system', 'Match system', Monitor],
                    ['light', 'Light', Sun],
                    ['dark', 'Dark', Moon],
                  ] as const
                ).map(([value, label, Icon]) => (
                  <DropdownMenuItem
                    key={value}
                    onSelect={() => setTheme(value as Theme)}
                    className="gap-2"
                  >
                    <Icon className="size-4" />
                    <span className="flex-1">{label}</span>
                    {theme === value && (
                      <span className="text-muted-foreground text-xs">Current</span>
                    )}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          {/* The Group-2 opt-in: whether extra leads reach this broker once
              everyone in the group has had their share (round-robin design
              §1, §8). An unwired "Available" switch used to sit beside it and
              was removed — presence never gated assignment, so it was a
              control that looked like it did something and did not.

              "today" is load-bearing: the flag is cleared by tomorrow's
              morning reset, and a broker who expects it to persist would think
              it broke overnight.

              Admins never appear here — migrations/0074 removed them from the
              round-robin, so the control would be inert. */}
          {me && me.role !== 'admin' && (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="flex items-center gap-1.5">
                  <Switch
                    checked={moreToday}
                    disabled={savingMore}
                    onCheckedChange={takeMoreToday}
                    aria-label={
                      moreToday
                        ? 'Taking extra leads today — click to stop'
                        : 'Not taking extra leads — click to take more today'
                    }
                  />
                  <span className="text-muted-foreground hidden text-xs sm:inline">
                    More today
                  </span>
                </span>
              </TooltipTrigger>
              <TooltipContent>
                {moreToday
                  ? "You'll get extra leads today. Resets tomorrow morning."
                  : 'Take extra leads today, once everyone has had their share.'}
              </TooltipContent>
            </Tooltip>
          )}

          <div className="bg-border mx-1 h-6 w-px" aria-hidden />

          {/* ONE CLICK FLIPS THE THEME. The profile menu still holds the full
              three-way choice (it is the only way to pick "match system"),
              but a switch people expect to SEE should not be hidden in a
              dropdown. This flips the EFFECTIVE mode — resolve() — so a broker
              on "match system" who is currently seeing light gets dark on the
              first click, rather than a no-op. That sets an explicit choice
              and quietly leaves system mode; the menu offers the way back.

              THE ICON IS THE DESTINATION, not the current state: a moon means
              "go dark". That is the convention every OS uses, and the other
              reading — a sun meaning "it is light now" — makes the button
              look like a status light rather than a control. */}
          {/* The first-visit "what's new" bubble anchors HERE, to the control
              it is about, rather than interrupting the screen with a modal. */}
          <Tooltip>
            <TooltipTrigger asChild>
              {/* The callout anchors to the BUTTON, inside the tooltip. It sat
                  outside, wrapping <Tooltip> — which renders no DOM element
                  at all (a Provider around a Root), so PopoverAnchor had
                  nothing to measure and the bubble never positioned. Two
                  Radix wrappers with asChild forward to the same element,
                  which is what both need. */}
              <WhatsNewCallout>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={resolve(theme) === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                  onClick={() => setTheme(resolve(theme) === 'dark' ? 'light' : 'dark')}
                >
                  {resolve(theme) === 'dark' ? <Sun /> : <Moon />}
                </Button>
              </WhatsNewCallout>
            </TooltipTrigger>
            <TooltipContent>
              {resolve(theme) === 'dark' ? 'Light mode' : 'Dark mode'}
            </TooltipContent>
          </Tooltip>

          <Button variant="ghost" size="icon" className="relative">
            <Bell />
            <span className="absolute top-0.5 right-0.5 flex size-3.5 items-center justify-center rounded-full bg-error text-2xs font-semibold text-error-foreground">
              2
            </span>
          </Button>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="focus-visible:ring-ring rounded-full focus-visible:ring-2 focus-visible:outline-none">
                {/* The broker's REAL photo, uploaded by an admin during
                    onboarding. This once showed a stock face from pravatar.cc
                    — the same stranger for every broker, fetched from a third
                    party — which was removed rather than faked.

                    photo_url is a short-lived signed link, so it is never
                    stored; it arrives with the profile and expires. Initials
                    remain the fallback for every broker without a photo, and
                    also when storage cannot be reached — the backend returns
                    null rather than failing the profile over an avatar. */}
                <Avatar className="size-8">
                  {me?.photo_url && <AvatarImage src={me.photo_url} alt="" />}
                  <AvatarFallback>{initialsOf(me?.name)}</AvatarFallback>
                </Avatar>
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              <DropdownMenuLabel>
                {me ? `${me.name} · ${me.role}` : '—'}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setProfileOpen(true)}>
                <User /> Profile
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setChangePasswordOpen(true)}>
                <KeyRound /> Change password
              </DropdownMenuItem>
              {/* In the profile menu rather than the nav: a broker checks this
                  once a month, and a permanent tab for it would sit beside My
                  Day and Leads implying it is somewhere they work. */}
              <DropdownMenuItem onClick={() => navigate('/my-lead-cost')}>
                <DollarSign /> Lead cost
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => {
                  logout()
                  navigate('/login', { replace: true })
                }}
              >
                <LogOut /> Log out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-auto">{children}</main>

      <ChangePasswordDialog open={changePasswordOpen} onOpenChange={setChangePasswordOpen} />
      <ProfileDialog open={profileOpen} onOpenChange={setProfileOpen} />
    </div>
  )
}

function ShellLink({
  to,
  active,
  children,
}: {
  to: string
  active: boolean
  children: ReactNode
}) {
  return (
    <Link
      to={to}
      className={cn(
        'inline-flex h-(--height-control) items-center rounded-md px-3 text-sm font-medium whitespace-nowrap transition',
        active
          ? 'bg-accent text-accent-foreground'
          : 'text-muted-foreground hover:text-foreground',
      )}
    >
      {children}
    </Link>
  )
}
