"""Tests for in-memory run registry."""

from __future__ import annotations

import unittest
import uuid

from src.worker import run_registry


class TestRunRegistry(unittest.TestCase):
    def setUp(self) -> None:
        run_registry.clear_runs()

    def tearDown(self) -> None:
        run_registry.clear_runs()

    def test_register_and_get_active(self) -> None:
        run_id = uuid.uuid4()
        site_id = "site-a"
        run_registry.register_run(
            {
                "id": str(run_id),
                "site_id": site_id,
                "source_type": "google_maps",
                "criteria": {},
            }
        )
        active = run_registry.get_active_run_for_site(site_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["status"], "queued")
        self.assertEqual(active["id"], str(run_id))

    def test_running_then_remove(self) -> None:
        run_id = uuid.uuid4()
        run_registry.register_run(
            {"id": str(run_id), "site_id": "site-a", "source_type": "google_maps", "criteria": {}}
        )
        run_registry.mark_run_running(run_id, message="working")
        row = run_registry.get_run(run_id)
        assert row is not None
        self.assertEqual(row["status"], "running")
        self.assertTrue(row["started_at"] is not None)
        run_registry.remove_run(run_id)
        self.assertIsNone(run_registry.get_run(run_id))


if __name__ == "__main__":
    unittest.main()
