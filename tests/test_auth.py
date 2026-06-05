"""Service token auth tests (mirror corex-share-service v1)."""

from __future__ import annotations

import os
import unittest

from fastapi import HTTPException

from src.api.auth import (
    get_request_token,
    require_api_token,
    site_identity_from_headers,
    verify_api_token,
)


class TestServiceToken(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = os.environ.get("API_TOKEN")
        os.environ["API_TOKEN"] = "secret"

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("API_TOKEN", None)
        else:
            os.environ["API_TOKEN"] = self._prev

    def test_get_request_token_bearer(self) -> None:
        token = get_request_token(
            authorization="Bearer secret", x_api_token=None
        )
        self.assertEqual(token, "secret")

    def test_get_request_token_header(self) -> None:
        token = get_request_token(
            authorization=None, x_api_token="secret"
        )
        self.assertEqual(token, "secret")

    def test_verify_missing_token(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            verify_api_token(None)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail["message"], "missing token")

    def test_verify_invalid_token(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            verify_api_token("wrong")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_verify_accepts_match(self) -> None:
        verify_api_token("secret")

    def test_require_api_token_is_exported(self) -> None:
        self.assertTrue(callable(require_api_token))

    def test_verify_unset_server_token(self) -> None:
        os.environ["API_TOKEN"] = ""
        with self.assertRaises(HTTPException) as ctx:
            verify_api_token("anything")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_site_identity_defaults(self) -> None:
        identity = site_identity_from_headers(
            server_id=None, site_url=None, plugin_version=None
        )
        self.assertEqual(identity.server_id, "dev-server")
        self.assertEqual(identity.site_url, "http://localhost")


if __name__ == "__main__":
    unittest.main()
