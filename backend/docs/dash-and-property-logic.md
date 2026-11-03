# DASH prefill, and what happens when a broker picks a policy type

Written for review. Every claim below was read out of the code on 2026-08-09,
not recalled — file and function names are given so each one can be checked.

Two features, in one document because they are the two places where **choosing
one thing silently creates or destroys another**, which is where the real
complexity in this screen lives.

---

# Part 1 — Reading a DASH report

## What DASH is, and why this is optional

DASH is the Insurance Bureau of Canada's driver history search. A broker pulls
a report on the customer's licence and gets an 8-page PDF: who the person is,
every auto policy they have held for 25 years, every operator on those
policies, every vehicle, and every claim.

Migration `0048` deleted an earlier version of this feature on the instruction
*"no auto-import of any kind — the broker reads DASH's own website and types
what they see."* It came back on 2026-08-09 as an **option beside typing**, not
a replacement. Nothing about typing changed, and nothing already typed is
overwritten. That distinction is enforced in code, not just intended — see
"never overwrite" below.

## The chain: text, Claude, then the form

```
routes/dash.py          the two ways in, and the ownership guard
  services/dash.py      order, and turning failure into something a broker reads
    dash_text.py        PDF -> text              <- local, no network, no bill
    dash_azure.py       PDF -> markdown          <- scans and the fallback only
    dash_claude.py      text -> fields           <- one Opus call
    dash_parse.py       markdown -> fields       <- regex fallback, no model
    dash_apply.py       fields -> the form       <- never overwrites
```

**Why not Azure first.** All 138 sample reports carry their own text. On 6 real
reports, three routes were compared with the same prompt: claims, amounts,
vehicles, dates and licences were the same every time, and all passed checks
against the PDF. Total for the 6: Azure then Opus 154s, local text then Opus
110s, the PDF directly to Opus 105s with 2.6 times the input of local text.
`pypdfium2` was the fastest library on all 138, at 7 ms a report.

Azure still runs when the PDF has no text (a scan), and when Claude fails,
because `dash_parse` is built on Azure's markdown.

The URL route makes a network call of its own: it fetches the document before
handing the bytes on.

`dash_parse` works on a markdown string, which is why the whole parser is
testable from a checked-in fixture with no Azure key and no bill:
`tests/fixtures/dash_sample.md` is a real 8-page report with every identifying
value replaced by an invented one of the same shape.

### Why Azure, and not Gemini

The deleted `services/dash_gemini.py` recorded a limitation it could not fix
after two rounds of increasingly explicit prompting:

> DASH prints "SMITH,JOHN A" with no space after the comma, and the model
> reliably returns "SMITH, JOHN A" … a vision-language model reconstructs
> layout whitespace rather than transcribing it.

Azure Document Intelligence transcribes. Run against the real report it
returned `FRANCIS,MILLAT F` and `5 FESTIVAL DR, , NORTH YORK ON M2R3T9` — the
missing space and the empty comma-separated part both byte-exact, first try.

`prebuilt-layout` with `DocumentContentFormat.MARKDOWN`, not
`prebuilt-document`: layout returns headings, reading order and real tables,
which is what the parser keys on, and markdown keeps DASH's policy and claim
tables as tables instead of a flat list of cells to rebuild from coordinates.

### And no model on top of it

An earlier design assumed one was needed, because choosing between several
operator blocks looked like judgement. Reading a real report showed it is not:
**every block prints the licence number**, and every claim names its own
driver. What was left is string matching — which is testable in a way a prompt
is not.

## What is extracted, and what is deliberately not

Verified against the real report (`FRANCIS,MILLAT F`, 2026-01-29):

### The applicant — 9 fields, from the header above the first `##`

| DASH prints | Column |
|---|---|
| `FRANCIS,MILLAT F` | `name` |
| `DLN: F7172-55339-70224 Ontario` | `license_number`, `license_province` |
| `Gender: Male` | `gender` |
| `Marital Status: Married` | `marital_status` |
| `Address: 5 FESTIVAL DR, , NORTH YORK ON M2R3T9` | `street_address`, `city`, `province`, `postal_code` |

**ONLY the header.** The same labels appear again inside policy blocks for
other operators, and reading those would fill the applicant's form with a
co-insured's details. Everything from the first `## ` onward is cut away
before anything is read (`parse_driver`).

The address needs splitting, and note the **empty part between the commas** —
that is real DASH output, and `_split_address` drops empty segments so the
split does not produce a blank city.

### The other drivers — 3 fields each, most recent policy only

| DASH prints | Column |
|---|---|
| `Operator: VALLOMKUNNEL,FRANCIS J` | `name` |
| `DLN: V0303-26756-20728 Ontario` | `license_number`, `license_province` |

**THE TRAP, and it is the thing most worth reviewing.** The report's own
subject is listed as an operator on his own policy — Policy #1 carries both
`VALLOMKUNNEL,FRANCIS J` and `FRANCIS,MILLAT F`. Taking every operator would
create a second copy of the applicant as an "additional driver", filled from
his own report.

He is excluded **by licence number, not by name**, because names vary within
one report: the same man is `VALLOMKUNNEL,FRANCIS J` on Policy #1 and
`Francis Vallomkunnel` on Policy #2. Licence numbers do not vary.

Covered by `test_the_subject_is_not_returned_as_an_additional_driver` and
`test_no_subject_licence_means_no_operators_at_all`, both proven by reverting
the code they cover.

### The vehicles — 4 fields each, most recent policy only

| DASH prints | Column |
|---|---|
| `2024 HONDA - CIVIC TOURING 4DR` | `year_make_model` |
| `2HGFE1F9XRH001562` | `vin` |
| `Commuting Distance (One-Way / Annual): 15km / 14000km` | `one_day_distance_km`, `annual_km` |

Every vehicle on the policy, whoever the listed operator is — matching them to
one operator would drop the second car on a two-driver household. Private
Passenger only, judged on the **class text** (`Private Passenger Vehicle`) and
never the leading code: DASH prints `01 /`, `02 /` and `03 /` for Pleasure,
Commute and Business, and all three are private passenger cars. Matching the
number would have excluded two thirds of them.

### Not taken, and why

| In the report | Why not |
|---|---|
| Date of birth | **Only when it is a whole date.** Every report seen so far prints the year alone (`Date of Birth: 1997`) and the column is a `date` — inventing a month and day would put a wrong value in a field the broker is *required* to complete before Quote Done. A report printing a full date IS taken; the rule checks the value rather than assuming the format |
| G1 / G2 / G dates | **Not in the report at all**, for anyone. Its only licensing figure is `Years Licensed: 10` — a count, about the subject alone |
| `Relationship to Policyholder` | Relationship to the **policyholder**, who need not be our applicant. Reads `NA` on every block of the real report anyway |
| `Start of the Earliest Term` | When that person joined *this policy*. Writing it into `g1_date` would be inventing a licence date from a policy date |
| Claims, policies, coverage | The broker reads the DASH document itself; a partial copy here is a second place to look, not a saving |
| `Vehicle Use`, `Coverage` | The previous insurer's choice on a policy that may be years old. The quote's coverage is the broker's decision |

## The parsing bugs worth knowing about

Both are the same shape: **Azure reads DASH's two table columns in reading
order**, so facts interleave in ways that break a naive line-by-line parser.

**One car from a two-car policy.** The first version anchored on the
`Vehicle #N:` marker. Azure puts `Vehicle Code:` between the label and the car:

```
Vehicle #1: Vehicle Code:
2024 HONDA - CIVIC TOURING 4DR - 1AAAA1A1AAA000001
```

It returned one vehicle where the policy had two, and **nothing raised** — the
output looked entirely healthy. Fixed by matching backwards from the VIN, 17
characters in DASH's own alphabet (no I, O or Q). Reverting fails 5 tests.

**A role between the name and the licence.** The same interleaving puts a
vehicle role inside an operator block:

```
Operator: FRANCIS,MILLAT F Vehicle #1: Principal Operator DLN: F7172-...
```

So each operator's segment runs from its `Operator:` marker to the next one,
and the DLN is searched for *within* that segment rather than read from the
line below.

## Azure is bounded, and off the event loop

`routes/dash.py` calls `service.apply_pdf` through `run_in_threadpool`, the
same pattern as `routes/webhooks.py` — which measured a 2.27s stall of every
concurrent request becoming 0.07s. Without it, one slow report froze the whole
API for however long the analysis took.

`poller.result(timeout=90)` bounds the wait itself. The threadpool stopped one
report freezing everyone; the timeout stops it hanging its own request forever
and holding a worker thread. Past the deadline the broker is told to try again
or type the fields, which is the honest answer for an optional feature.

## The three safety rules

**1. Never overwrite the broker** (`dash_apply._apply_driver`). DASH fills what
is blank and leaves everything else exactly as typed. A broker who corrected a
licence number on the phone keeps their correction, and a report uploaded
afterwards cannot silently undo it. The skip is **reported**, not hidden — a
silent no-op looks like a failure.

"Blank" means null *or* empty string: a field the broker cleared comes back as
`""`, and treating that as "already has a value" would leave it permanently
unfillable.

**2. Never delete.** A car or a driver missing from the report stays: a report
is not evidence that something does not exist.

**3. Otherwise, whatever is printed is taken.** Vehicles are still matched on
VIN within a single report, because DASH lists the same car on a policy and
its renewal. Drivers are not matched at all, and an operator with no licence is
imported like any other — a name the broker does not have to type is worth
having.

An earlier version tracked the licences already on the lead so a second upload
added nobody, and skipped licence-less operators so they could not multiply.
Both rules were carried over from the deleted `dash_*` tables, where a STORED
report had to be found and replaced. Nothing is stored now, so neither had a
job — and a broker who uploads twice and sees two spouses deletes one, exactly
as they would a duplicate car.

**The one exclusion that is real:** the report's subject, who is listed as an
operator on his own policy. It is matched by licence rather than name because
names vary within one report — the same man is `VALLOMKUNNEL,FRANCIS J` on
Policy #1 and `Francis Vallomkunnel` on Policy #2.

And it **fails closed**: a report whose header licence cannot be read has
nothing to exclude the subject BY, so no operators are imported at all. The
applicant's own fields and the vehicles are filled either way. Before this,
`subject` fell back to `""`, the comparison silently never matched, and the
applicant was imported as his own additional driver.

## Whose report was that?

`dash_apply` returns `subject_name` and `subject_license_number`, and the panel
states them beside the control:

```
Report: ALAPPAT,JOHNSON
```

**Nothing rejects a report for the wrong person.** The broker decides that; a
system that argued would turn an optional convenience into a step in the way.
But "fill blanks only" is no protection when the fields *are* blank — a report
for the spouse, or for the customer still open in another tab, fills exactly
the fields it should not. So the subject is stated.

Read from the **parsed report**, not from what was written. A broker who
already typed a name gets that field skipped, and reporting only what was
filled would fall silent in the one case that matters most — a mismatch is
likeliest precisely when they have been typing.

## The PDF is not kept

Read, used, dropped. The six `dash_*` tables were dropped in migration `0048`
and are not coming back: they were shaped like the DASH *document*, so they
could only ever answer DASH questions. The broker reads the real report on
DASH's own site.

Stated plainly because it is a real constraint: **claims and policy history are
not stored anywhere.**

---

# Part 2 — What "Homeowner" actually does

`services/lead.set_policy_type` is the single most destructive function in the
CRM, and the one most worth reviewing carefully. One dropdown decides whether
a property side exists at all — and switching it **creates and destroys rows
across four tables**.

## The two vocabularies

They are deliberately different, and the mapping is explicit
(`_PROPERTY_TYPE_FOR`) rather than assumed to be the same string:

| `lead.combined_policy_type` — the POLICY | `property.property_type` — one BUILDING |
|---|---|
| `Auto Only` | — |
| `Homeowner Only`, `Auto and Homeowner` | `Homeowner` |
| `Tenant Only`, `Auto and Tenant` | `Tenant` |
| `Auto and Condo` | `Condo` |

Both standalone and bundled forms map to the same property type: whether an
auto policy rides along changes nothing about the building. And an *Auto and
Homeowner* policy can hold a rental whose own type is `Condo`, which is why one
value cannot serve as the other.

## Picking a property type creates a home

The **Primary** property is created by this flow, never by a broker adding one
(`property.create_primary`). It is the applicant's own residence, so its
existence follows from *"this quote is combined"* — not from someone deciding
to add a building.

**It has no address of its own.** The primary residence is where the applicant
lives, which the driver row already records, so the screen reads it from there.
Change it on the auto side and the property follows, because they were never
two separate facts. Rentals *are* different buildings and do carry their own
address.

## Which questions each type asks

All three share: address, `insured_since`, `insured_with_property_since`,
`structure`, `year_built`, `notes`.

**A Tenant answers only those.** Everything below the address is about the
BUILDING, which is not theirs to describe (`PropertyPage.tsx`: `isBuilding =
property_type !== 'Tenant'`).

**A Homeowner or Condo owner also answers** stories, baths, mortgages, finished
percent, the five renovation years (electrical, plumbing, heating, roof, water
heater), and the protection block (burglar alarm, smoke detectors, fire
extinguishers, sump type).

**Only a Condo answers** `contents_value`.

## Changing the type starts the home over

This is the rule most likely to look like a bug and is deliberate. A type
change **deletes the Primary and creates a new one** rather than re-typing the
existing row.

Re-typing in place looks tidier and is wrong. The three intakes ask different
questions, so a row carried across keeps answers the new form never asks: a
tenant record would still hold *"2 stories, roof done 2019"* — invisible on
screen, present in the database, describing a building the customer does not
own. Switch back and those stale answers reappear as though freshly collected,
possibly against an address that changed in between.

## Rentals

A Homeowner or Condo applicant may add investment properties — a *Rental
Dwelling* (rated as a house) or a *Rented Condo* (rated as a condo). A **Tenant
applicant may not**: someone renting where they live may in reality own a
rental, but the intake does not cover it, and a row with no form behind it is
worse than no row.

So switching to a tenant policy deletes any rentals, and that is one of the
things the warning names.

## Nothing is discarded without saying so

Any transition that loses answers **refuses with a 409** naming exactly what
goes, and proceeds only when the request confirms the warning was accepted.
There is no undo and no saved copy.

What a switch can destroy, all in one dropdown change:

| | |
|---|---|
| The Primary property | Every answer on it, when the type changes |
| Rentals | When switching to a tenant policy |
| **Every vehicle on the lead** | When switching to a property-only policy |
| Premiums | For products the new type does not cover |

**The vehicles are counted across EVERY driver, not just the applicant.** A
household's cars are commonly under a spouse, and counting only the
applicant's would warn "1 vehicle", delete one, and leave the spouse's
orphaned on a policy with no auto side.

**The premiums are not cosmetic.** The totals are generated columns built with
`coalesce`, so a figure left behind keeps feeding the total Meta receives as
the Purchase event's value. Switching Auto Only -> Tenant Only without clearing
them would report a tenant policy worth its old auto premium.

## Re-picking the same value is a no-op

Returned rather than refused: choosing the value already set is not a mistake,
and it must not trip the confirmation guard. `create_primary` behaves the same
way — same type returns the existing row untouched.

---

# What a reviewer should push on

Honest list of the weak points, rather than only the parts that work:

1. **`set_policy_type` is not atomic.** It deletes vehicles, deletes rentals,
   replaces the primary, clears premiums and updates the lead — as separate
   calls. A failure halfway leaves the earlier deletions done and the change
   incomplete, with no undo. This is the one place in this document where a
   Postgres function is genuinely justified.
2. **There is no VIN uniqueness in the database.** Migration `0047` had
   `unique (lead_id, vin)`; `0048` dropped the whole table and `0050` recreated
   it without one. Duplicate VINs are prevented only by the Python check in
   `dash_apply`, and not at all when a broker adds a car by hand.
3. **The DASH URL route fetches arbitrary URLs**, follows redirects, downloads
   before checking that the broker may touch that driver, and buffers the whole
   response before enforcing its 20 MB limit. It also logs the exception, which
   can carry the signed URL and its token. Deferred deliberately: the link is
   an integral part of the workflow and will be tested against a real DASH URL
   first.
4. **The applicant's NAME is still written**, which one reviewer reads as a
   contradiction — DASH's name is meant to be the `Report:` microtext only.
   Kept on purpose: the rule this feature settled on is *write anything the
   broker can see and correct*, and name has a visible field. That is the same
   rule that keeps `license_province` and `unit_number` unwritten.
5. **One fixture.** The parser is tested against a single real report. A second
   and third, with different layouts, are the only real protection against an
   Azure layout change.
