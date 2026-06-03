#!/usr/bin/env python3
"""
DEPRECATED (Phase 8): use corex-sales-service FastAPI + WordPress REST proxy instead.

This standalone dev server on :8765 is replaced by:
  - corex-sales-python FastAPI  POST/GET /api/v1/runs  (./scripts/start-local.sh)
  - corexbiz-core WP REST     /wp-json/corexbiz/v1/sales/*

Kept for emergency local debugging only. Will be removed in a future release.

Original endpoints (legacy):
  GET  /api/leads-bundle
  GET  /api/qualified-leads
  ...
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import db as dbmod

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "corex_leads.db"
HOST = "127.0.0.1"
PORT = 8765

_discovery_lock = threading.Lock()
_discovery_state: Dict[str, Any] = {
    "running": False,
    "status": "idle",
    "run_id": None,
    "list_name": None,
    "source_type": None,
    "output_stub": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "message": None,
}


def _discovery_status_payload() -> Dict[str, Any]:
    with _discovery_lock:
        return dict(_discovery_state)


def _run_pipeline_job(config: Dict[str, Any]) -> None:
    run_id = str(config.get("run_id") or uuid.uuid4())
    source_type = str(config.get("source_type", "google_maps"))
    list_name = config.get("list_name")
    output_stub = str(ROOT / "runs" / f"{run_id}_{source_type}")
    config["run_id"] = run_id
    config["output_stub"] = output_stub

    with _discovery_lock:
        _discovery_state["running"] = True
        _discovery_state["status"] = "running"
        _discovery_state["run_id"] = run_id
        _discovery_state["list_name"] = list_name
        _discovery_state["source_type"] = source_type
        _discovery_state["output_stub"] = output_stub
        _discovery_state["started_at"] = datetime.now(timezone.utc).isoformat()
        _discovery_state["finished_at"] = None
        _discovery_state["error"] = None
        _discovery_state["message"] = f"Running pipeline ({source_type})…"

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            json.dump(config, tmp)
            config_path = tmp.name
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "run_lead_pipeline.py"),
                "--config",
                config_path,
                "--db",
                str(DB_PATH),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        Path(config_path).unlink(missing_ok=True)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "pipeline failed").strip()
            raise RuntimeError(err[:2000])
        with _discovery_lock:
            _discovery_state["status"] = "completed"
            _discovery_state["message"] = (
                f"Lead list “{list_name or run_id}” finished successfully."
            )
    except Exception as exc:  # noqa: BLE001 — dev API surfaces pipeline errors
        with _discovery_lock:
            _discovery_state["status"] = "failed"
            _discovery_state["error"] = str(exc)
            _discovery_state["message"] = "Lead run failed."
    finally:
        with _discovery_lock:
            _discovery_state["running"] = False
            _discovery_state["finished_at"] = datetime.now(timezone.utc).isoformat()


def _run_discovery_job() -> None:
    _run_pipeline_job(
        {
            "list_name": "Quick discovery",
            "source_type": "google_maps",
            "criteria": {"cities_file": "cities.csv"},
        }
    )


def _start_job(config: Dict[str, Any]) -> Dict[str, Any]:
    with _discovery_lock:
        if _discovery_state["running"]:
            return {"started": False, "reason": "already_running"}
        threading.Thread(target=_run_pipeline_job, args=(config,), daemon=True).start()
        return {"started": True}


def _start_discovery() -> Dict[str, Any]:
    return _start_job(
        {
            "list_name": "Quick discovery",
            "source_type": "google_maps",
            "criteria": {"cities_file": "cities.csv"},
        }
    )


def _cors_headers() -> Dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: Any) -> None:
    data = json.dumps(body, default=str).encode("utf-8")
    handler.send_response(status)
    for k, v in _cors_headers().items():
        handler.send_header(k, v)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class LeadsHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[leads_dev_api] {self.address_string()} - {fmt % args}")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        for k, v in _cors_headers().items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/leads-bundle":
            conn = dbmod.get_connection(str(DB_PATH))
            try:
                raw_leads = dbmod.get_all_leads(conn)
                qualified_leads = dbmod.get_all_qualified_leads(conn)
                tracker_rows = dbmod.get_all_tracker_rows(conn)
                export_log = dbmod.get_recent_exports(conn, 5)
            finally:
                conn.close()
            _json_response(
                self,
                200,
                {
                    "raw_leads": raw_leads,
                    "qualified_leads": qualified_leads,
                    "leads": qualified_leads,
                    "tracker_rows": tracker_rows,
                    "exports": export_log,
                },
            )
            return
        if path == "/api/qualified-leads":
            conn = dbmod.get_connection(str(DB_PATH))
            try:
                leads = dbmod.get_all_qualified_leads(conn)
            finally:
                conn.close()
            _json_response(self, 200, {"leads": leads, "count": len(leads)})
            return
        if path == "/health":
            _json_response(self, 200, {"ok": True, "db": str(DB_PATH)})
            return
        if path == "/api/discovery-status":
            _json_response(self, 200, _discovery_status_payload())
            return
        _json_response(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/run-discovery":
            result = _start_discovery()
            status = 200 if result.get("started") else 409
            _json_response(self, status, {**result, **_discovery_status_payload()})
            return
        if path == "/api/lead-runs":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                _json_response(self, 400, {"error": "invalid json"})
                return
            source_type = str(body.get("source_type", "")).strip()
            if not source_type:
                _json_response(self, 400, {"error": "source_type is required"})
                return
            allowed = {"google_maps", "google_web", "manual_csv", "custom_script"}
            if source_type not in allowed:
                _json_response(
                    self, 400, {"error": f"source_type must be one of {sorted(allowed)}"}
                )
                return
            config = {
                "run_id": str(uuid.uuid4()),
                "list_name": str(body.get("list_name", "")).strip() or None,
                "source_type": source_type,
                "criteria": body.get("criteria") if isinstance(body.get("criteria"), dict) else {},
                "notes": str(body.get("notes", "")),
            }
            result = _start_job(config)
            status = 200 if result.get("started") else 409
            _json_response(self, status, {**result, **_discovery_status_payload()})
            return
        _json_response(self, 404, {"error": "not found"})

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        prefix = "/api/qualified-leads/"
        if not path.startswith(prefix):
            _json_response(self, 404, {"error": "not found"})
            return
        try:
            lead_id = int(path[len(prefix) :].strip("/"))
        except ValueError:
            _json_response(self, 400, {"error": "invalid id"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            _json_response(self, 400, {"error": "invalid json"})
            return

        status = str(body.get("review_status", "")).strip().lower()
        notes = str(body.get("notes", ""))
        if status not in dbmod.REVIEW_STATUS_VALUES:
            _json_response(
                self,
                400,
                {"error": f"review_status must be one of {dbmod.REVIEW_STATUS_VALUES}"},
            )
            return

        conn = dbmod.get_connection(str(DB_PATH))
        try:
            conn.execute(
                "UPDATE qualified_leads SET review_status = ?, notes = ? WHERE id = ?",
                (status, notes, lead_id),
            )
            if conn.total_changes == 0:
                _json_response(self, 404, {"error": "lead not found"})
                return
            conn.commit()
        except sqlite3.Error as exc:
            _json_response(self, 500, {"error": str(exc)})
            return
        finally:
            conn.close()

        _json_response(self, 200, {"id": lead_id, "review_status": status, "notes": notes})


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found: {DB_PATH}")
    print(
        "WARNING: leads_dev_api.py is DEPRECATED (Phase 8). "
        "Use FastAPI :8080 + WP REST /wp-json/corexbiz/v1/sales/* instead.",
        file=sys.stderr,
    )
    server = ThreadingHTTPServer((HOST, PORT), LeadsHandler)
    print(f"Leads dev API on http://{HOST}:{PORT} (db={DB_PATH})")
    server.serve_forever()


if __name__ == "__main__":
    main()
