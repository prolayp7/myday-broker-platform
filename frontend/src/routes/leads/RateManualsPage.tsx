import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'

// ─── WIREFRAME ──────────────────────────────────────────────────────────
// What a carrier's auto discounts ARE — a reference a broker scans, not a
// checklist they work through. NOT WIRED: the lists are hardcoded from the
// carriers' manuals and know nothing about the lead.
//
// BUILT TO SCAN. Rate on the left, discount named on the right, a dozen lines
// per carrier. An earlier version phrased each row as a question and put
// scope, caveats and expiry inline; it read as a document rather than a
// reference, which is not what someone mid-call can use.
//
// The detail still matters — Retiree touches only AB and UA, only Tag and
// Kycs count as theft devices — so it is one click per row rather than
// deleted. Closed by default.
//
// ONE LIST PER CARRIER, no headings within it. A dozen rows is short enough
// to take in whole, and splitting it into policy/driver/vehicle sections
// added three headings and two gaps to a list that already fits on screen.
//
// TABBED, NOT STACKED. Two carriers on one page is ~24 rows, which is past
// the point where either list can be taken in at a glance — and being
// scannable is the whole design. One carrier at a time keeps that, and a
// third carrier is one more tab rather than a redesign.
//
// NOT sorted by percentage. On Aviva the two BUNDLING rows lead because they
// are one situation with two outcomes — combined (20%) or, failing that, Car
// and Home (10%) — and a percentage sort would put them four rows apart. It
// would also float the 50% student discount to the top, where it reads as
// the most valuable; it applies to one occasional driver's portion only,
// usually less money than the 12% multi-vehicle. Pembridge follows the same
// principle: its own 50% education discount is likewise a slice of one
// driver's premium, not half the policy.

type Discount = {
  rate: string
  /** The discount NAMED, not a question — brokers know what these are, so
   *  the row is a label to find, not an explanation to read. */
  name: string
  /** Which coverages it touches, when that is narrower than "everything".
   *  Several are much narrower than the headline rate suggests. */
  scope?: string
  /** What the PROPERTY policy gets, on the two bundling discounts. */
  also?: string
  /** A rate that VARIES, as a small grid.
   *
   *  Only where the alternative is worse. Pembridge's driver-training rate
   *  depends on two yes/no answers and how long since the G2 road test, which
   *  as bullets ran to seven lines of arithmetic a broker had to do in their
   *  head. Three rows and four columns is read, not computed. */
  table?: { head: string[]; rows: string[][] }
  /** One line per condition, shown when the row is opened. Bullets rather
   *  than prose: a broker checking eligibility is looking for the one that
   *  fails, which is a scan, not a read. */
  conditions: string[]
}

// Aviva Personal Lines Auto Ontario, Update 97, effective 2025-11-01.
const AVIVA: Discount[] = [
  {
    rate: '20%',
    name: 'Combined Policy',
    also: 'Property: 20% home · 15% condo · 5% tenant',
    conditions: [
      '$1M+ liability on every vehicle',
      'Same household — named insured, spouse or partner only',
      'Same brokerage, personal lines, Aviva group',
      'Not with Car and Home',
    ],
  },
  {
    rate: '10%',
    name: 'Car and Home',
    also: 'Property: 10% home · 10% condo · 2% tenant',
    conditions: [
      'Auto and property both in the Aviva group',
      'Same brokerage',
      'Named insureds in the same household',
      'Not part of a combined policy',
      'Family Member: spouse, children or legal dependents only, property must be their primary residence',
      'Could they COMBINE instead? That is 20%',
    ],
  },
  {
    rate: '10%',
    name: 'Aviva Journey',
    conditions: [
      'Valid G2 or G',
      'App activated within 15 days',
      'Compatible smartphone and data plan',
      'Excludes Elite',
      'After 120 days: becomes 0–20%, or a surcharge up to 5%',
      'Under 1,000km tracked = 0%',
    ],
  },
  {
    rate: '15%',
    name: 'Conviction Free',
    conditions: [
      'ALL operators of the vehicle clean for 3 years',
      'Any conviction counts — including seatbelt and minor speeding',
      'Class 05/06 qualify independently',
    ],
  },
  {
    rate: '10%',
    name: 'Graduated Licensing',
    scope: 'Liab · DCPD · AB · UA · Collision',
    conditions: [
      'G1→G2 or G2→G, proof required',
      'No at-fault or convictions at the previous level',
      'Not if both licences obtained the same day',
      'Expires 1 year from the licence date',
    ],
  },
  {
    rate: '10%',
    name: 'Retiree',
    scope: 'AB + UA only',
    conditions: [
      '65+, or on CPP/QPP or a registered pension',
      'Not employed, self-employed or running a business',
      'Not employed 26+ weeks in the last 52',
      'Under 65: signed declaration form required',
      'Applies to vehicles where they are principal operator',
    ],
  },
  {
    rate: '50%',
    name: 'Out of Town Student',
    scope: 'Class 05/06 portion only · excl. increased AB',
    conditions: [
      'Living at school 100km+ from home',
      'Vehicle garaged at the named insured’s location',
      'School name and location documented',
      'One term — revalidate at renewal',
    ],
  },
  {
    rate: '5%',
    name: 'Affinity',
    conditions: [
      'Validated member of an Aviva-approved affinity program',
      'Approved list not published — confirm with Aviva',
      'Class 05/06 qualify independently',
    ],
  },
  {
    rate: '12%',
    name: 'Multi-Vehicle',
    scope: 'Liab · AB · UA · DCPD · Collision',
    conditions: [
      '2+ vehicles, same policy or separate',
      'Same brokerage',
      'Named insured, spouse, children or legal dependents',
      'Same household, principally operated by them',
    ],
  },
  {
    rate: '20%',
    name: 'Theft Recovery Device',
    scope: 'Comprehensive · Specified Perils only',
    conditions: [
      'Tag or Kycs ONLY — AirTags do not qualify',
      'Professionally installed and activated',
      'Proof required, install date documented',
      '3 policy terms only',
      'MANDATORY on high-theft vehicles and $150,000+',
    ],
  },
  {
    rate: '5%',
    name: 'Electric or Hybrid',
    conditions: [
      'BEV: battery only, no secondary power source',
      'Hybrid: petrol engine plus electric motor',
      'Applied automatically from the VICC',
      'Class 05/06 qualify independently',
    ],
  },
  {
    rate: '2%',
    name: 'Winter Tires',
    scope: 'Liab · AB · UA · DCPD · Collision',
    conditions: [
      'Separate set of 4, fitted Dec 1 – Mar 31',
      'All-seasons do not qualify, even winter-rated',
      'Bill of sale or install receipt may be requested',
    ],
  },
]

// Pembridge Ontario Auto Manual, new business 2026-01-01 / renewal 2026-02-01.
//
// Two of these carry no published rate — Multi-Line and Multi-Vehicle say only
// "may vary by number of vehicles and operators". Shown as "—" rather than
// omitted: a broker needs to know the discount EXISTS and what qualifies for
// it, and inventing a percentage would be worse than admitting there isn't
// one. Same for the My_BRIDGE earned rate, which the manual calls the
// confidential Arity model.
const PEMBRIDGE: Discount[] = [
  {
    rate: '50%',
    name: 'Education',
    scope: 'Underage occasional operator premium only',
    conditions: [
      'Occasional operator under 25, living away at post-secondary',
      '100km+ from home, full-time, enrolment confirmed EACH term',
      'No at-fault accidents',
      'MUST NOT drive the car to school — if they do, charge FULL principal operator rates',
    ],
  },
  {
    rate: '14.5%',
    name: 'Driver Training + Graduated',
    scope: 'All coverages · excl. endorsements',
    // ASK ONE QUESTION: how long since the G2 road test? The manual answers
    // in "credited years licensed", which is years since G2 PLUS ONE, and
    // nobody converts that mid-call. The table below is already converted.
    table: {
      head: ['Since G2 road test', 'Both', 'Training only', 'Graduated only'],
      rows: [
        ['Under 1 year', '14.5%', '5%', '10%'],
        ['1–2 years', '12.25%', '2.5%', '10%'],
        ['2 years +', '—', '—', '—'],
      ],
    },
    conditions: [
      'Training: government-approved course, taken within the past 3 years',
      'Graduated: clean record — no conviction, suspension or at-fault',
      'Graduated is LOST if they were fast-tracked: shortened G1 wait, or G straight after G1',
      'Passing the G test does NOT restart it — the clock runs from G2',
    ],
  },
  {
    rate: '10%',
    name: 'Good Student',
    conditions: [
      'Ages 16–25, G2 minimum',
      'Full-time at an accredited high school, college or university',
      '80% average or equivalent in the PRIOR semester',
      'Principal or occasional operator',
    ],
  },
  {
    rate: '10%',
    name: 'Private Parking',
    conditions: [
      'Private garage, driveway or carport',
      'Extends to underage operators',
      'No proof required, no expiry — just has to be asked',
    ],
  },
  {
    rate: '10%',
    name: 'My_BRIDGE',
    conditions: [
      'Enrolment discount, one-time — enrol at NEW BUSINESS OR RENEWAL ONLY',
      'G2 minimum, principal driver only, mandatory coverages',
      'Activate within 30 days or it is removed and the policy re-rated',
      'At renewal it becomes an earned Arity score (rate not published)',
      'Needs 180 days of data, 1 trip, 1,000km',
      'Tampering = permanent exclusion, no re-enrolment',
    ],
  },
  {
    rate: '10%',
    name: 'TAG Anti-Theft',
    scope: 'Comprehensive only',
    table: {
      head: ['High theft list', 'Model years'],
      rows: [
        ['CR-V · Grand Cherokee · Highlander', 'Last 3 years'],
        ['RDX · Ram 1500 · Wrangler', 'Last 3 years'],
        ['Range Rover · Lexus RX', 'Last 5 years'],
      ],
    },
    conditions: [
      'ALSO waives the High Theft Additional Rate on the models above — often worth more than the 10%',
      'Model years roll forward with the effective date',
      'Approved TAG tracking device, proof needed: serial number and install date',
    ],
  },
  {
    rate: '5%',
    name: 'Winter Tires',
    conditions: [
      '4 winter or all-weather tires',
      'Transport Canada winter designation',
      'Installed November – April',
    ],
  },
  {
    rate: '5%',
    name: 'Retiree',
    conditions: [
      '65+, or on CPP/QPP or a registered pension',
      'AND retired, no employment or professional income',
      'AND not running a business',
      'AND not employed 26+ weeks in the last 52',
    ],
  },
  {
    rate: '3.5%',
    name: 'Loyalty',
    table: {
      head: ['Years with carrier', 'Discount'],
      rows: [
        ['3–4 years', '2%'],
        ['5–6 years', '3%'],
        ['7 years +', '3.5%'],
      ],
    },
    conditions: [
      'Counts years with Pembridge, a Pembridge brokerage, OR the prior carrier',
      'NEW BUSINESS also needs: principal operator licensed 6+ years AND no at-fault in 6 years',
      'Renewal: 3+ years with Pembridge is the whole test',
    ],
  },
  {
    rate: '—',
    name: 'Multi-Vehicle',
    conditions: [
      'Rate not published — varies by vehicles and operators',
      '2+ private passenger vehicles with TPL, same household',
      'Survives an OPCF 16 on the other vehicle',
      "Spouse's cross-file at PAFCO still counts",
      'Suspended licence, learner permit and excluded operators do not count',
      'Extends to underage occasional operators',
    ],
  },
  {
    rate: '—',
    name: 'Multi-Line',
    conditions: [
      'Rate not published — varies',
      'TPL on at least one vehicle',
      'Property must be a PRINCIPAL RESIDENCE home, condo or tenant',
      'Immediate family includes PARENTS AND IN-LAWS, and children away at school under 25',
      'Manual does not say the property policy must be with Pembridge — confirm',
      'Extends to underage occasional operators',
    ],
  },
  {
    rate: '—',
    name: 'Vehicle Safety Credit',
    conditions: [
      'Rate not published — the SUM of the features present',
      'Forward collision mitigation · driver monitor',
      'Lane departure mitigation · parking assist',
      'Restricted driver mode (e.g. Ford MyKey)',
      'Warning systems: forward, rear, lane departure',
    ],
  },
]

const CARRIERS = [
  { key: 'aviva', label: 'Aviva', meta: 'Update 97 · eff. 2025-11-01', discounts: AVIVA },
  {
    key: 'pembridge',
    label: 'Pembridge',
    meta: 'NB 2026-01-01 · renewal 2026-02-01',
    discounts: PEMBRIDGE,
  },
] as const

export function RateManualsPage() {
  const [carrier, setCarrier] = useState<(typeof CARRIERS)[number]['key']>('aviva')
  const active = CARRIERS.find((c) => c.key === carrier) ?? CARRIERS[0]

  return (
    <div className="bg-card flex min-w-0 max-w-(--width-form) flex-col p-2">
      <div className="flex items-baseline justify-between gap-3 border-b px-1">
        <div className="flex">
          {CARRIERS.map((c) => (
            <CarrierTab
              key={c.key}
              active={c.key === carrier}
              onClick={() => setCarrier(c.key)}
            >
              {c.label}
            </CarrierTab>
          ))}
        </div>
        {/* The edition rides with the carrier, not the page — two manuals with
            different effective dates, and quoting a rate off the wrong one is
            the mistake this line exists to prevent. */}
        <span className="text-muted-foreground shrink-0 pb-1.5 text-xs">
          {active.meta}
        </span>
      </div>

      {/* key on the carrier so switching tabs resets every open row: the
          detail of an Aviva discount left hanging under a Pembridge heading
          would read as that carrier's rule. */}
      <div key={active.key} className="mt-2 divide-y rounded-md border">
        {active.discounts.map((d) => (
          <Row key={d.name} discount={d} />
        ))}
      </div>
    </div>
  )
}

/** One carrier tab. Same shape as the lead detail tabs, deliberately — this
 *  is the same interaction and should not look like a new one. */
function CarrierTab({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={cn(
        // -mb-px pulls the tab's own border down onto the strip's, so the two
        // occupy one line rather than stacking into a two-pixel edge.
        '-mb-px border-b-2 px-3 py-1.5 text-sm font-semibold transition-colors',
        active
          ? 'border-primary text-foreground'
          : 'text-muted-foreground hover:text-foreground border-transparent',
      )}
    >
      {children}
    </button>
  )
}

/** Whether a rate is big enough to colour.
 *
 *  "—" (a discount whose rate the manual does not publish) parses to NaN and
 *  is never highlighted, which is right: an unknown rate should not look like
 *  a large one. */
function highlight(rate: string): boolean {
  return parseFloat(rate) >= 20
}

function Row({ discount: d }: { discount: Discount }) {
  const [open, setOpen] = useState(false)

  return (
    <div>
      {/* The whole row is the target, not a small chevron — this is used
          mid-call and a 12px hit area is a miss waiting to happen. */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="hover:bg-muted flex w-full items-center gap-2.5 px-2.5 py-2.5 text-left transition"
      >
        {open ? (
          <ChevronDown className="text-muted-foreground size-3.5 shrink-0" />
        ) : (
          <ChevronRight className="text-muted-foreground size-3.5 shrink-0" />
        )}
        {/* Fixed-width rate column so the percentages line up and the list
            reads as a ranked set of amounts at a glance. Wide enough for
            "14.5%" — Pembridge publishes a decimal rate, and at w-11 it wrapped
            or clipped. */}
        <span
          className={cn(
            'w-14 shrink-0 text-right text-base font-semibold tabular-nums',
            // 20%+ carries most of the money on a typical policy. Parsed rather
            // than string-matched: an earlier version tested d.rate === '20%',
            // which silently skipped Pembridge's 14.5% and would skip any
            // future decimal rate too.
            highlight(d.rate) ? 'text-success' : 'text-foreground',
          )}
        >
          {d.rate}
        </span>
        <span className="min-w-0 text-sm font-medium">{d.name}</span>
        {/* Scope rides on the closed row: "Retiree 10%" reads as 10% off
            everything unless it says otherwise, and that misreading is the
            whole reason to show it before the row is even opened. */}
        {d.scope && (
          <span className="text-muted-foreground ml-auto shrink-0 text-xs">
            {d.scope}
          </span>
        )}
      </button>

      {/* pl clears the chevron and the rate column, so the conditions hang
          under the discount NAME. Widened with the rate column, w-11 -> w-14. */}
      {open && (
        <div className="bg-muted/40 space-y-1.5 px-2.5 pt-0.5 pb-3 pl-[5.6rem]">
          {d.also && (
            <div className="text-success text-xs font-medium">{d.also}</div>
          )}

          {/* ABOVE the conditions, because it answers "how much?" and the
              conditions answer "do they qualify?" — and the first question is
              always asked first. */}
          {d.table && (
            <table className="text-xs tabular-nums">
              <thead>
                <tr className="text-muted-foreground text-left">
                  {d.table.head.map((h, i) => (
                    <th
                      key={h}
                      className={cn('pr-4 pb-1 font-medium', i > 0 && 'text-right')}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.table.rows.map((row) => (
                  <tr key={row[0]}>
                    {row.map((cell, i) => (
                      <td
                        key={i}
                        className={cn(
                          'pr-4',
                          i === 0
                            ? 'text-muted-foreground'
                            : 'text-right font-medium',
                          // The best cell in the row is the one a broker is
                          // looking for; grey everything that pays nothing.
                          i > 0 && cell === '—' && 'text-muted-foreground',
                        )}
                      >
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <ul className="text-muted-foreground space-y-1 text-xs">
            {d.conditions.map((c) => (
              <li key={c} className="flex gap-1.5">
                <span aria-hidden>·</span>
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
