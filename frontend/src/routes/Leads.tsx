import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import axios from 'axios'
import { useConfirm } from '@/components/ConfirmDialog'
import { AppShell } from '@/components/AppShell'
import {
  getMyLeads,
  getMyLead,
  updateLeadStage,
  setLeadQualification,
  getQualificationReadiness,
  type QualificationReadiness,
  getLeadPremium,
  // The applicant row, fetched when the qualification dialog opens: convictions
  // are a fact about the PERSON, so they save against the driver rather than the
  // lead. getLeadDriver creates the row if this lead has never been opened, so
  // there is always something to write to.
  getLeadDriver,
  updateDriver,
  adjustCallCount as adjustCallCountApi,
  createReminder,
  firstValidationMessage,
  type LeadPremium,
  type MyLead,
  type MyLeadCounts,
} from '@/data/api'
import QualifyLeadDialog, { type QualifyAnswer } from '@/components/QualifyLeadDialog'
import LostReasonDialog from '@/components/LostReasonDialog'
import IssuedDialog, { type IssuedPremiumLine } from '@/components/IssuedDialog'
import { isLeadStage, isSubStageChip, parseStageValue } from '@/domain/leadStages'
import { StageFilter } from './leads/StageFilter'
import { LeadList } from './leads/LeadList'
import { presetLabel } from './leads/callbackPresets'
import { LeadDetail } from './leads/LeadDetail'
import { LeadHeader } from './leads/LeadHeader'

// The broker's own Leads screen — three panes: stage rail · list · detail.
// Real data (GET /leads/mine), server-side stage/search/paging. The URL is the
// source of truth for stage + search, so back/forward and shared links
// reproduce the view. Filter changes keep the previous rows visible (dimmed)
// until the new page lands, so switching stages feels instant despite being a
// real round-trip.

const EMPTY_COUNTS: MyLeadCounts = { all: 0 }

// The broker judges a Meta lead on the way into Quote Done. The other four
// stages are here because stages are FREE-FORM — a lead can go New -> Sold
// without ever passing through Quote Done, and this list is what stops it
// arriving there unjudged. 'Lost' is absent: a dead lead must always be
// closable.
//
// The backend refuses these with a 422 regardless
// (services/lead.py::_STAGES_NEEDING_QUALIFICATION) — this list only decides
// when to ASK, so the broker is not simply rejected and left guessing.
//
// IN PROGRESS IS THE POINT OF ASKING NOW. Meta's CompleteRegistration used to
// fire at Quote Done, which is days later — a quote takes real work, so the ad
// model waited that long to learn anything. It fires at In Progress instead, so
// the judgement has to be collected there.
const NEEDS_QUALIFYING = new Set([
  'In Progress',
  'Quote Done',
  'Pending',
  'Sold',
  'Submitted',
  'Issued',
])

// Meta leads only (nothing else has a conversion to report), and only while
// unanswered — `false` is an answer, so it does not ask again.
//
// TAKES THE SUB-STAGE TOO, and it has to. In Progress is still a sub-stage of
// New (migration 0092), so the dropdown value 'New · In Progress' splits into
// stage='New' + subStage='In Progress' — and passing only the stage meant this
// function was asked about 'New', which is in no qualifying set and never
// could be. Adding 'In Progress' to the list above without this is a one-line
// change that does nothing at all.
function needsQualifying(
  lead: MyLead,
  targetStage: string,
  targetSubStage?: string | null,
): boolean {
  // Either half can name a qualifying destination: the sub-stage while In
  // Progress lives under New, the stage itself for Quote Done and beyond.
  const destination =
    targetSubStage && NEEDS_QUALIFYING.has(targetSubStage) ? targetSubStage : targetStage
  return (
    lead.source === 'meta' && lead.is_qualified === null && NEEDS_QUALIFYING.has(destination)
  )
}

// WHICH DESTINATIONS DEMAND THE FULL APPLICANT before the question is even
// asked. Quote Done and later do: those events carry the match keys Meta needs,
// and a broker judging a customer they have not written down yet is the wrong
// order.
//
// IN PROGRESS DELIBERATELY DOES NOT. Requiring all nine fields to reach it
// would recreate exactly the late-stage delay that moving the signal earlier is
// meant to remove — the lead would sit at New until the whole form was typed,
// which is where it sits today.
const NEEDS_FULL_APPLICANT = new Set([
  'Quote Done',
  'Pending',
  'Sold',
  'Submitted',
  'Issued',
])
const PAGE_SIZE = 50
// How often the visible inbox re-checks for newly assigned leads. 20s is the
// balance point: fast enough that a lead feels like it "arrives" rather than
// being found, slow enough that a broker sitting on this screen all day costs
// ~180 cheap queries an hour rather than one per second.
const POLL_INTERVAL_MS = 20_000

export default function Leads() {
  const [params, setParams] = useSearchParams()
  // The low-premium question at Quote Done — see the 409 branch in the stage
  // handler below.
  const confirm = useConfirm()

  // URL-derived filter state. '' stage = "All leads".
  const rawStage = params.get('stage') ?? ''
  // A rail chip is a real stage OR one of the two labels shown as stages
  // ('In Progress', 'Junk'). Both are sent to the backend as ?stage=, which
  // translates a label into a stage plus a sub_stage filter — so this screen
  // never has to hold two filter params, and a bookmarked ?stage=In Progress
  // works like any other.
  const stage = isLeadStage(rawStage) || isSubStageChip(rawStage) ? rawStage : ''
  const search = params.get('q') ?? ''
  const page = Math.max(1, Number(params.get('page')) || 1)

  const [leads, setLeads] = useState<MyLead[]>([])
  const [counts, setCounts] = useState<MyLeadCounts>(EMPTY_COUNTS)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [refetching, setRefetching] = useState(false)
  // A failed load is NOT an empty book. Without this the catch below empties
  // the list and the pane reads "No leads in All leads" — a broker whose
  // connection blipped would reasonably conclude they have no work.
  const [loadFailed, setLoadFailed] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [stageSaving, setStageSaving] = useState(false)
  // A call-counter request is in flight. Both steppers disable while it is,
  // so a double click cannot fire two increments — the server would count
  // both, since each press is a delta rather than a total.
  const [callCountSaving, setCallCountSaving] = useState(false)

  // A LEAD NAMED BY ?lead=, FETCHED BY ID.
  //
  // The renewal WhatsApp's "Set Up Reminder" button links straight to one
  // customer, and /leads/mine is paginated — so the lead it names is very
  // often not on the page the broker lands on. Finding it in `leads` would
  // work only by luck. Held separately for the same reason movedLead is: the
  // list is right to not show it, the pane still has to.
  const [deepLinked, setDeepLinked] = useState<MyLead | null>(null)
  // Whether this arrival should also open the reminder form. Held in state
  // rather than read from the URL at render time, because the param is cleared
  // the moment the dialog opens and the intent must survive that.
  const [openReminder, setOpenReminder] = useState(false)

  // THE LEAD THE BROKER JUST MOVED OUT OF THE FILTER THEY ARE LOOKING AT.
  //
  // Moving one to a stage the rail is not showing drops it from the next
  // refetch, and both the list and the detail pane read from that same array
  // — so the pane went blank, or silently swapped to whoever happened to be
  // first in the list. A broker who moves New -> Quote Done while filtered to
  // New loses the customer they are in the middle of working on.
  //
  // Held separately rather than kept in `leads`: the list SHOULD stop showing
  // it, because it genuinely no longer matches the filter. Only the pane
  // keeps it, and only until the broker moves on — see where this is cleared.
  const [movedLead, setMovedLead] = useState<MyLead | null>(null)
  // MIRRORED ON A REF because `load` is a useCallback keyed on the filters.
  // Reading the state inside it would either close over a stale value or, if
  // added to the dependency list, rebuild `load` on every move — which the
  // effect below would then run as a fresh fetch. The ref is read
  // synchronously and changes nothing about when `load` is recreated. Same
  // reasoning as stageSavingRef further down.
  const movedLeadRef = useRef<MyLead | null>(null)
  function rememberMoved(lead: MyLead | null) {
    movedLeadRef.current = lead
    setMovedLead(lead)
  }

  // Debounce the search box into the URL (300ms) so typing isn't one request
  // per keystroke.
  const [searchDraft, setSearchDraft] = useState(search)
  useEffect(() => setSearchDraft(search), [search])
  useEffect(() => {
    if (searchDraft === search) return
    const t = setTimeout(() => setFilter({ q: searchDraft || null }), 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchDraft])

  const firstLoad = useRef(true)
  // `silent` = a background poll the broker didn't ask for. A filter change is
  // a reply to a click and dims the list to show the click registered; a poll
  // has no click to acknowledge, so dimming it just makes the screen blink on
  // a timer at someone trying to read it.
  const load = useCallback((opts?: { silent?: boolean }) => {
    if (firstLoad.current) setLoading(true)
    else if (!opts?.silent) setRefetching(true)
    getMyLeads({ stage: stage || undefined, search: search || undefined, page, limit: PAGE_SIZE })
      .then((res) => {
        setLoadFailed(false)
        setLeads(res.rows)
        setCounts(res.counts ?? EMPTY_COUNTS)
        setTotal(res.total)
        // Keep the selection if it's still in view; otherwise select the first.
        //
        // THE ONE EXCEPTION is a lead the broker just moved out of this
        // filter. It is legitimately absent from res.rows, and reselecting
        // around it would blank the pane they are working in. Everything else
        // — first load, filter change, search, paging, the poll — keeps the
        // original rule exactly, which is why this is a guarded fallback
        // rather than a rewrite of it.
        setSelectedId((prev) => {
          if (prev && res.rows.some((l) => l.id === prev)) return prev
          if (prev && movedLeadRef.current?.id === prev) return prev
          return res.rows[0]?.id ?? null
        })
      })
      .catch(() => {
        // A FAILED REFRESH KEEPS WHAT IS ON SCREEN. This used to wipe the
        // list to nothing on any error, so one transient 500 on the reload
        // that follows a stage change emptied the screen the broker was
        // working in until they refreshed by hand. The rows they were looking
        // at were still perfectly good. Only the FIRST load has nothing to
        // fall back to, and only it clears.
        if (firstLoad.current) {
          setLoadFailed(true)
          setLeads([])
          setCounts(EMPTY_COUNTS)
          setTotal(0)
        } else {
          toast.error('Could not refresh the lead list — showing the last good copy')
        }
      })
      .finally(() => {
        setLoading(false)
        setRefetching(false)
        firstLoad.current = false
      })
  }, [stage, search, page])
  // Called, not passed directly: `load` now takes an options object, and as a
  // bare effect callback React would supply its own argument for it.
  useEffect(() => {
    load()
  }, [load])

  // ── Live inbox ───────────────────────────────────────────────────────
  // Leads arrive from Meta on their own schedule, so a broker staring at
  // this screen would otherwise have to refresh to discover work waiting
  // for them. Poll instead: `load` already distinguishes a background
  // refetch from a first load (no skeleton flash) and preserves the
  // selected lead if it's still in view, so a tick that lands while the
  // broker is reading a lead is invisible to them.
  //
  // Deliberately POLLING, not sockets/SSE: one small query per interval is
  // nothing at this scale, it needs no new endpoint or connection state,
  // and it recovers from sleep//reconnect for free — the reconnect handling
  // a push transport would need is the whole cost of that transport.
  //
  // Paused while the tab is hidden (a backgrounded tab is not being read,
  // so its polls are pure waste) and resumed with an immediate refetch on
  // return, so coming back to the tab shows current data rather than
  // whatever was true when it was hidden.
  const stageSavingRef = useRef(false)
  // Same guard for a call-counter press (adjustCallCount). Without it a poll
  // whose response lands AFTER the counter's own response overwrites the
  // fresh count with the stale one it fetched, until the next poll.
  const callCountSavingRef = useRef(false)
  useEffect(() => {
    function tick() {
      // A stage change is optimistic (see changeStage): the row shows the new
      // stage before the server has confirmed it, and calls load() itself when
      // it does. A poll landing in that window would fetch the not-yet-updated
      // row and visibly revert the broker's own click.
      if (stageSavingRef.current || callCountSavingRef.current) return
      if (document.visibilityState !== 'visible') return
      load({ silent: true })
    }
    const id = setInterval(tick, POLL_INTERVAL_MS)

    // Returning to the tab should show current data immediately, not after
    // waiting out the remainder of an interval that ran while hidden.
    function onVisibility() {
      if (document.visibilityState === 'visible') tick()
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [load])

  /** Patch one or more filter params at once. Several at a time because the
   *  stage chip has to set the stage and clear the tab together — two calls
   *  would have the second build its URL from the stale `params` of this
   *  render and undo the first. */
  function setFilter(patch: Record<string, string | null>) {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    next.delete('page') // any filter change resets to page 1
    // Changing the filter IS moving on. Holding the moved lead past this
    // would show a Quote Done customer while the rail says Sold, which is
    // the confusion this whole mechanism exists to avoid.
    rememberMoved(null)
    setParams(next, { replace: true })
  }

  // The lead the pane is showing. Normally the selected row in the list —
  // and, ONLY when that row has left the filter because the broker just moved
  // it, the copy held above. Guarded on the id so a stale movedLead can never
  // display over a different selection.
  //
  // deepLinked is the third source: a lead named by ?lead= that is not on this
  // page at all. Last, so a row actually in the list always wins.
  const selected =
    leads.find((l) => l.id === selectedId) ??
    (movedLead && movedLead.id === selectedId ? movedLead : null) ??
    (deepLinked && deepLinked.id === selectedId ? deepLinked : null)

  // ?lead=<uuid> — ARRIVING FROM A RENEWAL WHATSAPP.
  //
  // Fetched BY ID rather than looked for in `leads`: /leads/mine is paginated
  // and the lead a link names is usually not on the page the broker lands on,
  // so searching the list would work only by luck.
  //
  // The param is consumed immediately — selected, then stripped from the URL.
  // Leaving it would re-select on every render and fight the broker the moment
  // they click a different row.
  const deepLinkId = params.get('lead')
  useEffect(() => {
    if (!deepLinkId) return

    setSelectedId(deepLinkId)
    // Arriving this way always means the reminder form: it is the only button
    // that links here.
    setOpenReminder(true)

    const next = new URLSearchParams(params)
    next.delete('lead')
    setParams(next, { replace: true })

    let cancelled = false
    getMyLead(deepLinkId)
      .then((lead) => {
        if (!cancelled) setDeepLinked(lead)
      })
      .catch(() => {
        // Not this broker's lead, or gone. Quiet: the pane falls back to its
        // empty state, which says more than an error about a link the broker
        // did not type.
        if (!cancelled) setDeepLinked(null)
      })
    return () => {
      cancelled = true
    }
    // deepLinkId only — params and setParams are stable enough here, and
    // including them would re-run this on every unrelated filter change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepLinkId])

  // The lead waiting on a qualified/not-qualified answer, and the stage the
  // broker was trying to reach. Both null when the dialog is closed. The
  // TARGET is held here because the answer has to resume the move that
  // triggered it — asking and then making them click the stage again would be
  // its own small insult.
  // subStage travels with the target so the answer can resume the EXACT move
  // that triggered it. Without it, answering on the way into 'New · In
  // Progress' would resume into bare 'New' — the label silently dropped, and
  // the lead left sitting where it started after the broker had just answered
  // the questions to move it.
  const [qualifying, setQualifying] = useState<{
    lead: MyLead
    stage: string
    subStage?: string | null
    /** The applicant's id, resolved when the dialog opens. Convictions are a
     *  fact about the PERSON, so they save against the driver rather than the
     *  lead — and the row is created on demand, so this is always available by
     *  the time a step can be answered. */
    driverId?: string | null
    /** What is already recorded, so reopening shows the broker their own
     *  answers rather than a blank form. Null means unanswered — not zero. */
    savedConvictions3y?: number | null
    savedLicensed5yPlus?: boolean | null
  } | null>(null)

  // The lead on its way into Lost, waiting to be told whether it was a real
  // opportunity or junk. Null when the dialog is closed. Only the lead is
  // held — unlike `qualifying`, the target stage is always Lost.
  const [closing, setClosing] = useState<MyLead | null>(null)
  const [qualifySaving, setQualifySaving] = useState(false)

  // The Issued confirmation: which lead, and the premiums to show it back.
  // Fetched when the dialog opens rather than held for every lead — this
  // screen otherwise has no use for premium figures.
  const [issuing, setIssuing] = useState<{
    lead: MyLead
    premiums: IssuedPremiumLine[]
    // The date already on the row, when this is a correction. Null on a first
    // issue, which is what makes the dialog default to today.
    currentIssuedAt: string | null
  } | null>(null)

  async function changeStage(picked: string) {
    // The dropdown speaks in stage-and-label pairs ('New · In Progress'), so
    // split them apart before anything below reasons about the stage. A bare
    // stage decodes to a null label, which is every value that existed before
    // sub-stages did.
    const { stage: newStage, subStage: newSubStage } = parseStageValue(picked)

    // Re-picking what the lead already is, is nothing to do — and it takes
    // BOTH columns to know that. Comparing the stage alone would swallow
    // New -> New · In Progress, which is a real change and the only way a lead
    // becomes In Progress at all.
    // Correcting an issued date does NOT come through here — it has its own
    // button, which calls openIssuedDialog directly (see onEditIssuedDate).
    if (!selected) return
    if (newStage === selected.stage && newSubStage === (selected.sub_stage ?? null)) return

    // ASK FIRST. A Meta lead cannot enter Quote Done (or anything past it)
    // until it has been judged — the backend refuses with a 422 either way, so
    // this is about asking at the moment the broker is already thinking about
    // this lead, rather than refusing them and making them find out why.
    //
    // THE JUDGEMENT NO LONGER WAITS ON THE APPLICANT. set_qualification used to
    // refuse until all nine fields were filled, and this screen mirrored that by
    // checking first. Both were right while the question was asked on the way
    // into Quote Done; both were wrong once it moved to In Progress, which
    // exists so a qualified lead reaches Meta before the form is finished. The
    // applicant check below now runs only for the later stages, where
    // update_stage still enforces it.
    //
    // POLICY TYPE IS REQUIRED TO BE JUDGED, and it is answered on the New Policy
    // page rather than in the dialog — one control, one place. Checked before
    // the dialog opens, so a broker is not walked through four questions and
    // then refused for something they answer on a different screen.
    //
    // Measured before adding this: 114 of 162 Meta leads have no policy type,
    // every one of them at New or Lost — so it was first demanded at Quote Done,
    // through the premium validation. This moves that forward.
    //
    // STILL PRESENTATION, NOT THE GUARANTEE. The backend refuses In Progress
    // without the qualification judgement (0115 added it to
    // _STAGES_NEEDING_QUALIFICATION), but policy type itself has no server-side
    // stage guard — it is demanded here so the broker is asked on the screen
    // that owns the control, rather than refused later for something they answer
    // elsewhere.
    if (needsQualifying(selected, newStage, newSubStage) && !selected.combined_policy_type) {
      toast.error('Set the policy type first — it is on the New Policy page.')
      return
    }

    if (needsQualifying(selected, newStage, newSubStage)) {
      // WHAT THE SERVER HAS ALREADY DECIDED, asked before anything is put to the
      // broker. One call answers both questions below.
      //
      // A FAILED LOOKUP DOES NOT BLOCK. update_stage enforces both rules itself
      // and will refuse with the real reason, which is better than stopping a
      // broker because a read failed.
      let readiness: QualificationReadiness | null = null
      try {
        readiness = await getQualificationReadiness(selected.id)
      } catch {
        readiness = null
      }

      // THE FOUR-YEAR RULE HAS ALREADY ANSWERED. Asking now would put a question
      // to the broker that update_stage is about to overwrite — they would tap
      // Qualified, and the move would record false a moment later with nothing
      // to show they had been overruled.
      //
      // So the move goes straight through. The backend writes is_qualified =
      // false, the lead lands on its new stage, and the broker carries on
      // working it — only Meta is kept out of it.
      if (readiness?.insurance === 'too_recent') {
        await applyStage(selected, newStage, undefined, newSubStage)
        return
      }

      // NO DATE, SO NOTHING CAN BE DECIDED. update_stage refuses this, and
      // saying so here means the broker learns what to fix before answering
      // three questions that will be thrown away.
      if (readiness?.insurance === 'missing') {
        toast.error(
          `Add the applicant's Continuous Insurance date first — ` +
            `it decides whether this lead can be reported.`,
        )
        return
      }

      // THE APPLICANT CHECK IS CONDITIONAL. It used to run for every qualifying
      // destination and return early on any missing field, so a lead one field
      // short never reached the dialog at all — correct for Quote Done, and
      // fatal for In Progress, where the whole point is to judge the lead before
      // the form is finished.
      if (NEEDS_FULL_APPLICANT.has(newStage) && readiness?.missing.length) {
        toast.error(
          `Complete the applicant first — missing: ${readiness.missing.join(', ')}.`,
        )
        return
      }
      // THE APPLICANT, because convictions save against the driver rather than
      // the lead. getLeadDriver is a GET that creates the row when a lead has
      // never been opened, so this is also what guarantees there is something to
      // write to — and it hands back whatever was already answered, which is
      // what lets the dialog reopen showing the broker's own work.
      //
      // A FAILED LOOKUP STILL OPENS THE DIALOG. The judgement is the answer the
      // stage move is waiting on, and it saves against the lead — so a driver
      // fetch that fails must not block it. The convictions step reports its own
      // failure if it comes to that.
      let driverId: string | null = null
      let savedConvictions3y: number | null = null
      let savedLicensed5yPlus: boolean | null = null
      try {
        const applicant = await getLeadDriver(selected.id)
        driverId = applicant.id
        savedConvictions3y = applicant.convictions_3y
        savedLicensed5yPlus = applicant.licensed_5y_plus
      } catch {
        driverId = null
      }
      // The sub-stage travels with the target, so answering resumes the exact
      // move the broker asked for — New · In Progress, not bare New.
      setQualifying({
        lead: selected,
        stage: newStage,
        subStage: newSubStage,
        driverId,
        savedConvictions3y,
        savedLicensed5yPlus,
      })
      return
    }

    // ISSUED ASKS FOR ITS DATE — see openIssuedDialog. AFTER the qualification
    // check above, matching the backend's own order: a Meta lead going
    // straight to Issued is judged first, dated second.
    if (newStage === 'Issued') {
      await openIssuedDialog(selected)
      return
    }

    // LOST ASKS WHICH KIND. A real opportunity that did not convert and a
    // spam/duplicate lead both end up here, and telling them apart afterwards
    // is impossible — so the question is put at the one moment the broker
    // knows the answer. Answered in closeLost, which resumes the move.
    //
    // ONLY WHEN THE ANSWER IS NOT ALREADY IN THE PICKED VALUE. The dropdown
    // also offers 'Lost · Junk' outright, and asking "Lost or Junk?" of a
    // broker who just picked Junk is a question with one answer.
    if (newStage === 'Lost' && !newSubStage) {
      setClosing(selected)
      return
    }

    // Whatever label the picked value carried — 'In Progress', or null for
    // the stages that take none, which is what clears one left over from a
    // previous Lost or In Progress.
    await applyStage(selected, newStage, undefined, newSubStage)
  }

  /** Open the Issued confirmation for a lead.
   *
   *  ITS OWN FUNCTION BECAUSE THERE ARE THREE WAYS IN, and one of them used to
   *  miss it. answerQualification resumes an interrupted move by calling
   *  applyStage directly, so a Meta lead sent straight to Issued was judged,
   *  then submitted with no date — and the backend refused it with a 422 the
   *  broker had to clear by clicking Issued a second time. The third way in is
   *  the edit action on a lead that is already Issued.
   *
   *  The backend refuses Issued without a date (422), and the date is the
   *  broker's to give: a policy the carrier wrote on the 30th and recorded on
   *  the 2nd belongs in the earlier month. The dialog shows the premiums back
   *  at the same time, so the figures get one last look before the policy is
   *  recorded as written. */
  async function openIssuedDialog(lead: MyLead) {
    // A FAILED FETCH STOPS HERE. This used to fall back to an empty premium
    // list, which rendered a dialog with no figures in it — indistinguishable
    // from a policy that genuinely has none, and asking the broker to confirm
    // figures it had failed to load. Refusing to open is the honest answer.
    let premium: LeadPremium | null
    try {
      premium = await getLeadPremium(lead.id)
    } catch {
      toast.error('Could not load the premium — try again')
      return
    }
    // Only treat a stored date as "current" when the lead is actually Issued.
    // A lead moved off Issued and back has a cleared date (0085), but a stale
    // one on any other stage must not preselect itself.
    setIssuing({
      lead,
      premiums: premiumLines(premium),
      currentIssuedAt: lead.stage === 'Issued' ? (premium?.issued_at ?? null) : null,
    })
  }

  /** The premium figures to show back in the Issued dialog — only the ones
   *  this policy actually has. An empty list renders nothing rather than a row
   *  of dashes, because a policy with no premium cannot reach Issued anyway
   *  (the backend refuses it) and an empty box would read as a bug.
   *
   *  THE ANNUAL COLUMNS ARE GENUINELY ANNUAL SINCE 0106, so labelling these
   *  "annual" is finally true. Before that they held whatever the broker
   *  typed, and this dialog printed a monthly premium under a yearly label at
   *  the exact moment somebody confirmed the policy — the reading that goes
   *  on to be the Meta conversion value and the commission base. Unchanged
   *  code, correct for a new reason. */
  function premiumLines(premium: LeadPremium | null): IssuedPremiumLine[] {
    if (!premium) return []
    return (
      [
        ['Auto', premium.new_auto_premium_annual],
        ['Home', premium.new_homeowner_premium_annual],
        ['Tenant', premium.new_tenant_premium_annual],
      ] as const
    )
      .filter(([, annual]) => annual != null && annual !== '')
      .map(([label, annual]) => ({ label, annual: String(annual) }))
  }

  /** Confirmed on the dialog: run the move, carrying the date. */
  async function confirmIssued(issuedAt: string) {
    if (!issuing) return
    const { lead } = issuing
    setIssuing(null)
    await applyStage(lead, 'Issued', issuedAt)
  }

  /** The stage move itself, once nothing is standing in its way. Split out of
   *  changeStage so the qualification dialog can resume the exact move that
   *  triggered it. */
  /** Set a callback reminder on the selected lead.
   *
   * Thin on purpose: the reminder backend already does the work — kind
   * 'Callback' is its default, and a DB trigger (0122) queues the WhatsApp
   * that arrives on the broker's own phone when it comes due.
   *
   * THE NOTE IS GENERATED, not typed. The API requires one at three layers
   * (Pydantic min_length, a non-blank validator, and a DB CHECK), and this
   * popover is deliberately one click with no text field — so the counts
   * write it. It is what the broker reads when the reminder fires.
   *
   * Throws on failure so the popover stays open and the broker can retry;
   * the toast is raised here.
   */
  async function remindToCall(lead: MyLead, at: Date) {
    const tried =
      lead.answered_count + lead.no_answer_count === 0
        ? 'Call back'
        : `Call back — picked up ${lead.answered_count}, no answer ${lead.no_answer_count}`

    try {
      await createReminder(lead.id, {
        due_at: at.toISOString(),
        note: tried,
        kind: 'Callback',
      })
      toast.success(`Reminder set for ${presetLabel(at)}`)
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not set the reminder')
      throw err
    }
  }

  /** One press of a call counter, owned HERE rather than in the stepper.
   *
   * The counts live on the lead row, which feeds both the stepper on the Auto
   * tab and the coloured tally in the list beside it. Updating `leads` moves
   * both at once; letting the stepper hold its own copy would let the two
   * disagree the moment either re-rendered.
   *
   * Optimistic, then corrected: the click paints immediately, and the
   * server's returned pair replaces the guess — never the other way round,
   * because the server clamps at zero and we would not know it had.
   */
  async function adjustCallCount(
    leadId: string,
    outcome: 'answered' | 'no_answer',
    delta: 1 | -1,
  ) {
    const field = outcome === 'answered' ? 'answered_count' : 'no_answer_count'
    const previous = leads.find((l) => l.id === leadId)
    if (!previous) return

    setCallCountSaving(true)
    // The ref as well as state: the poll reads it synchronously, and state
    // is not visible to it until the next render.
    callCountSavingRef.current = true
    setLeads((prev) =>
      prev.map((l) =>
        l.id === leadId ? { ...l, [field]: Math.max(0, l[field] + delta) } : l,
      ),
    )

    try {
      const counts = await adjustCallCountApi(leadId, outcome, delta)
      // The authoritative pair, not our arithmetic.
      setLeads((prev) =>
        prev.map((l) =>
          l.id === leadId
            ? {
                ...l,
                answered_count: counts.answered_count,
                no_answer_count: counts.no_answer_count,
              }
            : l,
        ),
      )
    } catch (err) {
      setLeads((prev) =>
        prev.map((l) =>
          l.id === leadId
            ? {
                ...l,
                answered_count: previous.answered_count,
                no_answer_count: previous.no_answer_count,
              }
            : l,
        ),
      )
      toast.error(firstValidationMessage(err) ?? 'Could not record that call')
    } finally {
      setCallCountSaving(false)
      callCountSavingRef.current = false
    }
  }

  async function applyStage(
    lead: MyLead,
    newStage: string,
    issuedAt?: string,
    // The label to carry INTO the new stage. Undefined on every ordinary move,
    // which sends null and clears whatever the lead had before — see
    // updateLeadStage.
    subStage?: string | null,
  ) {
    setStageSaving(true)
    // Set on the ref too, not just state: the poll reads this synchronously,
    // and state wouldn't be visible to it until the next render — a window in
    // which a tick could still clobber the optimistic update below.
    stageSavingRef.current = true
    const previous = lead.stage
    // optimistic: reflect immediately, roll back on failure. The label moves
    // with the stage, so a Junk lead reads Junk before the server confirms.
    setLeads((prev) =>
      prev.map((l) =>
        l.id === lead.id ? { ...l, stage: newStage, sub_stage: subStage ?? null } : l,
      ),
    )
    try {
      try {
        await updateLeadStage(lead.id, newStage, issuedAt, subStage)
      } catch (err) {
        // 409 = the server is ASKING, not refusing. A proposed auto premium
        // under $1,000 a year is far more likely to be a monthly figure than a
        // real policy, so the move is held until the broker says they meant
        // it. Nothing was written — the check runs before both the premium
        // conversion and the stage write — so declining leaves the lead
        // exactly where it was.
        if (!axios.isAxiosError(err) || err.response?.status !== 409) throw err
        const proceed = await confirm({
          title: 'That premium looks low',
          description: String(
            err.response.data?.detail ?? 'Move this lead to Quote Done anyway?',
          ),
          confirmText: 'Yes, continue',
        })
        if (!proceed) throw err
        await updateLeadStage(lead.id, newStage, issuedAt, subStage, true)
      }
      // "Moved" would be a lie on an issued-date correction — the lead was
      // already Issued and stayed there; only the date changed.
      //
      // BOTH COLUMNS DECIDE THAT, not the stage alone. Comparing only the stage
      // was correct while an Issued-date edit was the one and only way for a
      // move to end on the stage it started from. Sub-stages (0092) broke that:
      // New -> New · In Progress leaves the stage 'New' on both sides, so this
      // announced "Updated the issued date" for a lead that has no issued date,
      // no premium row, and was never Issued.
      //
      // Same rule the early return at the top of changeStage already applies —
      // "it takes BOTH columns to know that" — and an issued-date correction is
      // specifically a lead sitting on Issued.
      const labelChanged = (subStage ?? null) !== (lead.sub_stage ?? null)
      const stageChanged = newStage !== previous
      toast(
        !stageChanged && !labelChanged && newStage === 'Issued'
          ? `Updated the issued date for ${lead.name}`
          : `Moved ${lead.name} to ${subStage ? `${newStage} · ${subStage}` : newStage}`,
      )
      // Remembered BEFORE the refetch below, which is the point: that refetch
      // is what drops this lead from the list when its new stage no longer
      // matches the rail, and the pane needs something to keep showing.
      // Carries the NEW stage so the header and the stage control agree with
      // the move that just happened.
      rememberMoved({ ...lead, stage: newStage, sub_stage: subStage ?? null })
      load() // refresh rail counts + list (the lead may leave the current filter)
    } catch (err) {
      // BOTH restored, not just the stage. The optimistic update above set the
      // label too, so rolling back one and not the other would leave a lead
      // reading its old stage with the new stage's label.
      setLeads((prev) =>
        prev.map((l) =>
          l.id === lead.id ? { ...l, stage: previous, sub_stage: lead.sub_stage } : l,
        ),
      )
      // The move did not happen, so there is nothing out-of-filter to hold.
      // Without this a failed move could leave a stale copy that the pane
      // would show in place of the real, rolled-back row.
      rememberMoved(null)
      const status = axios.isAxiosError(err) ? err.response?.status : null
      if (status === 403) toast.error('This lead is no longer assigned to you')
      else toast.error(firstValidationMessage(err) ?? 'Could not update the stage')
    } finally {
      setStageSaving(false)
      stageSavingRef.current = false
    }
  }

  /** ONE STEP'S ANSWER, saved as it is tapped.
   *
   *  NOT COLLECTED UP AND SENT AT THE END. A mis-click closing the dialog used
   *  to cost every answer given; now each one is already stored, and reopening
   *  shows the broker their own work.
   *
   *  THE JUDGEMENT IS LAST AND IS THE ONE THAT RESUMES THE MOVE. The two facts
   *  before it write and return — the dialog advances itself. Only
   *  `is_qualified` carries on into the stage change, because that is the answer
   *  the move was waiting for.
   *
   *  THROWS ON FAILURE, deliberately: the dialog awaits this and keeps the step
   *  put if it rejects, so a broker is never advanced past an answer that did
   *  not save.
   */
  async function answerQualification(answer: QualifyAnswer) {
    if (!qualifying) return
    // The target stage and its label are read by finishQualifying, which is
    // what resumes the move — both ways of finishing go through it.
    const { lead } = qualifying
    setQualifySaving(true)
    try {
      if ('convictions3y' in answer) {
        // ON THE DRIVER, not the lead — it describes the person. The applicant
        // row is fetched (and created if this lead has never been opened) when
        // the dialog opens, so the id is already to hand.
        if (!qualifying.driverId) throw new Error('No applicant to save against')
        await updateDriver(qualifying.driverId, {
          convictions_3y: answer.convictions3y,
        })
        setQualifying((q) =>
          q ? { ...q, savedConvictions3y: answer.convictions3y } : q,
        )
        return
      }

      if ('licensed5yPlus' in answer) {
        // ON THE DRIVER, like convictions — it describes the person, not the
        // quote.
        if (!qualifying.driverId) throw new Error('No applicant to save against')
        await updateDriver(qualifying.driverId, {
          licensed_5y_plus: answer.licensed5yPlus,
        })
        setQualifying((q) =>
          q ? { ...q, savedLicensed5yPlus: answer.licensed5yPlus } : q,
        )
        // THE LAST QUESTION, so this is where the interrupted move resumes.
        await finishQualifying({ ...lead, is_qualified: true })
        return
      }

      const { isQualified } = answer
      await setLeadQualification(lead.id, { is_qualified: isQualified })
      // Locally too, so a dismissed-then-retried move does not ask twice
      // before the next poll lands.
      setLeads((prev) =>
        prev.map((l) => (l.id === lead.id ? { ...l, is_qualified: isQualified } : l)),
      )

      // QUALIFIED KEEPS THE DIALOG OPEN. The judgement is the FIRST question
      // now, and convictions and vehicle count still have to be asked — so this
      // records the answer and lets the dialog advance.
      if (isQualified) return

      // NOT QUALIFIED ENDS IT HERE. Meta is told nothing about this lead, so the
      // two facts have no reader and asking them would be work thrown away.
      //
      // THE MOVE STILL GOES THROUGH. That is a judgement about Meta, not a
      // reason to refuse the broker their own pipeline.
      await finishQualifying({ ...lead, is_qualified: false })
    } catch (err) {
      toast.error(firstValidationMessage(err) ?? 'Could not save that answer')
      // RETHROWN so the dialog can keep the step where it is. Swallowing it here
      // would advance the broker past an answer that was never recorded.
      throw err
    } finally {
      setQualifySaving(false)
    }
  }

  /** Close the dialog and run the move it interrupted.
   *
   *  ONE PLACE, because there are now two ways to finish: rejecting the lead at
   *  the first question, or answering all three. Both end the same way, and a
   *  second copy of this would be a second chance to forget the Issued case.
   *
   *  ISSUED STILL NEEDS ITS DATE. Resuming straight into applyStage skipped the
   *  date dialog and submitted Issued without one, which the backend refused
   *  with a 422 — the broker answered the questions and was told off for it,
   *  then had to click Issued again.
   */
  async function finishQualifying(moved: MyLead) {
    if (!qualifying) return
    const { stage: target, subStage: targetSubStage } = qualifying
    setQualifying(null)
    if (target === 'Issued') await openIssuedDialog(moved)
    // The sub-stage is carried through, so 'New · In Progress' resumes as that
    // and not as bare 'New'.
    else await applyStage(moved, target, undefined, targetSubStage)
  }

  /** Skip, Escape, or clicking away. Writes NOTHING and abandons the move, so
   *  the lead stays where it was and the question returns next time. */
  function skipQualification() {
    setQualifying(null)
  }

  /** Lost or Junk. Runs the move the dialog interrupted, carrying the answer.
   *
   *  ONE CALL, unlike answerQualification's two: the label rides along on the
   *  stage change itself rather than being a separate judgement written first.
   *
   *  Cancelling closes the dialog and does nothing — the lead stays where it
   *  was, which is the same shape as skipping the qualification question. */
  async function closeLost(subStage: 'Junk' | null) {
    if (!closing) return
    const lead = closing
    setClosing(null)
    await applyStage(lead, 'Lost', undefined, subStage)
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  // Every chip — labels included — is filtered, counted and paged by the
  // server, so this is the real total and not the size of the current page.
  const heading = `${stage || 'All leads'} (${total})`

  return (
    <AppShell
      section="Leads"
      // The selected lead's facts fold into the title bar, so this screen
      // spends ONE row on chrome instead of two.
      //
      // No `actions` slot any more: Call and WhatsApp used to sit at the far
      // right of the bar, three columns from the number they dial. They now
      // live beside it inside LeadHeader, where the thing being acted on is
      // the thing you can see.
      // key by id so React remounts on a different lead, which is what lets
      // the fade run at all — without it the same element just swaps its text
      // and nothing animates. 150ms: enough to register that the bar now
      // describes someone else, short enough that clicking down a list never
      // feels like waiting.
      context={
        selected && (
          <div key={selected.id} className="animate-in fade-in flex min-w-0 flex-1 duration-150">
            <LeadHeader lead={selected} />
          </div>
        )
      }
    >
      {/* The stage filter sits ACROSS the top rather than in a left rail: the
          rail spent 224px of every screen on eight rows that are mostly zero,
          and horizontally the same eight fit in one line of chrome. */}
      <div className="flex h-full min-h-0 flex-col">
        <StageFilter active={stage} counts={counts} onSelect={(s) => setFilter({ stage: s || null })} />
        <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1">
          <div className="flex h-full flex-col">
            <LeadList
              leads={leads}
              selectedId={selectedId}
              onSelect={(l) => {
                // Picking another lead is the broker moving on, so the one
                // held out-of-filter is released. Without this the next poll
                // could pull it back over the lead they just chose.
                rememberMoved(null)
                setSelectedId(l.id)
              }}
              heading={heading}
              search={searchDraft}
              onSearch={setSearchDraft}
              loading={loading || refetching}
              loadFailed={loadFailed}
              onRetry={load}
              // SELECT WHAT WAS JUST CREATED. The dialog already hands back
              // the new lead and this used to throw it away and only reload —
              // so a broker who had just typed a customer's details landed
              // back on the list with nothing open, and had to find the row
              // they had created seconds earlier.
              //
              // rememberMoved for the same reason a stage change uses it: a
              // manual lead is New, so if the rail is filtered to anything
              // else it will not be in the reloaded page at all, and the pane
              // would have nothing to show.
              onLeadAdded={(lead) => {
                rememberMoved(lead)
                setSelectedId(lead.id)
                load()
              }}
            />
            {totalPages > 1 && (
              <div className="text-muted-foreground flex h-(--height-control) w-(--width-lead-list) shrink-0 items-center justify-between border-t bg-card px-2 text-2xs">
                <span>
                  Page {page} of {totalPages}
                </span>
                <div className="flex gap-1">
                  <button
                    disabled={page <= 1}
                    onClick={() => setPage(page - 1)}
                    className="border-border bg-card hover:bg-accent inline-flex size-6 items-center justify-center rounded-md border disabled:opacity-40"
                  >
                    ‹
                  </button>
                  <button
                    disabled={page >= totalPages}
                    onClick={() => setPage(page + 1)}
                    className="border-border bg-card hover:bg-accent inline-flex size-6 items-center justify-center rounded-md border disabled:opacity-40"
                  >
                    ›
                  </button>
                </div>
              </div>
            )}
          </div>
          {selected ? (
            // key by lead id: selecting a different lead remounts the panel, so
            // a half-typed licence never carries across to another customer.
            <LeadDetail
              key={selected.id}
              lead={selected}
              onStageChange={changeStage}
              onEditIssuedDate={() => selected && openIssuedDialog(selected)}
              stageSaving={stageSaving}
              callCountSaving={callCountSaving}
              onAdjustCallCount={(outcome, delta) =>
                selected && adjustCallCount(selected.id, outcome, delta)
              }
              onRemind={(at) => remindToCall(selected, at)}
              openReminder={openReminder}
              onReminderOpened={() => setOpenReminder(false)}
            />
          ) : (
            <div className="text-muted-foreground flex flex-1 items-center justify-center">
              {loading ? 'Loading…' : 'Select a lead'}
            </div>
          )}
        </div>
        </div>
      </div>

      {/* Asked on the way into Quote Done and beyond, for Meta leads only.
          Rendered here rather than inside LeadDetail because it interrupts a
          stage move, and the stage move is this component's business. */}
      <IssuedDialog
        leadName={issuing?.lead.name ?? null}
        premiums={issuing?.premiums ?? []}
        currentIssuedAt={issuing?.currentIssuedAt ?? null}
        busy={stageSaving}
        onConfirm={confirmIssued}
        onCancel={() => setIssuing(null)}
      />

      <QualifyLeadDialog
        leadName={qualifying?.lead.name ?? null}
        savedConvictions3y={qualifying?.savedConvictions3y ?? null}
        savedLicensed5yPlus={qualifying?.savedLicensed5yPlus ?? null}
        busy={qualifySaving}
        onAnswer={answerQualification}
        onSkip={skipQualification}
      />

      <LostReasonDialog
        leadName={closing?.name ?? null}
        busy={stageSaving}
        onAnswer={closeLost}
        onCancel={() => setClosing(null)}
      />
    </AppShell>
  )

  function setPage(p: number) {
    const next = new URLSearchParams(params)
    if (p <= 1) next.delete('page')
    else next.set('page', String(p))
    // Paging is moving on too — the same reason setFilter releases it. This
    // one does not go through setFilter, so it needs its own call rather than
    // inheriting one.
    rememberMoved(null)
    setParams(next)
  }
}
