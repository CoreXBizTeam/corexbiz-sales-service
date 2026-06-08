"""Tests for Postgres-backed job queue worker polling."""

from __future__ import annotations

import os
import threading
import unittest
import uuid

from src.db import repository as repo


def _db_configured() -> bool:
    from src.db.connection import resolve_database_url

    return bool(resolve_database_url()) and os.getenv("SKIP_POSTGRES_TESTS") != "1"


class TestJobQueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _db_configured():
            raise unittest.SkipTest("DATABASE_URL not configured")

        from src.db.migrate import run_migrations
        from src.db.pool import close_pool, get_pool

        close_pool()
        run_migrations()
        cls.pool = get_pool()
        from src.worker import job_queue

        job_queue.clear_queue()

    def setUp(self) -> None:
        from src.worker import job_queue

        job_queue.clear_queue()

    def tearDown(self) -> None:
        from src.worker import job_queue

        job_queue.clear_queue()

    def test_queue_position_after_insert(self) -> None:
        from src.worker import job_queue

        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        with self.pool.connection() as conn:
            with conn.transaction():
                repo.insert_queued_run(
                    conn,
                    run_id=first_id,
                    site_id="job-queue-test",
                    site_url="http://localhost",
                    list_name="one",
                    source_type="google_maps",
                    criteria={},
                )
                repo.insert_queued_run(
                    conn,
                    run_id=second_id,
                    site_id="job-queue-test",
                    site_url="http://localhost",
                    list_name="two",
                    source_type="google_maps",
                    criteria={},
                )
        self.assertEqual(job_queue.queue_position(first_id), 1)
        self.assertGreaterEqual(job_queue.queue_position(second_id), 2)
        self.assertGreaterEqual(job_queue.pending_count(), 2)

    def test_take_job_claims_fifo(self) -> None:
        from src.worker import job_queue

        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        with self.pool.connection() as conn:
            with conn.transaction():
                repo.insert_queued_run(
                    conn,
                    run_id=first_id,
                    site_id="job-queue-test",
                    site_url="http://localhost",
                    list_name="one",
                    source_type="google_maps",
                    criteria={},
                )
                repo.insert_queued_run(
                    conn,
                    run_id=second_id,
                    site_id="job-queue-test",
                    site_url="http://localhost",
                    list_name="two",
                    source_type="google_maps",
                    criteria={},
                )
        first = job_queue.take_job(timeout=2.0)
        second = job_queue.take_job(timeout=2.0)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None
        assert second is not None
        self.assertEqual(first["id"], str(first_id))
        self.assertEqual(second["id"], str(second_id))
        self.assertEqual(first["status"], "running")
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM runs WHERE id IN (%s, %s)", (first_id, second_id))

    def test_worker_waits_for_job(self) -> None:
        from src.worker import job_queue

        run_id = uuid.uuid4()
        taken: list[dict] = []

        def worker() -> None:
            item = job_queue.take_job(timeout=3.0)
            if item:
                taken.append(item)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        import time

        time.sleep(0.2)
        with self.pool.connection() as conn:
            with conn.transaction():
                repo.insert_queued_run(
                    conn,
                    run_id=run_id,
                    site_id="job-queue-test",
                    site_url="http://localhost",
                    list_name="wait",
                    source_type="google_maps",
                    criteria={},
                )
        thread.join(timeout=3.0)
        self.assertEqual(len(taken), 1)
        self.assertEqual(taken[0]["id"], str(run_id))
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM runs WHERE id = %s", (run_id,))

    def test_claim_run_by_id(self) -> None:
        run_id = uuid.uuid4()
        with self.pool.connection() as conn:
            with conn.transaction():
                repo.insert_queued_run(
                    conn,
                    run_id=run_id,
                    site_id="job-queue-test",
                    site_url="http://localhost",
                    list_name="claim-by-id",
                    source_type="manual_csv",
                    criteria={},
                )
                row = repo.claim_run_by_id(conn, run_id)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(str(row["id"]), str(run_id))
        self.assertEqual(row["status"], "running")
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM runs WHERE id = %s", (run_id,))


if __name__ == "__main__":
    unittest.main()
