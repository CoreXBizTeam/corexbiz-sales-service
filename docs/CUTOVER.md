# Phase 8 — Legacy cutover

Production CoreXLeads no longer uses:

- `leads_dev_api.py` on `:8765`
- Static `assets/dev/leads.json` in the WordPress plugin
- Direct SQLite reads from the Vue SPA

## New flow

**Deploy (Cloud Run):** [docs/DEPLOY_CLOUD_RUN.md](./DEPLOY_CLOUD_RUN.md)

**Full workflow (config, curl, criteria mapping):** `corexbiz-core/documentation/clients/SALES_LEADS_WP_SERVICE_WORKFLOW.md`

```
Vue (CoreXLeads) → WP REST /wp-json/corexbiz/v1/sales/*
                 → corex-sales-service (Postgres)
                 ← webhook run.completed → WP local tables
```

## One-time migration from `corex_leads.db`

1. Start Cloud SQL proxy and sales-service locally (or use Cloud Run).

2. Import legacy SQLite into Postgres:

```bash
cd corex-sales-python
source .venv/bin/activate

# site-id MUST match WordPress SubscriptionService server_id (not "dev-server").
# Get it once from your local WP:
#   php -r "require 'wp-load.php'; \$s=new CoreXBiz\Core\Services\SubscriptionService(); echo \$s->getShareServiceValidationFields()['server_id'];"

python scripts/migrate_legacy_sqlite.py \
  --db ./corex_leads.db \
  --site-id 'YOUR_WP_SERVER_ID_SHA256' \
  --site-url http://dev.corexbizhome.com:10004 \
  --webhook
```

If Postgres import succeeded but webhook failed, fix `site_id` on the run and retry:

```bash
python scripts/migrate_legacy_sqlite.py --webhook-only RUN_UUID
```

3. Confirm WordPress received the webhook (`wp_cbz_sales_runs` / `wp_cbz_sales_leads`).

4. Open **Clients → CoreXLeads** — data loads via `salesRestUrl` only.

## WordPress config

```php
define('COREXBIZ_SALES_SERVICE_BASE_URL', 'http://127.0.0.1:8080'); // or Cloud Run URL
define('COREXBIZ_SALES_SERVICE_API_TOKEN', '…');
define('COREXBIZ_SALES_WEBHOOK_SIGNING_SECRET', '…');
```

## Deprecated (kept for debugging)

| Tool | Replacement |
|------|-------------|
| `leads_dev_api.py` | FastAPI `:8080` + WP REST |
| `export_qualified_leads_json.py` | WP REST `GET …/sales/leads-bundle` |
| `corex-leads-review` Vite proxy → `:8765` | Set `VITE_COREX_LEADS_API` to WP REST URL |

## Standalone review app

In `corex-leads-review/.env.local`:

```
VITE_COREX_LEADS_API=https://your-site.test/wp-json/corexbiz/v1/sales
```

Use a WP application password or dev nonce strategy for authenticated REST if needed.
