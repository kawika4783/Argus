# Argus / NexusIntel MVP

Self-hosted intelligence workspace MVP with a protected FastAPI API and responsive Next.js command center. The first slice is deliberately small and verifiable: login/logout, session cookies, synthetic case/entity/relationship data, dashboard view, and a basic graph explorer.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r apps/api/requirements.txt
$env:NEXUSINTEL_DEMO_PASSWORD = "change-me-locally"
uvicorn apps.api.app.main:app --reload --port 8000
```

In another shell:

```powershell
cd apps/web
npm install
npm run dev
```

Open http://localhost:3000. Demo login: `analyst` / the value of `NEXUSINTEL_DEMO_PASSWORD`.

## Compose

Copy `.env.example` to `.env`, set a strong session secret, then run `docker compose up --build`. PostgreSQL is included, but local development defaults to SQLite when no database URL is provided.

## Scope

This MVP contains synthetic data only. Evidence storage, Neo4j projection, MFA, ingestion workers, maps, AI/RAG, reports, and automation are staged for subsequent milestones. The API remains the authorization boundary.
