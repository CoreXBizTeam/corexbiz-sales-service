"""Smoke tests: imports, pure helpers, CLI exit codes (no Google API calls)."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run_script(name: str, args: list, *, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / name), *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


class TestImports(unittest.TestCase):
    def test_finder_imports(self) -> None:
        sys.path.insert(0, str(ROOT))
        import finder_places  # noqa: F401

    def test_qualifier_imports(self) -> None:
        sys.path.insert(0, str(ROOT))
        import lead_qualifier  # noqa: F401


class TestFinderHelpers(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT))
        import finder_places as fp

        self.fp = fp

    def test_dedupe_by_place_id(self) -> None:
        rows = [
            {"place_id": "A", "website": "https://x.com", "business_name": "X", "formatted_address": "1"},
            {"place_id": "A", "website": "https://y.com", "business_name": "Y", "formatted_address": "2"},
        ]
        out = self.fp.dedupe_rows(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["place_id"], "A")

    def test_normalize_website_key(self) -> None:
        self.assertEqual(self.fp.normalize_website_key("https://WWW.Example.COM/path"), "example.com")

    def test_merge_keeps_types_from_search(self) -> None:
        place = {"types": ["a", "b"], "place_id": "p1"}
        detail = {"website": "https://z.com", "name": "Z"}
        merged = self.fp._merge_place_and_detail(place, detail)
        self.assertEqual(merged["types"], ["a", "b"])
        self.assertEqual(merged["website"], "https://z.com")

    def test_load_api_key_missing(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": ""}):
            with self.assertRaises(SystemExit) as ctx:
                self.fp.load_api_key()
        self.assertIn("GOOGLE_MAPS_API_KEY", str(ctx.exception))


class TestQualifierHelpers(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT))
        import lead_qualifier as lq

        self.lq = lq

    def test_normalize_url(self) -> None:
        self.assertEqual(self.lq.normalize_url("example.com"), "https://example.com")
        self.assertEqual(self.lq.normalize_url(""), "")

    def test_normalize_canadian_province(self) -> None:
        self.assertEqual(self.lq.normalize_canadian_province("ON"), "ON")
        self.assertEqual(self.lq.normalize_canadian_province("Ontario"), "ON")
        self.assertEqual(self.lq.normalize_canadian_province(""), "")


class TestCLI(unittest.TestCase):
    def test_finder_usage_exit(self) -> None:
        r = _run_script("finder_places.py", [])
        self.assertEqual(r.returncode, 1)
        self.assertIn("Usage", r.stdout)

    def test_qualifier_usage_exit(self) -> None:
        r = _run_script("lead_qualifier.py", [])
        self.assertEqual(r.returncode, 1)
        self.assertIn("Usage", r.stdout)

    def test_finder_missing_api_key(self) -> None:
        env = os.environ.copy()
        env.pop("GOOGLE_MAPS_API_KEY", None)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as out:
            out_path = out.name
        try:
            r = _run_script("finder_places.py", ["cities.csv", out_path], env=env)
            self.assertEqual(r.returncode, 1)
            combined = r.stdout + r.stderr
            self.assertIn("GOOGLE_MAPS_API_KEY", combined)
        finally:
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_qualifier_empty_csv(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8", newline=""
        ) as f:
            f.write("business_name,website\n")
            in_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".out.csv", delete=False) as out:
            out_path = out.name
        try:
            r = _run_script("lead_qualifier.py", [in_path, out_path])
            self.assertEqual(r.returncode, 1)
            self.assertIn("empty", r.stdout.lower())
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_qualifier_missing_website_row(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8", newline=""
        ) as f:
            w = csv.DictWriter(f, fieldnames=["business_name", "website", "city", "province"])
            w.writeheader()
            w.writerow(
                {
                    "business_name": "NoSite",
                    "website": "",
                    "city": "Vancouver",
                    "province": "BC",
                }
            )
            in_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".out.csv", delete=False) as out:
            out_path = out.name
        try:
            r = _run_script("lead_qualifier.py", [in_path, out_path])
            self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
            with open(out_path, encoding="utf-8") as outf:
                rows = list(csv.DictReader(outf))
            self.assertEqual(len(rows), 1)
            self.assertIn("Missing website", rows[0].get("notes", ""))
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)


class TestExportLeadTracker(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT))
        import export_lead_tracker as el

        self.el = el

    def test_tracker_mapping_and_columns(self) -> None:
        row = {
            "business_name": "Test Print",
            "website": "https://example.com",
            "email_found": "hi@example.com",
            "formatted_phone_number": "(555) 111-2222",
            "types": "establishment;store",
            "primary_type": "",
            "ecommerce": "WooCommerce",
            "fit_segment": "Strong Fit: WP without visible upload",
            "notes": "Test Print",
            "address": "1 Main St",
            "city": "Vancouver",
            "province_normalized": "BC",
            "zip": "V6B 1A1",
            "country": "CA",
            "source": "google_places_text_search",
            "priority_score": "85",
            "priority_segment": "P2: WordPress, no upload",
            "platform": "WordPress",
            "reachable": "yes",
            "wordpress_detected": "yes",
            "upload_present": "no",
        }
        out = self.el.enriched_row_to_tracker(row)
        self.assertEqual(out["Company Name"], "Test Print")
        self.assertEqual(out["Website"], "https://example.com")
        self.assertEqual(out["Contact Email"], "hi@example.com")
        self.assertEqual(out["Has E-commerce?"], "Yes")
        self.assertEqual(out["E-Commerce Platform"], "WooCommerce")
        self.assertEqual(out["Website Platform"], "WordPress")
        self.assertEqual(out["Priority Score"], "4 - Urgent")
        self.assertEqual(out["Fit Tier"], "Tier 1 (Ideal)")
        self.assertEqual(out["Business Type"], "Print Shop")
        self.assertEqual(out["Source"], "Outbound")
        self.assertEqual(out["Segment"], "")
        self.assertIn("Company Name", self.el.LEAD_TRACKER_FIELDNAMES)
        self.assertEqual(len(self.el.LEAD_TRACKER_FIELDNAMES), 42)


if __name__ == "__main__":
    unittest.main()
