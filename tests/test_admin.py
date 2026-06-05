"""Admin UI auth, session, and log endpoints."""

from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient

ADMIN_PASSWORD = "test-admin-password"


class TestAdminAuth(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
        os.environ["ADMIN_SESSION_SECRET"] = "test-admin-secret"
        os.environ.pop("ADMIN_AUTH_DISABLED", None)
        from src.admin.log_buffer import clear_log_buffer_for_tests
        from src.api.main import create_app

        clear_log_buffer_for_tests()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        os.environ.pop("ADMIN_PASSWORD", None)
        os.environ.pop("ADMIN_SESSION_SECRET", None)

    def test_admin_index_served(self) -> None:
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn("CoreX Sales Service", response.text)

    def test_logs_requires_auth(self) -> None:
        response = self.client.get("/admin/logs")
        self.assertEqual(response.status_code, 401)

    def test_login_and_logs(self) -> None:
        login = self.client.post("/admin/api/login", json={"password": ADMIN_PASSWORD})
        self.assertEqual(login.status_code, 200)
        cookie = login.cookies.get("cbz_sales_admin")
        self.assertTrue(cookie)

        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        rid = health.headers.get("X-Request-Id")
        self.assertTrue(rid)

        logs = self.client.get("/admin/logs", cookies={"cbz_sales_admin": cookie})
        self.assertEqual(logs.status_code, 200)
        body = logs.json()
        self.assertEqual(body["source"], "process")
        self.assertTrue(any(rid in row.get("message", "") or row.get("request_id") == rid for row in body["logs"]))

    def test_run_post_appears_in_access_logs(self) -> None:
        os.environ["API_TOKEN"] = "test-api-token"
        os.environ["SALES_WORKER_MODE"] = "disabled"
        os.environ.setdefault("GOOGLE_MAPS_API_KEY", "test-google-maps-key")

        from src.admin.log_buffer import clear_log_buffer_for_tests
        from src.api.main import create_app

        clear_log_buffer_for_tests()
        client = TestClient(create_app())

        login = client.post("/admin/api/login", json={"password": ADMIN_PASSWORD})
        self.assertEqual(login.status_code, 200)
        cookie = login.cookies.get("cbz_sales_admin")
        self.assertTrue(cookie)

        response = client.post(
            "/api/v1/runs",
            headers={"Authorization": "Bearer test-api-token"},
            json={
                "list_name": "log test",
                "source_type": "google_maps",
                "criteria": {},
            },
        )
        self.assertEqual(response.status_code, 202)

        logs = client.get("/admin/logs", cookies={"cbz_sales_admin": cookie})
        self.assertEqual(logs.status_code, 200)
        rows = logs.json().get("logs") or []
        run_lines = [
            row
            for row in rows
            if "/api/v1/runs" in row.get("message", "")
            and ("http_accept" in row.get("message", "") or "http_respon" in row.get("message", ""))
        ]
        self.assertGreaterEqual(len(run_lines), 2)
        self.assertTrue(any("[202]" in row.get("message", "") for row in run_lines))
        self.assertTrue(any("run_id" in row.get("message", "") for row in run_lines))

        from src.worker import run_registry

        run_registry.clear_runs()
        os.environ.pop("API_TOKEN", None)
        os.environ.pop("SALES_WORKER_MODE", None)

    def test_invalid_password(self) -> None:
        response = self.client.post("/admin/api/login", json={"password": "wrong"})
        self.assertEqual(response.status_code, 401)

    def test_session_endpoint(self) -> None:
        response = self.client.get("/admin/api/session")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["authRequired"])
        self.assertFalse(data["authenticated"])


class TestAdminSession(unittest.TestCase):
    def test_create_and_verify(self) -> None:
        from src.admin.session import create_admin_session, verify_admin_session

        token = create_admin_session("secret", ttl_sec=3600)
        self.assertTrue(verify_admin_session(token, "secret"))
        self.assertFalse(verify_admin_session(token, "wrong"))


if __name__ == "__main__":
    unittest.main()
