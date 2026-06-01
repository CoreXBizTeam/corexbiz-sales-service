"""SQLite persistence (db.py) tests."""

from __future__ import annotations

import os
import tempfile
import unittest


class TestDb(unittest.TestCase):
    def setUp(self) -> None:
        import db as dbmod

        self.dbmod = dbmod
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name

    def tearDown(self) -> None:
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_init_db_creates_three_tables(self) -> None:
        self.dbmod.init_db(self.db_path)
        conn = self.dbmod.get_connection(self.db_path)
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            names = {r[0] for r in cur.fetchall()}
            self.assertTrue(
                {"exports", "leads", "qualified_leads", "tracker_rows"}.issubset(names)
            )
        finally:
            conn.close()

    def test_upsert_lead_dedupes_place_id(self) -> None:
        self.dbmod.init_db(self.db_path)
        conn = self.dbmod.get_connection(self.db_path)
        try:
            row = {c: "" for c in self.dbmod.LEADS_COLUMN_NAMES}
            row["place_id"] = "ChIJ1"
            row["business_name"] = "A"
            self.dbmod.upsert_lead(conn, row)
            row["business_name"] = "B"
            self.dbmod.upsert_lead(conn, row)
            cur = conn.execute("SELECT COUNT(*) FROM leads WHERE place_id = ?", ("ChIJ1",))
            self.assertEqual(cur.fetchone()[0], 1)
            cur = conn.execute("SELECT business_name FROM leads WHERE place_id = ?", ("ChIJ1",))
            self.assertEqual(cur.fetchone()[0], "A")
        finally:
            conn.close()

    def test_upsert_qualified_links_lead_id(self) -> None:
        self.dbmod.init_db(self.db_path)
        conn = self.dbmod.get_connection(self.db_path)
        try:
            lead = {c: "" for c in self.dbmod.LEADS_COLUMN_NAMES}
            lead["place_id"] = "ChIJ2"
            lead["business_name"] = "Shop"
            self.dbmod.upsert_lead(conn, lead)
            cur = conn.execute("SELECT id FROM leads WHERE place_id = ?", ("ChIJ2",))
            lid = int(cur.fetchone()[0])

            qrow = {c: "" for c in self.dbmod.QUALIFIED_ROW_COLUMNS}
            qrow["place_id"] = "ChIJ2"
            qrow["website"] = "https://example.com"
            qrow["normalized_url"] = "https://example.com/"
            qrow["business_name"] = "Shop"
            qrow["fit_segment"] = "Strong Fit: WP without visible upload"
            qrow["city"] = "Vancouver"
            qrow["province"] = "BC"
            self.dbmod.upsert_qualified_lead(conn, qrow)

            cur = conn.execute(
                "SELECT lead_id, fit_tier, review_status FROM qualified_leads LIMIT 1"
            )
            r = cur.fetchone()
            self.assertEqual(r[0], lid)
            self.assertEqual(r[1], "Tier 1 (Ideal)")
            self.assertEqual(r[2], self.dbmod.REVIEW_STATUS_PENDING)
        finally:
            conn.close()

    def test_log_export(self) -> None:
        self.dbmod.init_db(self.db_path)
        conn = self.dbmod.get_connection(self.db_path)
        try:
            self.dbmod.log_export(conn, 42, "/tmp/out.csv", "test")
            cur = conn.execute("SELECT row_count, output_path, notes FROM exports")
            r = cur.fetchone()
            self.assertEqual(r[0], 42)
            self.assertEqual(r[1], "/tmp/out.csv")
            self.assertEqual(r[2], "test")
        finally:
            conn.close()

    def test_get_all_leads(self) -> None:
        self.dbmod.init_db(self.db_path)
        conn = self.dbmod.get_connection(self.db_path)
        try:
            row = {c: "" for c in self.dbmod.LEADS_COLUMN_NAMES}
            row["place_id"] = "ChIJ3"
            row["city"] = "X"
            self.dbmod.upsert_lead(conn, row)
            rows = self.dbmod.get_all_leads(conn)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["place_id"], "ChIJ3")
        finally:
            conn.close()

    def test_migrate_db_adds_missing_qualified_columns(self) -> None:
        self.dbmod.init_db(self.db_path)
        conn = self.dbmod.get_connection(self.db_path)
        try:
            conn.execute("DROP TABLE qualified_leads")
            conn.execute(
                """
                CREATE TABLE qualified_leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER REFERENCES leads(id),
                    website TEXT,
                    qualified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    fit_tier TEXT,
                    business_name TEXT
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

        self.dbmod.migrate_db(self.db_path)
        conn = self.dbmod.get_connection(self.db_path)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(qualified_leads)").fetchall()}
            self.assertIn("platform", cols)
            self.assertIn("normalized_url", cols)
            for c in (
                "lead_id",
                "website",
                "qualified_at",
                "dedupe_key",
                "fit_tier",
                "review_status",
            ):
                self.assertIn(c, cols)
            for c in self.dbmod.QUALIFIED_ROW_COLUMNS:
                self.assertIn(c, cols)
        finally:
            conn.close()

    def test_init_db_migrates_missing_data_columns_without_drop(self) -> None:
        self.dbmod.init_db(self.db_path)
        conn = self.dbmod.get_connection(self.db_path)
        try:
            conn.execute("DROP TABLE qualified_leads")
            conn.execute(
                """
                CREATE TABLE qualified_leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER REFERENCES leads(id),
                    website TEXT,
                    qualified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    fit_tier TEXT,
                    business_name TEXT
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

        self.dbmod.init_db(self.db_path)
        conn = self.dbmod.get_connection(self.db_path)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(qualified_leads)").fetchall()}
            self.assertIn("platform", cols)
            cur = conn.execute("SELECT COUNT(*) FROM qualified_leads")
            self.assertEqual(cur.fetchone()[0], 0)
        finally:
            conn.close()

    def test_init_db_recreates_incompatible_qualified_leads(self) -> None:
        self.dbmod.init_db(self.db_path)
        conn = self.dbmod.get_connection(self.db_path)
        try:
            conn.execute("DROP TABLE qualified_leads")
            conn.execute("CREATE TABLE qualified_leads (id INTEGER PRIMARY KEY, website TEXT);")
            conn.commit()
        finally:
            conn.close()

        self.dbmod.init_db(self.db_path)
        conn = self.dbmod.get_connection(self.db_path)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(qualified_leads)").fetchall()}
            self.assertIn("dedupe_key", cols)
            self.assertIn("fit_tier", cols)
            self.assertIn("review_status", cols)
        finally:
            conn.close()

    def test_get_qualified_by_tier(self) -> None:
        self.dbmod.init_db(self.db_path)
        conn = self.dbmod.get_connection(self.db_path)
        try:
            qrow = {c: "" for c in self.dbmod.QUALIFIED_ROW_COLUMNS}
            qrow["website"] = "https://a.com"
            qrow["normalized_url"] = "https://a.com/"
            qrow["fit_segment"] = "Strong Fit"
            qrow["business_name"] = "A"
            self.dbmod.upsert_qualified_lead(conn, qrow)
            qrow2 = dict(qrow)
            qrow2["website"] = "https://b.com"
            qrow2["normalized_url"] = "https://b.com/"
            qrow2["fit_segment"] = "Review Manually"
            qrow2["business_name"] = "B"
            self.dbmod.upsert_qualified_lead(conn, qrow2)

            t1 = self.dbmod.get_qualified_by_tier(conn, "Tier 1 (Ideal)")
            self.assertEqual(len(t1), 1)
            self.assertEqual(t1[0]["fit_tier"], "Tier 1 (Ideal)")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
