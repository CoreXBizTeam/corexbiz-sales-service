"""Tests for bracket access log formatting."""

from __future__ import annotations

import unittest

from src.log.bracket_access import format_bracket_access_line, level_from_http_status, truncate_snippet


class TestBracketAccess(unittest.TestCase):
    def test_format_accept_line(self) -> None:
        line = format_bracket_access_line(
            iso_time="2026-06-01T12:00:00+00:00",
            level="info",
            action="http_accept",
            method="POST",
            pathname="/api/v1/runs",
            status="--",
            response_snippet="-",
            request_id="abc123",
        )
        self.assertIn("[http_accept] POST /api/v1/runs", line)
        self.assertIn("[--][-]", line)
        self.assertIn("rid=abc123", line)

    def test_format_response_line_includes_snippet(self) -> None:
        line = format_bracket_access_line(
            iso_time="2026-06-01T12:00:01+00:00",
            level="info",
            action="http_respon",
            method="POST",
            pathname="/api/v1/runs",
            status=202,
            response_snippet='{"run_id":"111","status":"queued"}',
            request_id="abc123",
        )
        self.assertIn("[http_respon] POST /api/v1/runs", line)
        self.assertIn("[202]", line)
        self.assertIn("run_id", line)

    def test_truncate_snippet(self) -> None:
        long_json = '{"x":"' + ("a" * 200) + '"}'
        snippet = truncate_snippet(long_json)
        self.assertLess(len(snippet), len(long_json))
        self.assertTrue(snippet.endswith("…"))

    def test_level_from_http_status(self) -> None:
        self.assertEqual(level_from_http_status(202), "info")
        self.assertEqual(level_from_http_status(404), "warn")
        self.assertEqual(level_from_http_status(500), "error")


if __name__ == "__main__":
    unittest.main()
