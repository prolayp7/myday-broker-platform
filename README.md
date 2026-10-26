# MyDay Broker Platform

MyDay CRM is a broker-focused sales and lead-management application for assigning insurance leads, managing broker follow-up, and supporting administrative operations.

## Architecture

- **Frontend:** React 19, TypeScript, and Vite, with React Router for navigation and Axios for API requests.
- **Backend:** FastAPI, organized into HTTP routes, business services, Supabase repositories, and Pydantic request/response schemas. Routes handle HTTP, services own business rules, and repositories contain database queries.
- **Database:** Supabase/Postgres, with schema and database behavior maintained through numbered SQL migrations.
- **Deployment:** The backend runs with Uvicorn. The deployment process is configured in `backend/Procfile`.

## Frontend

The main frontend code is under `frontend/src/`:

- `domain/` contains lead types, validation schemas, and pipeline stage configuration.
- `data/` contains the API seam and mock data. Screens use the same API functions whether configured for mocks or a live backend.
- `components/` contains shared layout and UI components.
- `routes/` contains screens such as login, My Day, and Leads.

The frontend uses React Hook Form and Zod for form validation, Tailwind CSS and shadcn/ui primitives for styling, Recharts for dashboard charts, and Axios for API calls.

## Features

### Broker workflows

- Daily work dashboard with pipeline and follow-up reminders.
- Lead lists and details, including contact history and status updates.
- Applicant, driver, vehicle, and property information for insurance quoting.
- Reminder creation and contact logging.
- Separate mobile screens for My Day and lead lists.
- MVR pull logs and report screens.

### Admin workflows

Administrators can manage brokers and broker groups, onboarding, lead reassignment, commission rates, lead costs, campaigns, Meta conversion reporting, and website lead routing. Broker and admin routes have separate access controls.

### Lead intake and background processing

The backend supports Meta and website lead intake, manual lead creation, and retry handling for selected failed work. Additional integrations include Meta conversion signals, WhatsApp notifications, renewal reminders, DASH document handling, and MVR processing. Background tasks process queued conversions, lead retries, reminders, notifications, renewal messages, and website-lead routing.

## Authentication

Backend login uses a two-step password and TOTP flow, with refresh tokens for session renewal. Requests re-check the broker's current database status, and admin endpoints have a separate role check. The frontend stores session data in local storage and uses an Axios interceptor to attach tokens and refresh eligible expired sessions.

## Document processing and AI

AI use is limited to parts of the backend DASH/MVR document workflow; this project does not provide a general-purpose chatbot or AI sales assistant.

- **Azure AI Document Intelligence** reads uploaded DASH PDFs with the `prebuilt-layout` model. Local Python parsing logic then extracts selected driver, policy, and vehicle details.
- **Anthropic Claude** is used only to split ambiguous multiword names into first, middle, and last names. If it is unavailable or fails, the fields are left for broker review rather than guessed.
- A Gemini reference describes an older attempt; the current DASH reader uses Azure, and ambiguous-name handling uses Claude.

Configure these integrations with `AZURE_DOC_INTELLIGENCE_ENDPOINT`, `AZURE_DOC_INTELLIGENCE_KEY`, and `ANTHROPIC_API_KEY` in the backend environment.

## Development

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Vite serves the development app at `http://localhost:5173`. Set `VITE_API_BASE_URL` in a frontend `.env` file to point API functions at a live backend. Without it, supported functions resolve from the in-repository mock data.

Build and type-check the frontend with:

```bash
cd frontend
npm run build
```

### Backend

Create and activate a virtual environment, then install dependencies.

macOS/Linux:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell:

```powershell
cd backend
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in the required values in `backend/.env`, including Supabase credentials and the backend's signing/encryption keys, then start the API from the `backend/` directory:

```bash
uvicorn main:app --reload
```

The API's interactive documentation is available at `http://127.0.0.1:8000/docs`. Keep `.env` and service-role credentials private; the backend connects to Supabase using the service-role key.

## Database migrations

Migrations are in `backend/migrations/` and are applied manually through the Supabase SQL Editor after review. Number new migration files in sequence, and do not assume they auto-deploy. The backend README notes that its historical migration and endpoint summaries are stale; use the migration files and current implementation as the source of truth.

## Further documentation

See [`frontend/README.md`](frontend/README.md) for the frontend stack and structure, and [`backend/README.md`](backend/README.md) for backend architecture, database conventions, and migration guidance.
