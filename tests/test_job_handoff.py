"""Tests for durable worker handoff (Phase C)."""

from __future__ import annotations

import json
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


class TestCloudRunJobHandoff(unittest.TestCase):
    def test_build_request_requires_run_spec_payload(self) -> None:
        from src.worker.cloud_run_dispatch import build_run_job_request

        run = validate_run_spec(_sample_run())
        body = build_run_job_request(run)
        env = body["overrides"]["containerOverrides"][0]["env"]
        self.assertEqual(env[0]["name"], "SALES_RUN_SPEC")
        self.assertEqual(json.loads(env[0]["value"])["id"], run["id"])
        self.assertEqual(
            body["overrides"]["containerOverrides"][0]["args"],
            ["-m", "src.worker.run_job"],
        )

    def test_dispatch_posts_run_spec(self) -> None:
        from src.worker import cloud_run_dispatch as dispatch

        run = validate_run_spec(_sample_run())
        fake_response = mock.Mock()
        fake_response.status_code = 200
        fake_response.json.return_value = {"name": "executions/abc"}

        mock_client = mock.Mock()
        mock_client.__enter__ = mock.Mock(return_value=mock_client)
        mock_client.__exit__ = mock.Mock(return_value=False)
        mock_client.post.return_value = fake_response

        with mock.patch.dict(
            os.environ,
            {
                "SALES_CLOUD_RUN_JOB": "corex-sales-pipeline-job-dev",
                "GCP_PROJECT_ID": "corexbiz",
                "GCP_REGION": "us-west1",
            },
            clear=False,
        ):
            with mock.patch.object(dispatch, "_access_token", return_value="tok123"):
                with mock.patch("httpx.Client", return_value=mock_client):
                    result = dispatch.dispatch_cloud_run_job(run)

        self.assertTrue(result["ok"])
        self.assertEqual(result["handoff"], "SALES_RUN_SPEC")
        posted = mock_client.post.call_args.kwargs["json"]
        env_value = posted["overrides"]["containerOverrides"][0]["env"][0]["value"]
        self.assertEqual(json.loads(env_value)["id"], run["id"])


class TestEnqueueJobHandoff(unittest.TestCase):
    def test_subprocess_worker_writes_validated_config(self) -> None:
        from src.worker import enqueue as enqueue_mod

        run = validate_run_spec(_sample_run())
        with mock.patch.object(enqueue_mod.subprocess, "Popen") as popen:
            enqueue_mod._dispatch_subprocess_worker(run)

        popen.assert_called_once()
        argv = popen.call_args[0][0]
        config_index = argv.index("--config") + 1
        config_path = Path(argv[config_index])
        loaded = load_run_spec(config_path=config_path)
        self.assertEqual(loaded["id"], run["id"])
        config_path.unlink(missing_ok=True)

    def test_job_mode_rejects_invalid_spec(self) -> None:
        from src.worker import enqueue as enqueue_mod

        with mock.patch.dict(os.environ, {"SALES_WORKER_MODE": "job"}, clear=False):
            with self.assertRaises(RunSpecError):
                enqueue_mod.enqueue_run({"id": str(uuid.uuid4())})


if __name__ == "__main__":
    unittest.main()
