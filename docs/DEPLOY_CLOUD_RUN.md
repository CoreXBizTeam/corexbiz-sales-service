# Deploy CoreX Sales Service to Google Cloud Run

The sales service is a **Cloud Run service** running FastAPI (`uvicorn src.api.main:app`) on port `8080`. Accepted runs are **inserted into Postgres** (`runs.status=queued`); execution is scheduled via **FastAPI BackgroundTasks** (keeps CPU after the 202 response) and a **worker pool** (default **4** threads) for orphaned queued jobs.

**Cloud Run:** deploy with **`--no-cpu-throttling`** (default in `deploy.sh`). Without it, daemon worker threads stall after `POST /runs` returns 202 and admin verify / lead runs never webhooks back.

## Prerequisites

- `gcloud` CLI authenticated with deploy permissions
- APIs enabled: **Cloud Run**, **Cloud Build**, **Artifact Registry** (or Container Registry), **Cloud SQL Admin**
- Cloud SQL Postgres instance (sandbox: `corexbiz:us-west1:postgres-17-sandbox`)
- Optional: **Secret Manager** secrets for `GOOGLE_MAPS_API_KEY`, `API_TOKEN`, `WEBHOOK_SIGNING_SECRET`

## Configure `.env`

Copy `.env.example` → `.env` and set at minimum:

```bash
CLOUD_SQL_CONNECTION_NAME=corexbiz:us-west1:postgres-17-sandbox
POSTGRES_USER=postgres
POSTGRES_PASSWORD='…'
POSTGRES_DB=corexbiz-db
POSTGRES_SCHEMA=sales-service

# Cloud Run unix socket URL (deploy.sh builds this if omitted)
CLOUD_RUN_DATABASE_URL=postgresql://postgres:PASSWORD@/corexbiz-db?host=/cloudsql/corexbiz:us-west1:postgres-17-sandbox
```

Prefer Secret Manager in production:

```bash
SECRET_GOOGLE_MAPS_API_KEY=corex-sales-google-maps-api-key:latest
SECRET_API_TOKEN=corex-sales-api-token:latest
SECRET_WEBHOOK_SIGNING_SECRET=corex-sales-webhook-signing-secret:latest
```

## Deploy

```bash
chmod +x deploy.sh
./deploy.sh              # dev: corex-sales-service-dev
./deploy.sh --production # prod: corexbiz-sales-service
```

Options:

| Flag | Purpose |
|------|---------|
| `--skip-build` | Reuse `CONTAINER_IMAGE` or existing tag |

### Resource defaults (override via env)

| Variable | Default | Notes |
|----------|---------|-------|
| `SERVICE_MEMORY` | `2Gi` | Worker pool + API |
| `SERVICE_CPU` | `2` | Worker pool + API |
| `SALES_WORKER_POOL_SIZE` | `4` | Max concurrent pipeline workers |
| `SERVICE_CONCURRENCY` | `80` | HTTP concurrency |
| `SERVICE_TIMEOUT` | `3600` | Max request + background run window (seconds) |
| `SERVICE_MIN_INSTANCES` | `0` | Scale to zero when idle |
| `SERVICE_MAX_INSTANCES` | `10` | Multi-instance; Postgres queue uses `SKIP LOCKED` |

## One-time IAM

Grant the service account permission to reach Cloud SQL:

```bash
./scripts/grant-cloud-run-iam.sh --dev
# or
./scripts/grant-cloud-run-iam.sh --production
```

## WordPress

Set the deployed service URL in `wp-config.php`:

```php
define('COREXBIZ_SALES_SERVICE_BASE_URL', 'https://corex-sales-service-dev-….run.app');
define('COREXBIZ_SALES_SERVICE_API_TOKEN', '…');
define('COREXBIZ_SALES_WEBHOOK_SIGNING_SECRET', '…');
```

Verify:

```bash
curl -sS "https://YOUR-SERVICE.run.app/health" | python3 -m json.tool
./scripts/verify-workflow.sh
```

## Admin UI (request tracing)

After deploy, open **`https://YOUR-SERVICE.run.app/admin`**.

Set in `.env` before `./deploy.sh`:

```bash
ADMIN_PASSWORD=your-admin-password
# optional: ADMIN_SESSION_SECRET=…  (defaults to API_TOKEN)
```

The admin UI provides:

- **Overview** — pipeline mode, database, Google Maps config
- **Request logs** — Cloud Logging on Cloud Run (filter by `request_id` / `rid=…` on each line)
- **Active runs** — in-memory runs on the current Cloud Run instance

Each HTTP response includes **`X-Request-Id`**. When WordPress triggers a run, copy that header (or filter logs) to trace the full request/response chain.

Grant **`roles/logging.viewer`** to the Cloud Run service account if `/admin/logs` returns a credentials error.

## Architecture notes

- **Cloud SQL**: `--set-cloudsql-instances` mounts the unix socket; `DATABASE_URL` uses `host=/cloudsql/INSTANCE`
- **No local `.env` in container**: deploy sets `SALES_DISABLE_DOTENV=1`
- **Public invoke** by default (`--allow-unauthenticated`); app auth uses `Authorization: Bearer` + `API_TOKEN`
- **Health checks**: `/health` startup + liveness probes on the service
- **Manual pipeline CLI**: `python -m src.worker.run_job` with `SALES_RUN_SPEC` (local/debug only; production uses inline threads)
