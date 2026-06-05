"""Tests for durable worker run-spec handoff."""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from src.worker.job_handoff import (
    MAX_RUN_SPEC_BYTES,
    RunSpecError,
    decode_run_spec,
    encode_run_spec,
    load_run_spec,
    validate_run_spec,
    write_run_spec_file,
)


def _sample_run(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "site_id": "site-a",
        "site_url": "https://example.com",
        "list_name": "Test list",
        "source_type": "google_maps",
        "criteria": {"cities_file": "cities.csv"},
        "notes": "",
        "webhook_url": "https://example.com/hook",
    }
    base.update(overrides)
    return base


class TestJobHandoff(unittest.TestCase):
    def test_validate_requires_core_fields(self) -> None:
        with self.assertRaises(RunSpecError):
            validate_run_spec({"id": str(uuid.uuid4())})

    def test_validate_normalizes_criteria(self) -> None:
        run = _sample_run(criteria='{"cities_file":"cities.csv"}')
        normalized = validate_run_spec(run)
        self.assertEqual(normalized["criteria"]["cities_file"], "cities.csv")

    def test_encode_decode_roundtrip(self) -> None:
        run = validate_run_spec(_sample_run())
        raw = encode_run_spec(run)
        restored = decode_run_spec(raw)
        self.assertEqual(restored["id"], run["id"])
        self.assertEqual(restored["site_id"], run["site_id"])

    def test_encode_rejects_oversized_spec(self) -> None:
        run = _sample_run(criteria={"blob": "x" * (MAX_RUN_SPEC_BYTES + 1000)})
        with self.assertRaises(RunSpecError):
            encode_run_spec(run)

    def test_load_from_env(self) -> None:
        run = validate_run_spec(_sample_run())
        with mock.patch.dict(os.environ, {"SALES_RUN_SPEC": encode_run_spec(run)}, clear=False):
            loaded = load_run_spec()
        self.assertEqual(loaded["id"], run["id"])

    def test_load_from_config_file(self) -> None:
        run = validate_run_spec(_sample_run())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            write_run_spec_file(run, path)
            loaded = load_run_spec(config_path=path)
        self.assertEqual(loaded["source_type"], "google_maps")

    def test_load_requires_source(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SALES_RUN_SPEC", None)
            with self.assertRaises(RunSpecError):
                load_run_spec()

    def test_enqueue_rejects_invalid_spec(self) -> None:
        from src.worker import enqueue as enqueue_mod

        with self.assertRaises(RunSpecError):
            enqueue_mod.enqueue_run({"id": str(uuid.uuid4())})


if __name__ == "__main__":
    unittest.main()
