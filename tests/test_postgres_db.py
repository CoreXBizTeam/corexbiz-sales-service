"""Postgres repository tests (mirrors tests/test_db.py)."""

from __future__ import annotations

import os
import unittest
import uuid


class TestPostgresRepository(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.db.connection import resolve_database_url
        from src.db.migrate import run_migrations
        from src.db.pool import close_pool, get_pool

        if os.getenv("SKIP_POSTGRES_TESTS") == "1":
            raise unittest.SkipTest("SKIP_POSTGRES_TESTS=1")

        if not resolve_database_url():
            raise unittest.SkipTest("DATABASE_URL not configured — skipping Postgres tests")

        close_pool()
        run_migrations()
        cls.pool = get_pool()

    @classmethod
    def tearDownClass(cls) -> None:
        from src.db.pool import close_pool

        close_pool()

    def setUp(self) -> None:
        import db as dbmod

        self.dbmod = dbmod
        self._conn_cm = self.pool.connection()
        self.conn = self._conn_cm.__enter__()
        self._run_id = uuid.uuid4()
        self._site_id = f"test-site-{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            """
            INSERT INTO runs (id, site_id, source_type, status)
            VALUES (%s, %s, 'google_maps', 'completed')
            """,
            (self._run_id, self._site_id),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        with self.conn.transaction():
            self.conn.execute("DELETE FROM qualified_leads WHERE site_id = %s", (self._site_id,))
            self.conn.execute("DELETE FROM leads WHERE site_id = %s", (self._site_id,))
            self.conn.execute("DELETE FROM exports WHERE site_id = %s", (self._site_id,))
            self.conn.execute("DELETE FROM runs WHERE id = %s", (self._run_id,))
        self._conn_cm.__exit__(None, None, None)

    def test_upsert_lead_dedupes_place_id(self) -> None:
        from src.db import repository as repo

        row = {c: "" for c in self.dbmod.LEADS_COLUMN_NAMES}
        row["place_id"] = f"ChIJ-{uuid.uuid4().hex[:6]}"
        row["business_name"] = "A"
        repo.upsert_lead(self.conn, row, run_id=self._run_id, site_id=self._site_id)
        row["business_name"] = "B"
        repo.upsert_lead(self.conn, row, run_id=self._run_id, site_id=self._site_id)
        self.conn.commit()

        cur = self.conn.execute(
            "SELECT COUNT(*) FROM leads WHERE place_id = %s",
            (row["place_id"],),
        )
        self.assertEqual(cur.fetchone()[0], 1)
        cur = self.conn.execute(
            "SELECT business_name FROM leads WHERE place_id = %s",
            (row["place_id"],),
        )
        self.assertEqual(cur.fetchone()[0], "A")

    def test_upsert_qualified_links_lead_id(self) -> None:
        from src.db import repository as repo

        place_id = f"ChIJ-{uuid.uuid4().hex[:6]}"
        lead = {c: "" for c in self.dbmod.LEADS_COLUMN_NAMES}
        lead["place_id"] = place_id
        lead["business_name"] = "Shop"
        repo.upsert_lead(self.conn, lead, run_id=self._run_id, site_id=self._site_id)
        self.conn.commit()

        cur = self.conn.execute("SELECT id FROM leads WHERE place_id = %s", (place_id,))
        lid = int(cur.fetchone()[0])

        qrow = {c: "" for c in self.dbmod.QUALIFIED_ROW_COLUMNS}
        qrow["place_id"] = place_id
        qrow["website"] = "https://example.com"
        qrow["normalized_url"] = "https://example.com/"
        qrow["business_name"] = "Shop"
        qrow["fit_segment"] = "Strong Fit: WP without visible upload"
        qrow["city"] = "Vancouver"
        qrow["province"] = "BC"
        repo.upsert_qualified_lead(
            self.conn, qrow, run_id=self._run_id, site_id=self._site_id
        )
        self.conn.commit()

        cur = self.conn.execute(
            "SELECT lead_id, fit_tier, review_status FROM qualified_leads WHERE site_id = %s LIMIT 1",
            (self._site_id,),
        )
        r = cur.fetchone()
        self.assertEqual(r[0], lid)
        self.assertEqual(r[1], "Tier 1 (Ideal)")
        self.assertEqual(r[2], self.dbmod.REVIEW_STATUS_PENDING)

    def test_log_export_and_get_all_leads(self) -> None:
        from src.db import repository as repo

        row = {c: "" for c in self.dbmod.LEADS_COLUMN_NAMES}
        row["place_id"] = f"ChIJ-{uuid.uuid4().hex[:6]}"
        row["city"] = "X"
        repo.upsert_lead(self.conn, row, run_id=self._run_id, site_id=self._site_id)
        repo.log_export(
            self.conn,
            42,
            "/tmp/out.csv",
            "test",
            run_id=self._run_id,
            site_id=self._site_id,
        )
        self.conn.commit()

        rows = repo.get_all_leads(self.conn)
        self.assertGreaterEqual(len(rows), 1)
        exports = repo.get_recent_exports(self.conn, limit=1)
        self.assertEqual(exports[0]["row_count"], 42)


if __name__ == "__main__":
    unittest.main()
