# MyDay Broker — Sales Management (React)

Broker lead-management app. React + Vite + shadcn/ui port of the original SAPUI5
wireframes.

## Run

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # typecheck + production build → dist/
```

## Stack

- **React + Vite** (TypeScript)
- **React Router** — `/login`, `/my-day`, `/leads`
- **Axios** — all data access goes through `src/data/api.ts` (the backend seam)
- **React Hook Form + Zod** — capture form, login, log-contact; Zod schema in
  `src/domain/types.ts` is the single source of truth for the `Lead` shape
- **Tailwind CSS + shadcn/ui** — `src/components/ui/*` are shadcn primitives;
  design tokens (incl. Fiori-semantic states) in `src/index.css`
- **Recharts** — premium-by-line donut on My Day
- **TanStack Table** — available; reserved for the tabular My Performance screen

## Structure

```
src/
  domain/        stages, Lead Zod schema/types, PROPERTY_SPEC config
  data/
    mock/        in-repo mock leads + My Day data (ported from the wireframes)
    api.ts       Axios data seam — resolves from mocks; set VITE_API_BASE_URL
                 to point at a real backend
  components/    AppShell (top bar), CountTile, StageBadge, ConfirmDialog, ui/*
  routes/
    Login.tsx    two-step sign-in (credentials → OTP)
    MyDay.tsx    KPI card + pipeline / attention / closed-off tiles
    Leads.tsx    master–detail: StageRail + LeadList + LeadDetail
    leads/       StageRail, LeadList, LeadDetail, CaptureForm, LogContactDialog, fields
```

## Data → real backend

Mocks resolve through `src/data/api.ts`. To use a live API, set
`VITE_API_BASE_URL` in a `.env` file and the same functions issue Axios calls
instead of returning mock data — no screen code changes.
