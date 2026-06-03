#!/usr/bin/env bash
# End-to-end verification: corex-sales-service (+ optional WP reachability).
#
# Usage:
#   ./scripts/verify-workflow.sh              # service-only (default)
#   ./scripts/verify-workflow.sh --full       # also requires WP up + webhook round-trip
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export ROOT

# shellcheck disable=SC1091
source "$ROOT/scripts/load-env.sh"
load_sales_env "$ROOT"
apply_sales_env_defaults "$ROOT"

FULL=0
if [[ "${1:-}" == "--full" ]]; then
  FULL=1
fi

BASE="http://${HOST:-127.0.0.1}:${PORT:-8081}"
WP_URL="${SALES_SITE_URL:-http://dev.corexbizhome.com:10004}"
WEBHOOK_URL="${WP_URL%/}/wp-json/corexbiz/v1/sales/run-webhook"

pass=0
fail=0
warn=0

ok()   { echo "  ✓ $*"; pass=$((pass + 1)); }
bad()  { echo "  ✗ $*"; fail=$((fail + 1)); }
note() { echo "  ! $*"; warn=$((warn + 1)); }

section() {
  echo ""
  echo "── $* ──"
}

section "0. Environment"
if [[ -n "${API_TOKEN:-}" ]]; then ok "API_TOKEN set (${#API_TOKEN} chars)"; else bad "API_TOKEN missing"; fi
if [[ -n "${GOOGLE_MAPS_API_KEY:-}" ]]; then ok "GOOGLE_MAPS_API_KEY set"; else bad "GOOGLE_MAPS_API_KEY missing"; fi
if [[ -n "${SALES_SITE_ID:-}" ]]; then ok "SALES_SITE_ID set"; else bad "SALES_SITE_ID missing"; fi
if [[ -n "${WEBHOOK_SIGNING_SECRET:-}" ]]; then ok "WEBHOOK_SIGNING_SECRET set"; else note "WEBHOOK_SIGNING_SECRET missing — webhooks skipped"; fi
ok "Service base: $BASE"
ok "WP site URL:  $WP_URL"

section "1. Service health"
HEALTH="$(curl -sS -m 5 "$BASE/health" 2>/dev/null || true)"
if echo "$HEALTH" | grep -q '"ok":true'; then
  ok "GET /health → ok"
else
  bad "GET /health failed — is ./scripts/start-local.sh running?"
  echo "$HEALTH"
  echo ""
  echo "Summary: pass=$pass fail=$fail warn=$warn — fix service before continuing."
  exit 1
fi
if echo "$HEALTH" | grep -q '"configured":true'; then
  ok "Google Maps configured on service"
else
  bad "Google Maps not configured on service"
fi

section "2. WordPress reachability"
WP_CODE="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "$WP_URL/" 2>/dev/null || echo 000)"
if [[ "$WP_CODE" =~ ^[23] ]]; then
  ok "WP responds at $WP_URL (HTTP $WP_CODE)"
  WP_UP=1
else
  bad "WP not reachable at $WP_URL (HTTP $WP_CODE)"
  note "Start local WP before --full or UI verification"
  WP_UP=0
fi

if [[ "$WP_UP" == 1 ]]; then
  WH_CODE="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' -X POST "$WEBHOOK_URL" 2>/dev/null || echo 000)"
  if [[ "$WH_CODE" == "401" || "$WH_CODE" == "403" ]]; then
    ok "Webhook route exists (HTTP $WH_CODE without signature — expected)"
  elif [[ "$WH_CODE" =~ ^2 ]]; then
    note "Webhook returned HTTP $WH_CODE without signature (unexpected)"
  else
    bad "Webhook route not reachable ($WEBHOOK_URL → HTTP $WH_CODE)"
  fi
fi

section "3. Create run (service API — same payload WP forwards)"
CREATE_BODY="$(cat <<EOF
{
  "list_name": "verify workflow",
  "source_type": "google_maps",
  "criteria": {
    "location": {
      "scope": "radius",
      "radius_center": "1920 Willingdon Ave, Burnaby, BC",
      "radius_value": 10,
      "radius_unit": "km"
    },
    "postal_code": "1920 Willingdon Ave, Burnaby, BC"
  },
  "notes": "verify-workflow.sh",
  "webhook_url": "$WEBHOOK_URL"
}
EOF
)"

CREATE_RESP="$(curl -sS -m 30 -X POST "$BASE/api/v1/runs" \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "X-Corexbiz-Server-Id: ${SALES_SITE_ID}" \
  -H "X-Corexbiz-Site-Url: ${SALES_SITE_URL}" \
  -d "$CREATE_BODY" 2>/dev/null || true)"

RUN_ID="$(echo "$CREATE_RESP" | "$ROOT/.venv/bin/python3" -c "import json,sys; d=json.load(sys.stdin); print(d.get('run_id',''))" 2>/dev/null || true)"

if [[ -z "$RUN_ID" ]]; then
  bad "POST /api/v1/runs failed: $CREATE_RESP"
  echo ""
  echo "Summary: pass=$pass fail=$fail warn=$warn"
  exit 1
fi
ok "Run accepted run_id=$RUN_ID"

section "4. Wait for completion (max 120s)"
STATUS=""
for i in $(seq 1 60); do
  POLL="$(curl -sS -m 10 "$BASE/api/v1/runs/$RUN_ID" \
    -H "Authorization: Bearer ${API_TOKEN}" \
    -H "X-Corexbiz-Server-Id: ${SALES_SITE_ID}" \
    -H "X-Corexbiz-Site-Url: ${SALES_SITE_URL}" 2>/dev/null || true)"
  STATUS="$(echo "$POLL" | "$ROOT/.venv/bin/python3" -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)"
  RUNNING="$(echo "$POLL" | "$ROOT/.venv/bin/python3" -c "import json,sys; print(json.load(sys.stdin).get('running',True))" 2>/dev/null || true)"
  if [[ "$STATUS" == "completed" || "$STATUS" == "failed" ]]; then
    break
  fi
  if [[ "$RUNNING" == "False" && "$STATUS" != "running" && "$STATUS" != "queued" ]]; then
    break
  fi
  sleep 2
done

if [[ "$STATUS" == "completed" ]]; then
  ok "Run completed"
elif [[ "$STATUS" == "failed" ]]; then
  ERR="$(echo "$POLL" | "$ROOT/.venv/bin/python3" -c "import json,sys; print((json.load(sys.stdin).get('error') or '')[:200])" 2>/dev/null || true)"
  bad "Run failed: $ERR"
else
  bad "Run still in progress or unknown status: $STATUS"
fi

section "5. Leads in Postgres (via API)"
LEADS_RESP="$(curl -sS -m 15 "$BASE/api/v1/runs/$RUN_ID/leads?per_page=100" \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H "X-Corexbiz-Server-Id: ${SALES_SITE_ID}" \
  -H "X-Corexbiz-Site-Url: ${SALES_SITE_URL}" 2>/dev/null || true)"
TOTAL="$(echo "$LEADS_RESP" | "$ROOT/.venv/bin/python3" -c "import json,sys; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo 0)"
if [[ "${TOTAL:-0}" -gt 0 ]]; then
  ok "Qualified leads: $TOTAL"
  echo "$LEADS_RESP" | "$ROOT/.venv/bin/python3" -c "
import json, sys
d = json.load(sys.stdin)
for L in d.get('leads', [])[:5]:
    print(f\"    · {L.get('business_name','?')} — {L.get('city','')}\")
if d.get('total', 0) > 5:
    print(f\"    … and {d['total'] - 5} more\")
" 2>/dev/null || true
else
  bad "No leads returned for run"
fi

section "6. On-disk artifacts"
RUN_DIR="$ROOT/runs/${RUN_ID}_google_maps"
if [[ -d "$RUN_DIR" ]]; then
  ok "Run folder: $RUN_DIR"
  if [[ -f "$RUN_DIR/finder_queries.json" ]]; then
    ok "Query: $(cat "$RUN_DIR/finder_queries.json")"
  fi
else
  note "Run folder not found (may be normal if cleaned up)"
fi

if [[ "$FULL" == 1 && "$WP_UP" == 1 ]]; then
  section "7. WP sync (manual — requires logged-in admin)"
  note "In WP admin → Clients → Leads, confirm list \"verify workflow\" appears after webhook."
  note "Or with WP REST nonce: GET $WP_URL/wp-json/corexbiz/v1/sales/discovery-status"
  note "Or: GET $WP_URL/wp-json/corexbiz/v1/sales/leads-bundle"
fi

section "Summary"
echo "  pass=$pass  fail=$fail  warn=$warn"
echo "  run_id=$RUN_ID  status=${STATUS:-unknown}  leads=${TOTAL:-0}"

WP_PATH="${WP_PATH:-/Users/tobymalek/Local Sites/corex-composer/app/public}"
if [[ -f "$WP_PATH/wp-config.php" ]] && command -v wp >/dev/null 2>&1; then
  section "7. WordPress local DB (wp-cli)"
  WP_LEADS="$(wp db query "SELECT COUNT(*) AS n FROM wp_cbz_sales_leads WHERE remote_run_id='$RUN_ID'" --path="$WP_PATH" --skip-column-names 2>/dev/null || echo 0)"
  WP_RUN="$(wp db query "SELECT id FROM wp_cbz_sales_runs WHERE remote_run_id='$RUN_ID' LIMIT 1" --path="$WP_PATH" --skip-column-names 2>/dev/null || true)"
  if [[ "${WP_LEADS:-0}" -gt 0 ]]; then
    ok "WP leads table: $WP_LEADS rows for this run"
  else
    bad "WP leads table: no rows for run_id (webhook sync may have failed)"
  fi
  if [[ -n "${WP_RUN:-}" ]]; then
    ok "WP runs table: local id=$WP_RUN"
  else
    bad "WP runs table: no row for run_id (check SalesSyncService)"
  fi
  WP_EVT="$(wp db query "SELECT event_type FROM wp_cbz_sales_run_events WHERE run_id='$RUN_ID' ORDER BY id DESC LIMIT 1" --path="$WP_PATH" --skip-column-names 2>/dev/null || true)"
  if [[ -n "${WP_EVT:-}" ]]; then
    ok "WP SSE event: $WP_EVT"
  else
    note "No wp_cbz_sales_run_events row for this run"
  fi
fi

echo ""
if [[ "$fail" -gt 0 ]]; then
  echo "Some checks failed. See output above."
  exit 1
fi
if [[ "$WP_UP" == 0 ]]; then
  echo "Service pipeline OK. Start WordPress, then verify UI path:"
  echo "  1. wp-config: COREXBIZ_SALES_SERVICE_BASE_URL=$BASE"
  echo "  2. Clients → Leads → Generate List (Google Maps)"
  echo "  3. Or re-run: ./scripts/verify-workflow.sh --full"
  exit 0
fi
echo "Service pipeline OK. Complete UI check in WP admin (Clients → Leads)."
exit 0
