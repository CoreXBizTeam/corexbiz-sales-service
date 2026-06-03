"""Tests for run lifecycle progress logging."""

from __future__ import annotations

import io
import logging
import os
import unittest
from unittest.mock import patch

from src.log.run_trace import log_run_poll, log_run_progress
from src.log.structured import StructuredFormatter


class TestRunTrace(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = io.StringIO()
        handler = logging.StreamHandler(self.stream)
        handler.setFormatter(StructuredFormatter())
        self.logger = logging.getLogger("sales.run")
        self._saved_handlers = self.logger.handlers[:]
        self._saved_level = self.logger.level
        self._saved_propagate = self.logger.propagate
        self.logger.handlers = [handler]
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

    def tearDown(self) -> None:
        self.logger.handlers = self._saved_handlers
        self.logger.setLevel(self._saved_level)
        self.logger.propagate = self._saved_propagate

    def test_log_run_progress_includes_stage_and_status(self) -> None:
        log_run_progress(
            "abc-123",
            "running",
            source_type="google_maps",
            stage="pipeline",
            traces=[("start", "run_lead_pipeline.py")],
        )
        line = self.stream.getvalue()
        self.assertIn("[RUN]", line)
        self.assertIn("run/abc-123", line)
        self.assertIn("running", line)
        self.assertIn("pipeline", line)
        self.assertIn("[start] run_lead_pipeline.py", line)

    def test_log_run_poll_in_progress_at_info(self) -> None:
        with patch.dict(os.environ, {"SALES_LOG_RUN_POLLS": ""}, clear=False):
            log_run_poll(
                "run-1",
                status="running",
                source_type="google_maps",
                message="Running pipeline…",
                running=True,
            )
        line = self.stream.getvalue()
        self.assertIn("[INFO]", line)
        self.assertIn("poll", line)

    def test_log_run_poll_completed_at_debug_by_default(self) -> None:
        self.logger.setLevel(logging.INFO)
        with patch.dict(os.environ, {"SALES_LOG_RUN_POLLS": ""}, clear=False):
            log_run_poll(
                "run-1",
                status="completed",
                source_type="google_maps",
                running=False,
            )
        self.assertEqual(self.stream.getvalue().strip(), "")

    def test_log_run_poll_completed_verbose_when_enabled(self) -> None:
        with patch.dict(os.environ, {"SALES_LOG_RUN_POLLS": "1"}, clear=False):
            log_run_poll(
                "run-1",
                status="completed",
                source_type="google_maps",
                running=False,
            )
        line = self.stream.getvalue()
        self.assertIn("[INFO]", line)
        self.assertIn("completed", line)


if __name__ == "__main__":
    unittest.main()
