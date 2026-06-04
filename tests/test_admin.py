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
