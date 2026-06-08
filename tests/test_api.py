"""FastAPI route tests."""

from __future__ import annotations

import os
import time
import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient

TEST_API_TOKEN = "test-api-token"


def _db_configured() -> bool:
    from src.db.connection import resolve_database_url

    return bool(resolve_database_url()) and os.getenv("SKIP_POSTGRES_TESTS") != "1"


AUTH_HEADERS = {
    "Authorization": f"Bearer {TEST_API_TOKEN}",
}


class TestApiAuth(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["API_TOKEN"] = TEST_API_TOKEN
        from src.api.main import create_app
        from src.db.pool import close_pool

        close_pool()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        from src.db.pool import close_pool

        close_pool()

    @unittest.skipUnless(_db_configured(), "DATABASE_URL not configured")
    def test_create_run_requires_token(self) -> None:
        response = self.client.post(
            "/api/v1/runs",
            json={"source_type": "google_maps", "criteria": {}},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["message"], "missing token")


class TestApiRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _db_configured():
            raise unittest.SkipTest("DATABASE_URL not configured")

        os.environ["SALES_DISABLE_DOTENV"] = "1"
        os.environ["API_TOKEN"] = TEST_API_TOKEN
        os.environ["SALES_WORKER_MODE"] = "disabled"
        os.environ.setdefault("GOOGLE_MAPS_API_KEY", "test-google-maps-key")
        from src.api.main import create_app
        from src.db.migrate import run_migrations
        from src.db.pool import close_pool, get_pool

        close_pool()
        run_migrations()
        cls.client = TestClient(create_app())
        cls.pool = get_pool()
        cls.site_id = "dev-server"

    def setUp(self) -> None:
        from src.worker import job_queue

        job_queue.clear_queue()
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    "DELETE FROM runs WHERE site_id IN (%s, %s, %s, %s, %s)",
                    (
                        "dev-server",
                        "site-a",
                        "site-b",
                        "job-queue-test",
                        "enqueue-test",
                    ),
                )

    @classmethod
    def tearDownClass(cls) -> None:
        from src.db.pool import close_pool

        close_pool()

    def test_health_ok(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["database"]["schema"], "sales-service")

    def test_create_run_returns_202(self) -> None:
        response = self.client.post(
            "/api/v1/runs",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            json={
                "list_name": "API test run",
                "source_type": "google_maps",
                "criteria": {"cities_file": "cities.csv"},
                "notes": "phase 2 test",
            },
        )
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertTrue(body["started"])
        run_id = body["run_id"]

        get_resp = self.client.get(
            f"/api/v1/runs/{run_id}",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            params={"site_id": "dev-server"},
        )
        self.assertEqual(get_resp.status_code, 200)
        run = get_resp.json()
        self.assertEqual(run["id"], run_id)
        self.assertEqual(run["status"], "queued")
        self.assertTrue(run["running"])

        self._cleanup_run(uuid.UUID(str(run_id)))

    def test_get_active_run_idle_when_no_run(self) -> None:
        response = self.client.get(
            "/api/v1/runs/active",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            params={"site_id": self.site_id},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["running"])
        self.assertEqual(body["status"], "idle")
        self.assertIsNone(body["run_id"])

    def test_get_active_run_reports_queued_run(self) -> None:
        create = self.client.post(
            "/api/v1/runs",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            json={
                "list_name": "active poll test",
                "source_type": "google_maps",
                "criteria": {"cities_file": "cities.csv"},
            },
        )
        self.assertEqual(create.status_code, 202)
        run_id = create.json()["run_id"]

        active = self.client.get(
            "/api/v1/runs/active",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            params={"site_id": self.site_id},
        )
        self.assertEqual(active.status_code, 200)
        body = active.json()
        self.assertTrue(body["running"])
        self.assertEqual(body["run_id"], run_id)
        self.assertEqual(body["status"], "queued")

        self._cleanup_run(uuid.UUID(str(run_id)))

    def test_get_active_run_idle_for_other_site(self) -> None:
        create = self.client.post(
            "/api/v1/runs",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            json={
                "site_id": "site-a",
                "list_name": "tenant isolation",
                "source_type": "google_maps",
                "criteria": {"cities_file": "cities.csv"},
            },
        )
        self.assertEqual(create.status_code, 202)
        run_id = create.json()["run_id"]

        other_site = self.client.get(
            "/api/v1/runs/active",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            params={"site_id": "site-b"},
        )
        self.assertEqual(other_site.status_code, 200)
        other_body = other_site.json()
        self.assertFalse(other_body["running"])
        self.assertEqual(other_body["status"], "idle")
        self.assertIsNone(other_body["run_id"])

        same_site = self.client.get(
            "/api/v1/runs/active",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            params={"site_id": "site-a"},
        )
        self.assertEqual(same_site.status_code, 200)
        same_body = same_site.json()
        self.assertTrue(same_body["running"])
        self.assertEqual(same_body["run_id"], run_id)

        self._cleanup_run(uuid.UUID(str(run_id)))

    def test_second_run_queues_without_overwriting_first(self) -> None:
        payload = {
            "list_name": "concurrency test",
            "source_type": "google_maps",
            "criteria": {"cities_file": "cities.csv"},
        }
        first = self.client.post(
            "/api/v1/runs",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            json=payload,
        )
        second = self.client.post(
            "/api/v1/runs",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            json={**payload, "list_name": "concurrency test 2"},
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["queue_position"], 1)
        self.assertEqual(second.json()["queue_position"], 2)
        self._cleanup_run(uuid.UUID(str(first.json()["run_id"])))
        self._cleanup_run(uuid.UUID(str(second.json()["run_id"])))

    def test_create_run_rejects_invalid_source_type(self) -> None:
        response = self.client.post(
            "/api/v1/runs",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            json={"source_type": "invalid_source", "criteria": {}},
        )
        self.assertEqual(response.status_code, 400)

    def test_create_google_maps_run_requires_api_key(self) -> None:
        prev = os.environ.pop("GOOGLE_MAPS_API_KEY", None)
        try:
            from src.api.main import create_app

            client = TestClient(create_app())
            response = client.post(
                "/api/v1/runs",
                headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
                json={"source_type": "google_maps", "criteria": {}},
            )
            self.assertEqual(response.status_code, 503)
            detail = response.json()["detail"]
            self.assertEqual(detail["error"], "google_maps_not_configured")
            self.assertIn("GOOGLE_MAPS_API_KEY", detail["message"])
        finally:
            if prev is not None:
                os.environ["GOOGLE_MAPS_API_KEY"] = prev
            else:
                os.environ.setdefault("GOOGLE_MAPS_API_KEY", "test-google-maps-key")

    def test_leads_bundle_and_patch(self) -> None:
        import db as dbmod
        from src.db import repository as repo

        run_id = uuid.uuid4()
        with self.pool.connection() as conn:
            with conn.transaction():
                repo.persist_run_result(
                    conn,
                    run_id=run_id,
                    site_id=self.site_id,
                    site_url="http://localhost",
                    list_name="bundle test",
                    source_type="google_maps",
                    criteria={},
                    status="completed",
                    message="bundle test",
                )
                qrow = {c: "" for c in dbmod.QUALIFIED_ROW_COLUMNS}
                qrow["place_id"] = f"api-{uuid.uuid4().hex[:8]}"
                qrow["website"] = f"https://{uuid.uuid4().hex[:8]}.example.com"
                qrow["normalized_url"] = qrow["website"] + "/"
                qrow["business_name"] = "API Test Shop"
                qrow["fit_segment"] = "Strong Fit"
                repo.upsert_qualified_lead(
                    conn, qrow, run_id=run_id, site_id=self.site_id
                )

        bundle_resp = self.client.get(
            f"/api/v1/sites/{self.site_id}/leads-bundle",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
        )
        self.assertEqual(bundle_resp.status_code, 200)
        bundle = bundle_resp.json()
        self.assertGreaterEqual(bundle["total_qualified"], 1)
        self.assertGreaterEqual(len(bundle["qualified_leads"]), 1)

        lead_id = bundle["qualified_leads"][0]["id"]
        patch_resp = self.client.patch(
            f"/api/v1/qualified-leads/{lead_id}",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            params={"site_id": self.site_id},
            json={"review_status": "approved", "notes": "looks good"},
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.json()["review_status"], "approved")

        run_leads_resp = self.client.get(
            f"/api/v1/runs/{run_id}/leads",
            headers={"Authorization": f"Bearer {TEST_API_TOKEN}"},
            params={"site_id": self.site_id},
        )
        self.assertEqual(run_leads_resp.status_code, 200)
        self.assertGreaterEqual(run_leads_resp.json()["total"], 1)

        self._cleanup_run(run_id)

    def _cleanup_run(self, run_id: uuid.UUID) -> None:
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    "DELETE FROM qualified_leads WHERE run_id = %s",
                    (run_id,),
                )
                conn.execute("DELETE FROM leads WHERE run_id = %s", (run_id,))
                conn.execute("DELETE FROM runs WHERE id = %s", (run_id,))


if __name__ == "__main__":
    unittest.main()
