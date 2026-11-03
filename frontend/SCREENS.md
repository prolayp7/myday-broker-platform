# MyDay CRM — Screen Index

The app is now a **React + Vite + shadcn/ui** application in [`app/`](app/).
(It was originally prototyped as standalone SAPUI5 HTML files; those were retired
after the React port reached parity — see git history for the UI5 originals.)

Run it:

```bash
cd app
npm install
npm run dev     # http://localhost:5173
```

## Broker screens (the broker's day, in order)

| # | Screen | Route | Source | What it does | Status |
|---|--------|-------|--------|--------------|--------|
| **1** | Login | `/login` | [app/src/routes/Login.tsx](app/src/routes/Login.tsx) | Username + password → OTP → attendance stamp → My Day | ✅ Done |
| **2** | My Day | `/my-day` | [app/src/routes/MyDay.tsx](app/src/routes/MyDay.tsx) | Daily overview: pipeline stage tiles, needs-attention, closed premium (Recharts) | ✅ Done |
| **3** | My Leads | `/leads` | [app/src/routes/Leads.tsx](app/src/routes/Leads.tsx) | Stage rail + lead list + lead detail (capture form, log contact, change stage) | ✅ Done |

> **Sales-management tool, not a quoting tool** — quoting and DASH lookups happen
> in the external rating system. Detail capture (vehicle / home / tenant) lives in
> the My Leads capture form.

## Planned (not built yet)

| # | Screen | What it does |
|---|--------|--------------|
| 4 | Capture details (TBD) | Standalone capture flow for vehicle / home / tenant — feeds the lead record. |
| 5 | Lead detail — mobile | One-hand on-call version of the lead detail (Call / WhatsApp / log). |
| 6 | My Performance | Broker's closed deals, premiums, call & conversion stats (TanStack Table). |
| 7 | Tickets | Raise & track customer-request tickets (broker + admin). |

## Stack

React + Vite · React Router · Axios (data seam in [app/src/data/api.ts](app/src/data/api.ts))
· TanStack Table · React Hook Form + Zod · Tailwind CSS · shadcn/ui · Recharts.

Domain model and mock data live in [app/src/domain/](app/src/domain/) and
[app/src/data/mock/](app/src/data/mock/); swapping to a real backend is a one-file
change in `api.ts` (set `VITE_API_BASE_URL`).
