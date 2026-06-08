"""Webhook delivery integration test with local HTTP receiver."""

from __future__ import annotations

import json
import os
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_CSV = ROOT / "tests" / "fixtures" / "worker_sample.csv"


def _db_configured() -> bool:
    from src.db.connection import resolve_database_url

    return bool(resolve_database_url()) and os.getenv("SKIP_POSTGRES_TESTS") != "1"


class _WebhookReceiver:
    def __init__(self) -> None:
        self.secret = "integration-webhook-secret"
        self.received: Optional[dict] = None
        self.status = 200
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port = 0

    def start(self) -> None:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                from src.lib.sales_run_webhook_sign import sign_payload

                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                headers = {
                    "X-Corexbiz-Server-Id": self.headers.get("X-Corexbiz-Server-Id", ""),
                    "X-Corexbiz-Timestamp": self.headers.get("X-Corexbiz-Timestamp", ""),
                    "X-Corexbiz-Signature": self.headers.get("X-Corexbiz-Signature", ""),
                }
                site_id = headers["X-Corexbiz-Server-Id"]
                expected = sign_payload(
                    receiver.secret,
                    server_id=site_id,
                    raw_body=raw.decode("utf-8"),
                    timestamp_sec=int(headers["X-Corexbiz-Timestamp"]),
                )
                if expected["X-Corexbiz-Signature"] != headers["X-Corexbiz-Signature"]:
                    self.send_response(401)
                    self.end_headers()
                    return

                receiver.received = json.loads(raw.decode("utf-8"))
                self.send_response(receiver.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, format, *args):  # noqa: A003
                return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


class TestWebhookIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _db_configured():
            raise unittest.SkipTest("DATABASE_URL not configured")

        os.environ["SALES_WORKER_MODE"] = "sync"
        from src.db.migrate import run_migrations
        from src.db.pool import close_pool

        close_pool()
        run_migrations()

    def setUp(self) -> None:
        from src.db.pool import close_pool, get_pool

        close_pool()
        self.pool = get_pool()
        self.run_id = uuid.uuid4()
        self.site_id = f"webhook-int-{uuid.uuid4().hex[:8]}"
        self.receiver = _WebhookReceiver()
        self.receiver.start()

        self.run_spec = {
            "id": str(self.run_id),
            "site_id": self.site_id,
            "site_url": "http://localhost",
            "list_name": "webhook integration",
            "source_type": "manual_csv",
            "criteria": {"csv_path": str(FIXTURE_CSV.relative_to(ROOT))},
            "notes": "",
            "webhook_url": f"http://127.0.0.1:{self.receiver.port}/wp-json/corexbiz/v1/sales/run-webhook",
        }
        from src.db import repository as repo

        with self.pool.connection() as conn:
            with conn.transaction():
                repo.insert_queued_run(
                    conn,
                    run_id=self.run_id,
                    site_id=self.site_id,
                    site_url="http://localhost",
                    list_name="webhook integration",
                    source_type="manual_csv",
                    criteria=self.run_spec["criteria"],
                    webhook_url=self.run_spec["webhook_url"],
                )

    def tearDown(self) -> None:
        from src.db.pool import close_pool

        self.receiver.stop()
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    "DELETE FROM qualified_leads WHERE site_id = %s",
                    (self.site_id,),
                )
                conn.execute("DELETE FROM leads WHERE site_id = %s", (self.site_id,))
                conn.execute("DELETE FROM runs WHERE id = %s", (self.run_id,))
        close_pool()

    def test_execute_run_delivers_webhook(self) -> None:
        from src.db import repository as repo
        from src.worker.run_job import execute_run

        with mock_webhook_secret(self.receiver.secret):
            summary = execute_run(self.run_spec)

        self.assertEqual(summary["status"], "completed")
        self.assertIsNotNone(self.receiver.received)
        assert self.receiver.received is not None
        self.assertEqual(self.receiver.received["event"], "run.completed")
        self.assertEqual(self.receiver.received["run_id"], str(self.run_id))
        self.assertGreaterEqual(self.receiver.received["qualified_count"], 1)

        with self.pool.connection() as conn:
            run = repo.get_run(conn, self.run_id)
        self.assertIsNotNone(run["webhook_sent_at"])


class mock_webhook_secret:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self._prev = os.environ.get("WEBHOOK_SIGNING_SECRET")

    def __enter__(self):
        os.environ["WEBHOOK_SIGNING_SECRET"] = self.secret
        return self

    def __exit__(self, *args):
        if self._prev is None:
            os.environ.pop("WEBHOOK_SIGNING_SECRET", None)
        else:
            os.environ["WEBHOOK_SIGNING_SECRET"] = self._prev


if __name__ == "__main__":
    unittest.main()
