"""Tests for structured logging."""

from __future__ import annotations

import io
import logging
import unittest

from src.log.structured import (
    StructuredFormatter,
    configure_logging,
    format_data,
    log_action,
    sanitize_value,
)


class TestStructuredLogging(unittest.TestCase):
    def test_sanitize_redacts_secrets(self) -> None:
        payload = {"api_token": "secret-value", "list_name": "Test"}
        cleaned = sanitize_value(payload)
        self.assertEqual(cleaned["api_token"], "***")
        self.assertEqual(cleaned["list_name"], "Test")

    def test_format_data_json(self) -> None:
        text = format_data({"source_type": "google_maps", "count": 3})
        self.assertIn("google_maps", text)

    def test_formatter_main_and_trace_lines(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="sales.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="HTTP",
            args=(),
            exc_info=None,
        )
        record.action = "HTTP"
        record.url = "POST /api/v1/runs"
        record.data = {"source_type": "google_maps"}
        record.traces = [(202, "completed in 12ms")]

        output = formatter.format(record)
        self.assertIn("[INFO]", output)
        self.assertIn("[HTTP]", output)
        self.assertIn("POST /api/v1/runs", output)
        self.assertIn("google_maps", output)
        self.assertIn("  - [202] completed in 12ms", output)

    def test_log_action_emits_structured_line(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(StructuredFormatter())
        test_logger = logging.getLogger("sales.test.emit")
        test_logger.handlers = [handler]
        test_logger.propagate = False
        test_logger.setLevel(logging.INFO)

        log_action(
            test_logger,
            logging.INFO,
            "PIPELINE",
            "run/abc",
            {"stage": "finder"},
            traces=[("start", "finder_places.py")],
        )

        line = stream.getvalue().strip()
        self.assertIn("[PIPELINE]", line)
        self.assertIn("run/abc", line)
        self.assertIn("[start] finder_places.py", line)

    def test_configure_logging_idempotent(self) -> None:
        configure_logging(force=True)
        configure_logging()
        root = logging.getLogger()
        self.assertTrue(any(isinstance(h.formatter, StructuredFormatter) for h in root.handlers))


if __name__ == "__main__":
    unittest.main()
