I’ll map the app’s entry points, dependencies, major modules, and how the tagged driver repository fits into the backend.

Find files matching `*`

Find files matching `**/{README*,pyproject.toml,requirements*.txt,package.json,Dockerfile,docker-c...`

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/repositories/driver.py"
}

Ran terminal command: git status --short && git branch --show-current

The repository is a two-part CRM: a React frontend and a Python/Supabase backend. I’m tracing the actual route and service structure next so the overview distinguishes implemented features from general project intent.

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/README.md"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/frontend/README.md"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/main.py"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/frontend/package.json"
}

Find files matching `backend/**/*.py`

Find files matching `backend/migrations/*.sql`

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/frontend/src/App.tsx"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/frontend/src/data/api.ts"
}

Read , lines 1 to 160
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/README.md",
  "view_range": [
    1,
    160
  ]
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/requirements.txt"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/db.py"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/auth_dependencies.py"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/frontend/src/auth/AuthContext.tsx"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/frontend/src/components/AppShell.tsx"
}

Search for `^(export (async )?function|export const [A-Za-z0-9_]+\s*=|export async function)`

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/docs/architecture-guidelines.md"
}

Search for `@router\.(get|post|put|patch|delete)\(|@broker_router\.(get|post|put|patch|de...`

Search for `^export async function [A-Za-z0-9_]+|^export function [A-Za-z0-9_]+|^export c...`

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/frontend/src/auth/authInterceptor.ts"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/frontend/vite.config.ts"
}

Find files matching `frontend/src/routes/**/*.tsx`

Read , lines 110 to 210
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/README.md",
  "view_range": [
    110,
    210
  ]
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/routes/driver.py"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/services/driver.py"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/routes/auth.py"
}

Read , lines 1 to 160
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/auth_dependencies.py",
  "view_range": [
    1,
    160
  ]
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/frontend/src/domain/stages.ts"
}

Read , lines 1 to 170
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/frontend/src/domain/types.ts",
  "view_range": [
    1,
    170
  ]
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/.env.example"
}

Read , lines 1 to 220
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/services/driver.py",
  "view_range": [
    1,
    220
  ]
}

Read , lines 1 to 120
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/frontend/src/data/api.ts",
  "view_range": [
    1,
    120
  ]
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/start.sh"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/Procfile"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/frontend/src/main.tsx"
}

# MyDay CRM — application overview

**MyDay CRM is a broker-focused sales and lead-management application**, built around assigning insurance leads to brokers, helping them progress those leads through a sales pipeline, and giving administrators tools to manage brokers, routing, and performance.

## Architecture

The project has two main parts:

- **Frontend:** React 19, TypeScript, and Vite, with React Router for navigation, Axios for API calls, and Tailwind/shadcn-style UI components. The routes and shared app shell are wired in `App.tsx`.
- **Backend:** FastAPI, with Supabase/Postgres for persistence. Backend requests generally follow a layered structure: HTTP routes → business services → Supabase repositories, with Pydantic schemas for validation. The project describes this pattern in `backend/README.md`.
- **Database:** 135 SQL migrations are present, through `0134_broker_commission_share.sql`. Migration files capture schema changes and database-side behavior.
- **Deployment:** The backend runs with Uvicorn; the deployment entry is in `Procfile`. The frontend build runs TypeScript checks before producing a Vite build, as defined in `package.json`.

## What the application does

### Broker workflow

Brokers can sign in, work their assigned leads, and track follow-ups. The frontend includes:

- **My Day:** a daily work dashboard for pipeline, reminders, and related metrics.
- **Leads:** lead lists and detail views, including contact history and status changes.
- **Lead capture and quoting details:** applicant, driver, vehicle, and property information.
- **Reminders and contact logging.**
- **A mobile companion:** separate `/m` screens for My Day and lead lists, rather than just resizing the desktop screens.
- **MVR tools:** a broker-facing MVR pull log and a separately routed report screen.

The route definitions, including protected broker pages and admin pages, are in `App.tsx`. The pipeline stage labels and groupings used in the UI are in `stages.ts`.

### Admin workflow

Administrators have screens and backend endpoints for broker and broker-group management, onboarding, lead reassignment, commission rates, lead-cost settings, campaigns, Meta conversion reporting, and website lead routing. Admin routes are protected separately from broker-facing routes. The backend registers these route groups in `main.py`.

### Lead intake and integrations

The backend supports multiple lead and data flows, including Meta and website lead intake, manual lead creation, and retry handling for some failed ingestion or outbound work. It also contains modules for Meta conversion signals and reporting, WhatsApp notifications, renewal reminders, DASH document handling, and MVR processing.

Several background tasks start with the FastAPI app and are cancelled on shutdown. They drain queued conversion signals, retry lead ingestion, and handle reminders, WhatsApp notifications, renewal messages, and website-lead routing; see the lifecycle setup in `main.py`.

## Authentication and access model

The backend implements a two-step login: password, then TOTP verification, with refresh tokens for session renewal. Authenticated requests re-check the broker’s current database status, so blocked, archived, or deactivated accounts can lose access without waiting for an access token to expire. Admin endpoints use a separate role check. The relevant route and access logic is in `auth.py` and `auth_dependencies.py`.

The frontend stores the session in local storage and includes Axios handling for attaching tokens and refreshing them after eligible `401` responses; see `AuthContext.tsx` and `authInterceptor.ts`.

## The tagged driver repository

`driver.py` is the Supabase data-access layer for the `driver` table. It explicitly selects the columns the broker UI needs rather than selecting every column. Its operations cover:

- Fetching or creating the principal applicant row for a lead.
- Listing drivers with the applicant first, then additional drivers in creation order.
- Inserting additional drivers, fetching by ID, updating fields, and deleting rows.

The service layer handles the domain rules: the applicant is created the first time a lead’s driver data is requested, is seeded from the lead’s contact details, and cannot be deleted. The database’s unique constraint protects against concurrent creation of multiple principal drivers; if that race occurs, the service re-reads the winning row. Additional drivers represent other people on the policy. Those behaviors are implemented in `services/driver.py` and exposed through authenticated endpoints in `routes/driver.py`.

## Data and frontend behavior

The frontend centralizes much of its API access in `api.ts`. It has a mock-data path for some lead functions when `VITE_API_BASE_URL` is unset, while live backend calls use an authenticated Axios client. So the frontend is not uniformly “mock” or uniformly “live”; behavior depends on the API function and environment configuration.

The backend loads Supabase connection settings from its environment. For local frontend development, the README documents `npm install` and `npm run dev`; for a production frontend build, use `npm run build`. The frontend setup notes are in `frontend/README.md`.

## Documentation caveat

Some narrative sections in `backend/README.md` are explicitly marked as stale: its migration summary stops well before the current migration set, and its old endpoint table describes authentication as a placeholder. For the current implementation, the route, service, repository, and migration files are more reliable than those older README summaries.

**In short:** MyDay CRM is a full-stack broker operations CRM with lead intake and assignment, insurance-specific customer/policy data, broker and admin workflows, follow-up tooling, and integrations supported by background processing.


I’ll check the tagged analysis document and trace AI-related dependencies and call sites to separate real integrations from unused or incidental references.

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/repositories/docs/analyze.md"
}

Search for `anthropic|claude|openai|gemini|llm|AI\b|artificial intelligence|generate_text...`

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/claude_client.py"
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/requirements.txt"
}

Read , lines 1 to 220
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/claude_client.py",
  "view_range": [
    1,
    220
  ]
}

Read , lines 1 to 145
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/repositories/docs/analyze.md",
  "view_range": [
    1,
    145
  ]
}

Read , lines 146 to 270
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/repositories/docs/analyze.md",
  "view_range": [
    146,
    270
  ]
}

Read , lines 1 to 140
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/services/dash_azure.py",
  "view_range": [
    1,
    140
  ]
}

Read , lines 1 to 180
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/services/dash_parse.py",
  "view_range": [
    1,
    180
  ]
}

Read 
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/.env.example"
}

Read , lines 185 to 265
Completed with input: {
  "path": "/home/prolay/Projects/myday-crm/backend/claude_client.py",
  "view_range": [
    185,
    265
  ]
}

Search for `AI|Claude|Anthropic|Gemini|Azure|Document Intelligence|LLM|model|OCR`

Search for `ANTHROPIC_API_KEY|AZURE_DOC_INTELLIGENCE`

Search for `split_name\(|read_pdf\(`

**Yes. There are two AI-related integrations, both in the backend’s DASH/MVR document workflow—not a general-purpose AI assistant.**

1. **Azure AI Document Intelligence reads DASH PDFs.** The backend sends uploaded PDF bytes to Azure’s `prebuilt-layout` model and receives markdown text. That extracted text is then processed by deterministic Python parsing rules to pull out selected driver, policy, and vehicle details. In other words, Azure handles document reading; the application code handles the field extraction. See `dash_azure.py`, `dash_parse.py`, and the DASH processing service in `dash.py`.

2. **Anthropic Claude handles a narrow name-splitting case.** The app first resolves names using local rules—for example, names with a comma or a single word. Only ambiguous, multiword names without a comma are sent to Claude to determine first, middle, and last name. If the API key is absent or the call fails, the parts are left blank for the broker to review rather than guessed. See `claude_client.py` and its use in `driver.py`.

**Overall flow:** DASH PDF → Azure document reading → local parsing → Claude only for ambiguous names → broker reviews or corrects the resulting fields.

The integrations are configured through `AZURE_DOC_INTELLIGENCE_ENDPOINT`, `AZURE_DOC_INTELLIGENCE_KEY`, and `ANTHROPIC_API_KEY` in `.env.example`. The project also contains a reference to an older Gemini-based attempt, but the current DASH reader uses Azure, and name interpretation uses Claude.

I didn’t find evidence of an AI chatbot, broker-facing copilot, or broad AI-driven sales recommendations in the code I checked. The tagged `analyze.md` does not appear to describe a separate AI workflow.