# MyDay CRM — Design Principles

> The single source of truth for how every screen is built. Read this before
> creating or editing any screen. The goal: every screen looks and behaves like
> it came from the same hand, because it followed the same rules.
>
> **Stack:** React + Vite + TypeScript · Tailwind CSS v4 · shadcn/ui · React
> Router · React Hook Form + Zod · Recharts · lucide-react. App lives in
> [`app/`](app/). (This project was originally prototyped in SAPUI5; it inherited
> that design language's *values* — token-driven color, semantic states, the
> right control per data type, calm status styling, one primary action per
> region — but is now a plain React app. Those values are what these rules
> encode.)

---

## 0. The one rule that governs all others

**Everything is a token. Nothing is hardcoded.**

No component ever writes a raw color (hex / rgb / oklch), an inline `style`, an
arbitrary Tailwind value (`w-[26rem]`, `text-[10px]`), or a magic URL. Every
visual value — color, radius, shadow, named width, type step — is defined once
in [`app/src/index.css`](app/src/index.css) and consumed through a Tailwind
utility (`bg-primary`, `text-success`, `shadow-card`, `rounded-lg`,
`w-(--width-kpi-card)`). Data (names, avatars, numbers) comes from the data
layer, never literals in a component.

Why: one edit to a token re-themes the whole app; light/dark stay in sync; the
UI reads as one system. A hardcoded value is a silent fork of the design system.

---

## 1. The token layer — where every value lives

All tokens are declared in [`app/src/index.css`](app/src/index.css) in two parts:

1. **`:root` / `.dark`** — the raw values (OKLCH colors, radius, shadows, widths,
   type steps). `.dark` overrides the same names for dark mode.
2. **`@theme inline { … }`** — re-exports each token as a Tailwind theme variable
   (`--color-*`, `--shadow-*`, `--radius-*`, …) so Tailwind **generates a utility
   class** for it. This is the step that makes the system usable: because
   `--color-success` exists in `@theme`, `bg-success` / `text-success` /
   `border-success` all exist as classes.

**To add a value:** add it to `:root` (and `.dark` if it differs), mirror it in
`@theme inline`, then use the generated utility. Never skip straight to an
arbitrary value in a component.

### Token groups (current)

| Group | Tokens | Utility examples |
|-------|--------|------------------|
| Surfaces | `background`, `card`, `popover`, `foreground`, `*-foreground` | `bg-card`, `text-foreground` |
| Brand | `primary`, `secondary`, `accent`, `muted`, `destructive` | `bg-primary`, `text-muted-foreground` |
| Lines/focus | `border`, `input`, `ring` | `border`, `ring-ring` |
| **Semantic states** | `success`, `warning`, `error`, `info` — each with `-foreground` + `-muted` | `text-success`, `bg-warning-muted` |
| **Chart** | `chart-auto/home/travel`, `chart-1..5` | `bg-chart-auto` (+ `chartColors` in JS) |
| Elevation | `shadow-card`, `shadow-popover` | `shadow-card` |
| Radius | `radius` → `radius-sm/md/lg` | `rounded-lg` |
| Type | `font-sans`, `text-2xs` | `font-sans`, `text-2xs` |
| Named widths | `width-kpi-card`, `width-lead-list`, `width-stage-rail` | `w-(--width-kpi-card)` |

---

## 2. Color — semantic, never literal

- **Brand / primary action** → `primary` (shadcn `Button` default variant).
- **State** (stage, status, validity) → the semantic set: `success` / `warning` /
  `error` / `info`, plus `muted-foreground` for neutral. Use the **`-muted`
  variant as a soft tinted background** with the solid color as text — calm
  colored text, not a heavy solid pill (see `StageBadge`). Never express state
  with a one-off color.
- **Charts / data-viz** need a color *string*, not a class. The single bridge is
  [`app/src/lib/tokens.ts`](app/src/lib/tokens.ts): `chartColors.auto`, `.home`,
  `.travel`, `.series[]`. Import from there; never write `fill="var(--…)"` or a
  hex at a chart call site. Legend swatches use the matching `bg-chart-*` class.

**Rule:** if you're about to type a color, stop — either a token/utility exists,
or you add one to `index.css` first.

---

## 3. Spacing, sizing & radius — the scale, then named tokens

- **Spacing/gaps** use Tailwind's built-in scale (`gap-3`, `p-6`, `mt-4`) — that
  scale *is* our spacing system; don't invent `[..]` pixel gaps.
- **One-off dimensions that recur or carry meaning** (a card width, a rail width)
  become a **named width token** (`--width-kpi-card`) used as `w-(--width-…)`.
  This keeps intent legible and avoids `w-[26rem]` scattered around.
- **Radius** always via `rounded-sm/md/lg` (all derive from the one `--radius`).
- **Micro type** below `text-xs` uses the `text-2xs` token, not `text-[10px]`.

---

## 4. Layout — Tailwind flex/grid, responsive by default

Unlike the old SAP rules, hand-built flex/grid layout **is** the idiom here.

- Full app chrome = [`AppShell`](app/src/components/AppShell.tsx): a fixed top bar
  (brand, nav, availability, notifications, profile) + scrolling `<main>`.
- Master–detail (My Leads) = a horizontal flex of three panes: `StageRail`
  (`w-(--width-stage-rail)`) · `LeadList` (`w-(--width-lead-list)`) · `LeadDetail`
  (`flex-1 min-w-0`). Use `min-w-0` on flex children that hold truncating text.
- Card/tile rows = `flex flex-wrap gap-3` (wraps responsively) — not fixed grids
  that overflow.
- Prefer relative units and `max-w-full` on fixed-width blocks so nothing forces
  horizontal page scroll. **Primary target = laptop; secondary = iPad.**

---

## 5. Forms — the correct control per data type

Capture/edit forms use **React Hook Form + Zod**. The Zod schema in
[`app/src/domain/types.ts`](app/src/domain/types.ts) is the single source of truth
for the `Lead` shape; forms bind to it and validate against it.

Field controls come from the shared primitives in
[`app/src/routes/leads/fields.tsx`](app/src/routes/leads/fields.tsx) — pick by
data type, don't default everything to a text input:

| Data type | Component |
|-----------|-----------|
| Free text / number / tel / email | `TextField` (`type` prop) |
| Date | `TextField type="date"` |
| Fixed short list | `SelectField` (shadcn Select) |
| Exactly 2 mutually-exclusive options | `TwoOptionField` (shadcn RadioGroup) |

Rules: labels on top; required marked with a token-colored asterisk
(`text-error`); dynamic repeating groups (drivers, vehicles, rented properties)
use RHF `useFieldArray`, not manual array wrangling; progressive disclosure via
`watch()` (show a field only when it applies — e.g. financing company only when
ownership is financed/leased). Config-driven field sets (the property fact set)
live in [`app/src/domain/propertySpec.ts`](app/src/domain/propertySpec.ts) as a
single spec rendered against each variant — no duplication.

---

## 6. Status, feedback & validation

- **Stage / status** → `StageBadge` (semantic `-muted` tint + solid text).
- **Transient confirmation** ("Saved", "Moved to Quoted") → `toast` (sonner);
  toasts pull their colors from tokens (`--popover`, `--border`).
- **Destructive / irreversible confirm** (clearing entered property data on a
  bundling switch) → [`ConfirmDialog`](app/src/components/ConfirmDialog.tsx) via
  the `useConfirm()` hook (returns a promise).
- **Field validation** → Zod messages rendered inline in `text-error`; mark the
  input with `aria-invalid` so shadcn's error ring shows.
- **Empty states** → every list gives a meaningful message that tells the user
  what to do, never a bare "No data".

---

## 7. Actions & buttons

- **One primary per region.** Exactly one filled (`Button` default variant) — the
  single most important action. Everything else is `variant="ghost"` or
  `"outline"`. Competing primary buttons destroy hierarchy.
- **Destructive** → `variant="destructive"` **and** a confirm (§6).
- **Icons** come from **lucide-react** only (no other icon set, no inline SVG for
  iconography). Keep icon sizing on the scale (`size-4`, `size-5`).

---

## 8. Typography & hierarchy

- One typeface: the `font-sans` token. No per-component font-family.
- Hierarchy comes from **contrast** (a prominent title beside `text-muted-
  foreground` secondary text), not from bolding everything.
- Sizes use Tailwind's type scale (`text-sm`, `text-xl`, `text-2xl`); the only
  sub-`xs` step is the `text-2xs` token.

---

## 9. Data & theming

- **No literal data in components.** Names, avatars, counts, leads all come
  through the data layer — [`app/src/data/api.ts`](app/src/data/api.ts) (Axios
  seam) backed by [`app/src/data/mock/`](app/src/data/mock/). Swapping to a real
  backend is a one-file change (set `VITE_API_BASE_URL`); no screen edits.
- **Dark mode** is fully tokenized: it works by toggling a `.dark` class on
  `<html>`; every semantic token has a `.dark` value. Because components only use
  token utilities, dark mode needs zero per-component work.

---

## 10. Pre-build checklist (run before writing a screen)

1. Am I about to type a **color, size, shadow, or URL literal**? → Use a
   token/utility, or add the token to `index.css` first. (§0–3)
2. Is layout done with **Tailwind flex/grid + named width tokens**, responsive,
   with `min-w-0` on truncating flex children? (§4)
3. For each field — the **correct control** from `fields.tsx`, bound to the Zod
   schema? (§5)
4. What is the **one primary action**? Everything else ghost/outline? (§7)
5. Are **state, confirmations, validation, empty states** using the semantic
   system (StageBadge / toast / ConfirmDialog / `text-error` / real empty text)? (§6)
6. Does all **data come from the data layer**, none hardcoded? (§9)
7. Does it hold up at **laptop + iPad** widths with no horizontal page scroll,
   and in **dark mode** (it should, for free)? (§4, §9)

---

## Appendix — verifying token compliance

A quick grep proves the app has no hardcoded values (run from `app/`):

```bash
# should all return nothing for app code (src/routes, src/components/*.tsx, src/lib):
grep -rEn "#[0-9a-fA-F]{3,8}\b|oklch\(|rgba?\(|hsl\(" src/routes src/components/*.tsx
grep -rn "style={{" src/routes src/components/*.tsx
grep -rEn "\-\[[0-9]" src/routes src/components/*.tsx
```

shadcn primitives under `src/components/ui/` legitimately use `var(--token)`
references and Radix data-attribute selectors — those are token *consumers*, not
hardcoded values, and are correct as-is.
