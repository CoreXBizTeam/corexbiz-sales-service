"""Tests for wizard criteria → Google Maps finder mapping."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestGoogleMapsCriteria(unittest.TestCase):
    def setUp(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        from src.pipeline import google_maps_criteria as gmc

        self.gmc = gmc

    def test_radius_maps_to_geo_seed_and_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            criteria = {
                "location": {
                    "scope": "radius",
                    "radius_center": "Hastings-Sunrise, Vancouver, BC",
                    "radius_value": 15,
                    "radius_unit": "km",
                },
                "intent": {"keywords": ["print shop", "commercial printer"]},
                "postal_code": "Hastings-Sunrise, Vancouver, BC",
            }
            plan = self.gmc.build_google_maps_finder_plan(
                criteria,
                root=ROOT,
                work_dir=work,
                default_cities=ROOT / "cities.csv",
            )
            self.assertTrue(plan.cities_csv.is_file())
            self.assertEqual(plan.geo_center, "Hastings-Sunrise, Vancouver, BC")
            self.assertEqual(plan.geo_radius_meters, 15_000)
            self.assertFalse(plan.geocode_bias)
            self.assertEqual(
                plan.query_templates,
                [
                    "print shop near Hastings-Sunrise, Vancouver, BC",
                    "commercial printer near Hastings-Sunrise, Vancouver, BC",
                ],
            )

    def test_list_name_used_when_keywords_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            criteria = {
                "location": {
                    "scope": "radius",
                    "radius_center": "Burnaby, BC",
                    "radius_value": 10,
                    "radius_unit": "km",
                },
            }
            plan = self.gmc.build_google_maps_finder_plan(
                criteria,
                root=ROOT,
                work_dir=Path(tmp),
                default_cities=ROOT / "cities.csv",
                list_name="coffee shop",
            )
            self.assertEqual(plan.query_templates, ["coffee shop near Burnaby, BC"])

    def test_quick_discovery_uses_default_finder_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            criteria = {"cities_file": "cities.csv", "list_name": "Quick discovery"}
            plan = self.gmc.build_google_maps_finder_plan(
                criteria,
                root=ROOT,
                work_dir=Path(tmp),
                default_cities=ROOT / "cities.csv",
                list_name="Quick discovery",
            )
            self.assertEqual(plan.cities_csv, ROOT / "cities.csv")
            self.assertIsNone(plan.geo_center)
            self.assertFalse(plan.geocode_bias)
            self.assertEqual(plan.query_templates, [])

    def test_default_falls_back_to_cities_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            criteria = {"provinces": ["BC"]}
            plan = self.gmc.build_google_maps_finder_plan(
                criteria,
                root=ROOT,
                work_dir=Path(tmp),
                default_cities=ROOT / "cities.csv",
            )
            self.assertEqual(plan.cities_csv, ROOT / "cities.csv")
            self.assertIsNone(plan.geo_center)
            self.assertEqual(plan.query_templates, [])

    def test_write_queries_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.json"
            self.gmc.write_queries_json(path, ["a", "b"])
            self.assertEqual(json.loads(path.read_text()), ["a", "b"])


class TestLoadProjectEnv(unittest.TestCase):
    def test_loads_key_from_dotenv_over_empty_shell(self) -> None:
        import os
        import sys
        import unittest.mock

        sys.path.insert(0, str(ROOT))
        from src.config import env as env_mod

        env_mod._env_loaded = False
        with unittest.mock.patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": ""}, clear=False):
            env_mod.load_project_env()
            self.assertTrue(env_mod.google_maps_configured(), "expected .env key to override empty export")


if __name__ == "__main__":
    unittest.main()
