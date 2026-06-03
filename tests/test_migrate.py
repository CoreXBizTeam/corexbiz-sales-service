"""Migration loader and Postgres integration tests."""

from __future__ import annotations

import os
import unittest


class TestMigrationLoader(unittest.TestCase):
    def test_load_migration_files_in_order(self) -> None:
        from src.db.migrate import load_migration_files

        migrations = load_migration_files()
        ids = [m[0] for m in migrations]
        self.assertGreaterEqual(len(ids), 6)
        self.assertEqual(ids, sorted(ids))
        self.assertIn("001_create_schema.sql", ids)
        self.assertIn("006_tracker_rows.sql", ids)


class TestPostgresMigrations(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.db.connection import resolve_database_url

        cls.database_url = resolve_database_url()
        if not cls.database_url or os.getenv("SKIP_POSTGRES_TESTS") == "1":
            raise unittest.SkipTest("DATABASE_URL not configured — skipping Postgres tests")

    def setUp(self) -> None:
        from src.db.pool import close_pool

        close_pool()

    def tearDown(self) -> None:
        from src.db.pool import close_pool

        close_pool()

    def test_migrations_idempotent(self) -> None:
        from src.db.migrate import run_migrations

        first = run_migrations()
        second = run_migrations()
        self.assertGreater(len(first["applied"]) + len(first["skipped"]), 0)
        self.assertEqual(second["applied"], [])
        self.assertEqual(len(second["skipped"]), len(first["applied"]) + len(first["skipped"]))


if __name__ == "__main__":
    unittest.main()
