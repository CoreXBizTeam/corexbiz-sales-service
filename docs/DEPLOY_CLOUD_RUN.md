# Deploy CoreX Sales Service to Google Cloud Run

The sales service runs as:

1. **Cloud Run service** — FastAPI HTTP API (`uvicorn src.api.main:app`) on port `8080`
2. **Cloud Run Job** — pipeline worker (`python -m src.worker.run_job`) dispatched per lead run

One container image serves both. The API returns `202` immediately and starts the job with the full run spec in `SALES_RUN_SPEC`.

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

# Production worker mode
SALES_WORKER_MODE=job
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
./deploy.sh              # dev: corex-sales-service-dev + corex-sales-pipeline-job-dev
./deploy.sh --production # prod: corexbiz-sales-service + corexbiz-sales-pipeline-job
```

Options:

| Flag | Purpose |
|------|---------|
| `--service-only` | Update HTTP service only |
| `--job-only` | Update pipeline job only |
| `--skip-build` | Reuse `CONTAINER_IMAGE` or existing tag |

### Resource defaults (override via env)

| Variable | Default | Applies to |
|----------|---------|------------|
| `SERVICE_MEMORY` | `1Gi` | HTTP service |
| `SERVICE_CPU` | `1` | HTTP service |
| `SERVICE_CONCURRENCY` | `80` | HTTP service |
| `SERVICE_TIMEOUT` | `300` | HTTP service (seconds) |
| `SERVICE_MAX_INSTANCES` | `10` | HTTP service |
| `JOB_MEMORY` | `2Gi` | Pipeline job |
| `JOB_CPU` | `2` | Pipeline job |
| `JOB_TASK_TIMEOUT` | `3600` | Pipeline job (seconds) |

## One-time IAM

After the first deploy, grant the service account permission to execute jobs and reach Cloud SQL:

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

## Architecture notes

- **Cloud SQL**: `--set-cloudsql-instances` mounts the unix socket; `DATABASE_URL` uses `host=/cloudsql/INSTANCE`
- **No local `.env` in container**: deploy sets `SALES_DISABLE_DOTENV=1`
- **Public invoke** by default (`--allow-unauthenticated`); app auth uses `Authorization: Bearer` + `API_TOKEN`
- **Health checks**: `/health` startup + liveness probes on the service
- **Job CPU**: pipeline job uses 2 CPU / 2Gi by default (`JOB_CPU`, `JOB_MEMORY`)
