"""Cloud Run Job dispatch unit tests."""

from __future__ import annotations

import json
import os
import unittest
import uuid
from unittest import mock

from src.worker import cloud_run_dispatch as dispatch
from src.worker.job_handoff import validate_run_spec


def _run_spec() -> dict:
    run_id = uuid.uuid4()
    return validate_run_spec(
        {
            "id": str(run_id),
            "site_id": "site-a",
            "source_type": "google_maps",
            "criteria": {},
        }
    )


class TestBuildRunJobRequest(unittest.TestCase):
    def test_includes_run_spec_env(self) -> None:
        run_spec = _run_spec()
        body = dispatch.build_run_job_request(run_spec)
        env = body["overrides"]["containerOverrides"][0]["env"]
        self.assertEqual(env[0]["name"], "SALES_RUN_SPEC")
        self.assertEqual(json.loads(env[0]["value"])["id"], run_spec["id"])
        args = body["overrides"]["containerOverrides"][0]["args"]
        self.assertEqual(args, ["-m", "src.worker.run_job"])


class TestCloudRunJobConfigured(unittest.TestCase):
    def test_configured_when_job_name_set(self) -> None:
        with mock.patch.dict(os.environ, {"SALES_CLOUD_RUN_JOB": "my-job"}, clear=False):
            self.assertTrue(dispatch.cloud_run_job_configured())
            self.assertEqual(dispatch.cloud_run_job_name(), "my-job")

    def test_not_configured_when_empty(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("SALES_CLOUD_RUN_JOB", "CLOUD_RUN_JOB_NAME")}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(dispatch.cloud_run_job_configured())


class TestDispatchCloudRunJob(unittest.TestCase):
    def test_posts_run_api_with_token(self) -> None:
        run_spec = _run_spec()
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
                    result = dispatch.dispatch_cloud_run_job(run_spec)

        self.assertTrue(result["ok"])
        self.assertEqual(result["run_id"], run_spec["id"])
        self.assertEqual(result["handoff"], "SALES_RUN_SPEC")
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args.kwargs
        self.assertIn("Authorization", call_kwargs["headers"])
        self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer tok123")
        self.assertIn("corex-sales-pipeline-job-dev:run", call_kwargs.get("url", mock_client.post.call_args[0][0]))

    def test_raises_when_api_errors(self) -> None:
        run_spec = _run_spec()
        fake_response = mock.Mock()
        fake_response.status_code = 403
        fake_response.text = "permission denied"

        mock_client = mock.Mock()
        mock_client.__enter__ = mock.Mock(return_value=mock_client)
        mock_client.__exit__ = mock.Mock(return_value=False)
        mock_client.post.return_value = fake_response

        with mock.patch.dict(
            os.environ,
            {"SALES_CLOUD_RUN_JOB": "job", "GCP_PROJECT_ID": "p"},
            clear=False,
        ):
            with mock.patch.object(dispatch, "_access_token", return_value="tok"):
                with mock.patch("httpx.Client", return_value=mock_client):
                    with self.assertRaises(RuntimeError):
                        dispatch.dispatch_cloud_run_job(run_spec)


class TestEnqueueJobMode(unittest.TestCase):
    def test_job_mode_uses_cloud_run_when_configured(self) -> None:
        from src.worker import enqueue as enqueue_mod

        run = validate_run_spec(_run_spec())
        with mock.patch.object(enqueue_mod, "_dispatch_cloud_run_worker") as cloud_dispatch:
            with mock.patch.object(enqueue_mod, "_dispatch_subprocess_worker") as subprocess_dispatch:
                with mock.patch(
                    "src.worker.cloud_run_dispatch.cloud_run_job_configured",
                    return_value=True,
                ):
                    with mock.patch.dict(os.environ, {"SALES_WORKER_MODE": "job"}, clear=False):
                        enqueue_mod.enqueue_run(run)
                        deadline = time.time() + 2.0
                        while not cloud_dispatch.called and time.time() < deadline:
                            time.sleep(0.01)

        cloud_dispatch.assert_called_once_with(run)
        subprocess_dispatch.assert_not_called()

    def test_job_mode_falls_back_to_subprocess_locally(self) -> None:
        from src.worker import enqueue as enqueue_mod

        run = validate_run_spec(_run_spec())
        with mock.patch.object(enqueue_mod, "_dispatch_cloud_run_worker") as cloud_dispatch:
            with mock.patch.object(enqueue_mod, "_dispatch_subprocess_worker") as subprocess_dispatch:
                with mock.patch(
                    "src.worker.cloud_run_dispatch.cloud_run_job_configured",
                    return_value=False,
                ):
                    with mock.patch.dict(os.environ, {"SALES_WORKER_MODE": "job"}, clear=False):
                        enqueue_mod.enqueue_run(run)
                        deadline = time.time() + 2.0
                        while not subprocess_dispatch.called and time.time() < deadline:
                            time.sleep(0.01)

        subprocess_dispatch.assert_called_once_with(run)
        cloud_dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
