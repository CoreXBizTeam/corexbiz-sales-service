"""Tests for Postgres-backed run queue (runs table)."""

from __future__ import annotations

import os
import unittest
import uuid

from src.db import repository as repo


def _db_configured() -> bool:
    from src.db.connection import resolve_database_url

    return bool(resolve_database_url()) and os.getenv("SKIP_POSTGRES_TESTS") != "1"


def _insert_queued(conn, run_id: uuid.UUID, site_id: str) -> None:
    repo.insert_queued_run(
        conn,
        run_id=run_id,
        site_id=site_id,
        site_url="http://localhost",
        list_name="queue test",
        source_type="google_maps",
        criteria={},
    )


class TestDbRunQueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _db_configured():
            raise unittest.SkipTest("DATABASE_URL not configured")

        from src.db.migrate import run_migrations
        from src.db.pool import close_pool, get_pool
        from src.worker import job_queue

        close_pool()
        run_migrations()
        cls.pool = get_pool()
        job_queue.clear_queue()

    def setUp(self) -> None:
        from src.worker import job_queue

        job_queue.clear_queue()

    def tearDown(self) -> None:
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM runs WHERE site_id LIKE 'queue-test-%'")
                conn.execute("DELETE FROM runs WHERE status IN ('queued', 'running')")

    def test_queue_position_fifo(self) -> None:
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        with self.pool.connection() as conn:
            with conn.transaction():
                _insert_queued(conn, first_id, "queue-test-a")
                _insert_queued(conn, second_id, "queue-test-a")
                self.assertEqual(repo.queue_position_for_run(conn, first_id), 1)
                pos_second = repo.queue_position_for_run(conn, second_id)
        self.assertGreaterEqual(pos_second, 2)

    def test_claim_next_queued_run_fifo(self) -> None:
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        with self.pool.connection() as conn:
            with conn.transaction():
                _insert_queued(conn, first_id, "queue-test-b")
                _insert_queued(conn, second_id, "queue-test-b")
            with conn.transaction():
                claimed = repo.claim_next_queued_run(conn)
            self.assertIsNotNone(claimed)
            assert claimed is not None
            self.assertEqual(str(claimed["id"]), str(first_id))
            self.assertEqual(claimed["status"], "running")

    def test_get_in_progress_for_site_no_cross_leak(self) -> None:
        run_a = uuid.uuid4()
        run_b = uuid.uuid4()
        with self.pool.connection() as conn:
            with conn.transaction():
                _insert_queued(conn, run_a, "queue-test-site-a")
                _insert_queued(conn, run_b, "queue-test-site-b")
                repo.mark_run_running(conn, run_a, message="site-a running")
                active_a = repo.get_in_progress_run_for_site(conn, "queue-test-site-a")
                active_b = repo.get_in_progress_run_for_site(conn, "queue-test-site-b")
                idle_c = repo.get_in_progress_run_for_site(conn, "queue-test-site-c")
        self.assertIsNotNone(active_a)
        self.assertIsNotNone(active_b)
        assert active_a is not None
        assert active_b is not None
        self.assertEqual(str(active_a["id"]), str(run_a))
        self.assertEqual(active_a["status"], "running")
        self.assertEqual(str(active_b["id"]), str(run_b))
        self.assertEqual(active_b["status"], "queued")
        self.assertIsNone(idle_c)


if __name__ == "__main__":
    unittest.main()
