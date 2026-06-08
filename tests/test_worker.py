"""Worker pipeline and SQLite sync tests."""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_CSV = ROOT / "tests" / "fixtures" / "worker_sample.csv"


def _db_configured() -> bool:
    from src.db.connection import resolve_database_url

    return bool(resolve_database_url()) and os.getenv("SKIP_POSTGRES_TESTS") != "1"


def _run_spec(
    run_id: uuid.UUID,
    site_id: str,
    *,
    source_type: str = "manual_csv",
    criteria: dict | None = None,
    webhook_url: str | None = None,
) -> dict:
    return {
        "id": str(run_id),
        "site_id": site_id,
        "site_url": "http://localhost",
        "list_name": "worker test",
        "source_type": source_type,
        "criteria": criteria or {},
        "notes": "",
        "webhook_url": webhook_url,
    }


class TestSyncSqlite(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _db_configured():
            raise unittest.SkipTest("DATABASE_URL not configured")

        from src.db.migrate import run_migrations
        from src.db.pool import close_pool

        close_pool()
        run_migrations()

    def setUp(self) -> None:
        from src.db.pool import close_pool, get_pool

        close_pool()
        self.pool = get_pool()
        self.run_id = uuid.uuid4()
        self.site_id = f"worker-sync-{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        from src.db.pool import close_pool

        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    "DELETE FROM qualified_leads WHERE site_id = %s",
                    (self.site_id,),
                )
                conn.execute("DELETE FROM leads WHERE site_id = %s", (self.site_id,))
                conn.execute("DELETE FROM runs WHERE id = %s", (self.run_id,))
        close_pool()

    def test_sync_sqlite_to_postgres(self) -> None:
        import db as dbmod
        from src.db import repository as repo
        from src.worker.sync_sqlite import init_empty_sqlite, sync_sqlite_to_postgres

        with tempfile.TemporaryDirectory() as tmp:
            sqlite_path = Path(tmp) / "test.db"
            init_empty_sqlite(sqlite_path)
            sqlite_conn = dbmod.get_connection(str(sqlite_path))
            try:
                lead = {c: "" for c in dbmod.LEADS_COLUMN_NAMES}
                lead["place_id"] = f"sync-{uuid.uuid4().hex[:6]}"
                lead["business_name"] = "Sync Co"
                lead["website"] = "https://example.com"
                dbmod.upsert_lead(sqlite_conn, lead)

                qrow = {c: "" for c in dbmod.QUALIFIED_ROW_COLUMNS}
                qrow["place_id"] = lead["place_id"]
                qrow["website"] = "https://example.com"
                qrow["normalized_url"] = "https://example.com/"
                qrow["business_name"] = "Sync Co"
                dbmod.upsert_qualified_lead(sqlite_conn, qrow)
            finally:
                sqlite_conn.close()

            with self.pool.connection() as conn:
                with conn.transaction():
                    repo.persist_run_result(
                        conn,
                        run_id=self.run_id,
                        site_id=self.site_id,
                        site_url="http://localhost",
                        list_name="sync test",
                        source_type="manual_csv",
                        criteria={},
                        status="completed",
                        message="sync test",
                    )
                    counts = sync_sqlite_to_postgres(
                        conn,
                        sqlite_path,
                        run_id=self.run_id,
                        site_id=self.site_id,
                    )

        self.assertEqual(counts["leads"], 1)
        self.assertEqual(counts["qualified_leads"], 1)

        with self.pool.connection() as conn:
            rows = repo.list_qualified_for_run(
                conn, self.run_id, self.site_id, page=1, per_page=10
            )[0]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["business_name"], "Sync Co")

    def test_sync_preserves_review_status(self) -> None:
        import db as dbmod
        from src.db import repository as repo
        from src.worker.sync_sqlite import init_empty_sqlite, sync_sqlite_to_postgres

        with tempfile.TemporaryDirectory() as tmp:
            sqlite_path = Path(tmp) / "review.db"
            init_empty_sqlite(sqlite_path)
            sqlite_conn = dbmod.get_connection(str(sqlite_path))
            try:
                lead = {c: "" for c in dbmod.LEADS_COLUMN_NAMES}
                lead["place_id"] = f"review-{uuid.uuid4().hex[:6]}"
                lead["business_name"] = "Review Co"
                lead["website"] = "https://review.example.com"
                dbmod.upsert_lead(sqlite_conn, lead)

                qrow = {c: "" for c in dbmod.QUALIFIED_ROW_COLUMNS}
                qrow["place_id"] = lead["place_id"]
                qrow["website"] = "https://review.example.com"
                qrow["normalized_url"] = "https://review.example.com/"
                qrow["business_name"] = "Review Co"
                qrow["review_status"] = dbmod.REVIEW_STATUS_APPROVED
                qrow["notes"] = "legacy note"
                dbmod.upsert_qualified_lead(sqlite_conn, qrow)
            finally:
                sqlite_conn.close()

            with self.pool.connection() as conn:
                with conn.transaction():
                    repo.persist_run_result(
                        conn,
                        run_id=self.run_id,
                        site_id=self.site_id,
                        site_url="http://localhost",
                        list_name="review status test",
                        source_type="manual_csv",
                        criteria={},
                        status="completed",
                        message="review status test",
                    )
                    sync_sqlite_to_postgres(
                        conn,
                        sqlite_path,
                        run_id=self.run_id,
                        site_id=self.site_id,
                    )

        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT review_status, notes FROM qualified_leads WHERE run_id = %s LIMIT 1",
                (self.run_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], dbmod.REVIEW_STATUS_APPROVED)
        self.assertEqual(row[1], "legacy note")


class TestExecuteRun(unittest.TestCase):
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
        from src.db import repository as repo
        from src.db.pool import close_pool, get_pool

        close_pool()
        self.pool = get_pool()
        self.run_id = uuid.uuid4()
        self.site_id = f"worker-exec-{uuid.uuid4().hex[:8]}"
        self.run_spec = _run_spec(
            self.run_id,
            self.site_id,
            criteria={"csv_path": str(FIXTURE_CSV.relative_to(ROOT))},
        )
        with self.pool.connection() as conn:
            with conn.transaction():
                repo.insert_queued_run(
                    conn,
                    run_id=self.run_id,
                    site_id=self.site_id,
                    site_url="http://localhost",
                    list_name="worker test",
                    source_type="manual_csv",
                    criteria=self.run_spec["criteria"],
                    webhook_url=self.run_spec.get("webhook_url"),
                )

    def tearDown(self) -> None:
        from src.db.pool import close_pool

        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    "DELETE FROM qualified_leads WHERE site_id = %s",
                    (self.site_id,),
                )
                conn.execute("DELETE FROM leads WHERE site_id = %s", (self.site_id,))
                conn.execute("DELETE FROM runs WHERE id = %s", (self.run_id,))
        close_pool()

    def test_execute_run_manual_csv_completes(self) -> None:
        from src.db import repository as repo
        from src.worker.run_job import execute_run

        summary = execute_run(self.run_spec)
        self.assertEqual(summary["status"], "completed")
        self.assertGreaterEqual(summary["qualified_leads"], 1)

        with self.pool.connection() as conn:
            run = repo.get_run(conn, self.run_id)
        self.assertEqual(run["status"], "completed")
        self.assertIsNotNone(run["finished_at"])

    def test_execute_run_rejects_duplicate_persist(self) -> None:
        from src.worker.run_job import execute_run

        execute_run(self.run_spec)
        with self.assertRaises(RuntimeError):
            execute_run(self.run_spec)

        with self.pool.connection() as conn:
            from src.db import repository as repo

            run = repo.get_run(conn, self.run_id)
        self.assertEqual(run["status"], "completed")


class TestEnqueuePool(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _db_configured():
            raise unittest.SkipTest("DATABASE_URL not configured")

        from src.db.migrate import run_migrations
        from src.db.pool import close_pool, get_pool

        close_pool()
        run_migrations()
        cls.pool = get_pool()

    def tearDown(self) -> None:
        from src.worker import job_queue

        job_queue.clear_queue()

    def test_pool_dispatches_without_executing_inline(self) -> None:
        from src.db import repository as repo
        from src.worker import enqueue as enqueue_mod

        run_id = uuid.uuid4()
        run = _run_spec(run_id, "enqueue-test")
        with self.pool.connection() as conn:
            with conn.transaction():
                repo.insert_queued_run(
                    conn,
                    run_id=run_id,
                    site_id="enqueue-test",
                    site_url="http://localhost",
                    list_name="enqueue",
                    source_type="manual_csv",
                    criteria={},
                )
        with mock.patch("src.worker.worker_pool.ensure_worker_pool_started"):
            with mock.patch.dict(os.environ, {"SALES_WORKER_MODE": "pool"}, clear=False):
                enqueue_mod.dispatch_run(run)
        from src.worker import job_queue

        self.assertGreaterEqual(job_queue.pending_count(), 1)
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM runs WHERE id = %s", (run_id,))


if __name__ == "__main__":
    unittest.main()
