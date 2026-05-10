# Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure Render (backend FastAPI) and Vercel (frontend Next.js) deployments with all required environment variables, health check configuration, and a build verification step.

**Architecture:** Backend runs on Render as a Docker-backed Web Service (`render.yaml`). The Airflow MCP subprocess runs as a long-lived child process inside the same Render instance. Vercel deploys the Next.js frontend from `frontend/`. `NEXT_PUBLIC_API_URL` points to the Render service URL.

**Tech Stack:** Render (Web Service), Vercel (Next.js), Docker

---

### Task 0: Account Setup and Prerequisites

> Complete all steps in this task before touching any config files. None of the later tasks will work without the services provisioned here.

- [ ] **Step 1: Create a GitHub repository and push the codebase**

1. Go to [github.com/new](https://github.com/new) and create a new **private** repository named `pipeline-observability`.
2. Do not initialise it with a README (the local repo already has commits).
3. Add the remote and push:

```bash
git remote add origin https://github.com/<your-org>/pipeline-observability.git
git push -u origin main
```

---

- [ ] **Step 2: Provision PostgreSQL databases on Neon**

The backend uses two logical databases within a single Neon project:
- `pipeline_watch` — embeddings, metrics caches, and the audit log (backend's own tables)
- `source_data` — monitored business tables (`orders`, `users`, `inventory_items`, `revenue_aggregate`, `airflow_task_logs`)

The Airflow metadata database stays on Render Postgres (co-located with Airflow — see Step 3 note).

1. Go to [console.neon.tech](https://console.neon.tech) and sign up or log in.
2. Click **New Project**, name it `pipeline-observability`, and choose a region.
3. Neon creates a default database named `neondb`. Rename it to `pipeline_watch` in **Settings → Databases**, or create a new database named `pipeline_watch` and delete the default.
4. In **Databases**, click **New Database** and create `source_data`.
5. On the project **Dashboard**, copy the connection string for each database:
   - `pipeline_watch` connection string → `DATABASE_URL`
   - `source_data` connection string → `SOURCE_DATABASE_URL`
   Both strings will have the same host; only the `dbname` parameter differs.
6. Add `sslmode=require` to both connection strings (Neon requires SSL):
   ```
   DATABASE_URL=postgresql://user:password@host.neon.tech:5432/pipeline_watch?sslmode=require
   SOURCE_DATABASE_URL=postgresql://user:password@host.neon.tech:5432/source_data?sslmode=require
   ```

Run the schema migrations locally before deploying:

```bash
# pipeline_watch schema (embeddings, metrics, audit)
psql "$DATABASE_URL" -f backend/migrations/001_schema.sql

# source_data schema (monitored business tables)
psql "$SOURCE_DATABASE_URL" -f backend/migrations/002_source_schema.sql
```

Expected: no errors; `\dt` in psql against each DB should list the relevant tables.

---

- [ ] **Step 3: Create a Render account and connect GitHub**

1. At [dashboard.render.com](https://dashboard.render.com), go to **Account Settings → GitHub**.
2. Click **Connect GitHub** and authorise Render to access your organisation or personal account.
3. Grant access to the `pipeline-observability` repository specifically (or all repos — your choice).

---

- [ ] **Step 4: Create the Render Web Service**

1. Click **New → Web Service**.
2. Select the `pipeline-observability` repository.
3. Fill in:
   - **Name:** `pipeline-observability-api`
   - **Region:** same region as the database from Step 2
   - **Branch:** `main`
   - **Runtime:** Docker
   - **Dockerfile Path:** `backend/Dockerfile`
   - **Docker Build Context:** `backend`
   - **Plan:** Standard (the free plan does not support always-on services; the MCP subprocess requires a persistent process)
4. Under **Health Check Path**, enter `/health`.
5. Do **not** click Deploy yet — add env vars first (Step 5).

---

- [ ] **Step 5: Set secret environment variables in Render**

In the Web Service's **Environment** tab, add the following. Variables marked `sync: false` in `render.yaml` must be entered manually here because they contain credentials.

| Variable | Where to get the value |
|---|---|
| `DATABASE_URL` | Neon `pipeline_watch` connection string from Step 2 (include `?sslmode=require`) |
| `SOURCE_DATABASE_URL` | Neon `source_data` connection string from Step 2 (include `?sslmode=require`) |
| `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `COHERE_API_KEY` | [dashboard.cohere.com/api-keys](https://dashboard.cohere.com/api-keys) |
| `AIRFLOW_BASE_URL` | Your Airflow instance URL, e.g. `https://airflow.your-org.com/api/v1` |
| `AIRFLOW_TOKEN` | Airflow → Admin → Connections → generate a token, or use username/password instead (set `AIRFLOW_USERNAME` + `AIRFLOW_PASSWORD`) |
| `AUDIT_HMAC_SECRET` | Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |

Click **Save Changes**. The non-secret vars (`FRONTEND_ORIGIN`, `MAX_CONCURRENT_LIVE_CHECKS`, etc.) will be applied automatically from `render.yaml` when the service is created.

Now click **Create Web Service**. Render will pull the repo, build the Docker image, and deploy. First build takes ~5 minutes.

---

- [ ] **Step 6: Create a Vercel account and connect GitHub**

1. Go to [vercel.com](https://vercel.com) and sign up or log in with GitHub.
2. Click **Add New → Project**.
3. Select the `pipeline-observability` repository.
4. Set **Root Directory** to `frontend`.
5. Framework will be auto-detected as **Next.js** — leave all build settings at defaults.
6. Before clicking Deploy, expand **Environment Variables** and add:

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | Your Render service URL, e.g. `https://pipeline-observability-api.onrender.com` |
| `API_KEY` | Any strong random string — this is the key the dashboard server component sends as `X-API-Key` to the backend |

7. Click **Deploy**. First deployment takes ~2 minutes.

---

## File Map

```
backend/
├── Dockerfile
└── render.yaml           # Render IaC config
frontend/
└── vercel.json           # Vercel routing config
.env.example              # canonical env var reference (already created in plan 01)
```

---

### Task 1: Write and Verify the Dockerfile

**Files:**
- Create: `backend/Dockerfile`

- [ ] **Step 1: Write `backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system deps for asyncpg and presidio
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download Presidio NLP model
RUN python -m spacy download en_core_web_lg

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Build and verify locally**

```bash
cd backend && docker build -t pipeline-observability-backend .
docker run --rm -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:pass@host/db \
  -e OPENAI_API_KEY=sk-test \
  -e COHERE_API_KEY=test \
  -e AIRFLOW_BASE_URL=http://localhost:8080/api/v1 \
  -e AUDIT_HMAC_SECRET=test-secret \
  pipeline-observability-backend &
sleep 5 && curl -s http://localhost:8000/health | python -m json.tool
docker stop $(docker ps -q --filter ancestor=pipeline-observability-backend)
```

Expected: `{"status": "degraded", ...}` (DB not connected, but HTTP responds).

- [ ] **Step 3: Commit**

```bash
git add backend/Dockerfile
git commit -m "chore: Dockerfile for backend (python 3.12-slim, asyncpg, Presidio)"
```

---

### Task 2: Write Render Configuration

**Files:**
- Create: `render.yaml`

- [ ] **Step 1: Write `render.yaml`**

```yaml
# render.yaml — Render IaC configuration
services:
  - type: web
    name: pipeline-observability-api
    runtime: docker
    dockerfilePath: backend/Dockerfile
    dockerContext: backend
    plan: standard
    healthCheckPath: /health
    autoDeploy: true
    envVars:
      - key: DATABASE_URL
        sync: false          # Neon pipeline_watch DB — set in Render dashboard
      - key: SOURCE_DATABASE_URL
        sync: false          # Neon source_data DB — set in Render dashboard
      - key: OPENAI_API_KEY
        sync: false
      - key: COHERE_API_KEY
        sync: false
      - key: AIRFLOW_BASE_URL
        sync: false
      - key: AIRFLOW_TOKEN
        sync: false
      - key: AUDIT_HMAC_SECRET
        sync: false
      - key: FRONTEND_ORIGIN
        value: https://pipeline-observability.vercel.app
      - key: MAX_CONCURRENT_LIVE_CHECKS
        value: "3"
      - key: MAX_LOG_PAGES
        value: "5"
      - key: CONFIDENCE_THRESHOLD
        value: "0.7"
      - key: RERANK_FALLBACK_THRESHOLD
        value: "0.5"
      - key: STATEMENT_TIMEOUT_MS
        value: "10000"
      - key: OPENAI_MAX_RETRIES
        value: "2"
```

- [ ] **Step 2: Commit**

```bash
git add render.yaml
git commit -m "chore: Render IaC config (web service, health check, env vars)"
```

---

### Task 3: Write Vercel Configuration

**Files:**
- Create: `frontend/vercel.json`

- [ ] **Step 1: Write `frontend/vercel.json`**

```json
{
  "buildCommand": "npm run build",
  "outputDirectory": ".next",
  "framework": "nextjs",
  "rewrites": [
    {
      "source": "/api/:path*",
      "destination": "https://pipeline-observability-api.onrender.com/:path*"
    }
  ]
}
```

- [ ] **Step 2: Set Vercel environment variables**

In the Vercel dashboard for the `frontend` project, set:

| Variable               | Value                                                       |
|------------------------|-------------------------------------------------------------|
| `NEXT_PUBLIC_API_URL`  | `https://pipeline-observability-api.onrender.com`           |
| `API_KEY`              | Your API key (used by the Dashboard server component)       |

- [ ] **Step 3: Commit**

```bash
git add frontend/vercel.json
git commit -m "chore: Vercel config (Next.js, API rewrite to Render backend)"
```

---

### Task 4: Deploy and Smoke Test

- [ ] **Step 1: Push to GitHub to trigger deployments**

```bash
git push origin main
```

- [ ] **Step 2: Verify Render health check**

```bash
curl -s https://pipeline-observability-api.onrender.com/health | python -m json.tool
```

Expected: `{"status": "ok", "mcp": "ok", "db": "ok"}` (or `"degraded"` with specific down components if not fully configured).

- [ ] **Step 3: Verify Vercel frontend loads**

Open `https://pipeline-observability.vercel.app` in a browser.

Expected: Dashboard page loads, status sections render (may show "unavailable" if backend not yet seeded).

- [ ] **Step 4: Smoke test the chat endpoint**

```bash
curl -s -N \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"query": "Are there any pipeline issues?"}' \
  https://pipeline-observability-api.onrender.com/chat
```

Expected: SSE stream of `data:` lines ending with `data: [DONE]`.

- [ ] **Step 5: Commit deployment notes**

```bash
git commit --allow-empty -m "deploy: pipeline-observability to Render (backend) + Vercel (frontend)"
```
