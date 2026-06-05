"""Webhook signing and dispatch tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import unittest
from unittest import mock

from src.lib.sales_run_webhook_sign import (
    REQUEST_PATH,
    derive_secret,
    sign_payload,
)
from src.worker.webhook import (
    build_run_webhook_body,
    dispatch_run_webhook,
    resolve_webhook_url,
)


class TestWebhookSigning(unittest.TestCase):
    def test_derive_secret_matches_php_formula(self) -> None:
        secret = derive_secret("test-webhook-secret", "site-abc")
        expected = hashlib.sha256(
            b"corexbiz-sales-run:test-webhook-secret:site-abc"
        ).hexdigest()
        self.assertEqual(secret, expected)

    def test_sign_payload_vector(self) -> None:
        body = (
            '{"event":"run.completed","run_id":"00000000-0000-0000-0000-000000000001",'
            '"site_id":"site-abc","status":"completed"}'
        )
        headers = sign_payload(
            "test-webhook-secret",
            server_id="site-abc",
            raw_body=body,
            timestamp_sec=1_700_000_000,
        )
        secret_hex = derive_secret("test-webhook-secret", "site-abc")
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        payload = f"1700000000\nsite-abc\n{REQUEST_PATH}\n{body_hash}"
        expected_sig = hmac.new(
            secret_hex.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        self.assertEqual(headers["X-Corexbiz-Signature"], expected_sig)


class TestWebhookDispatch(unittest.TestCase):
    def test_skips_when_no_url(self) -> None:
        ok = dispatch_run_webhook({"id": "x", "site_id": "s"}, event="run.completed")
        self.assertFalse(ok)

    def test_skips_when_no_secret(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "WEBHOOK_SIGNING_SECRET": "",
                "COREX_SALES_SERVICE_ENV": "production",
            },
            clear=False,
        ):
            ok = dispatch_run_webhook(
                {
                    "id": "x",
                    "site_id": "s",
                    "webhook_url": "http://example.com/hook",
                    "status": "completed",
                },
                event="run.completed",
            )
        self.assertFalse(ok)

    def test_posts_signed_payload(self) -> None:
        run = {
            "id": "00000000-0000-0000-0000-000000000001",
            "site_id": "site-abc",
            "webhook_url": "http://example.com/hook",
            "status": "completed",
            "source_type": "manual_csv",
        }
        captured: dict = {}

        class FakeResponse:
            status_code = 200
            text = '{"ok":true}'

        fake_client = mock.Mock()
        fake_client.post = mock.Mock(
            side_effect=lambda url, *, content, headers: (
                captured.update({"url": url, "content": content, "headers": headers})
                or FakeResponse()
            )
        )
        fake_client.__enter__ = mock.Mock(return_value=fake_client)
        fake_client.__exit__ = mock.Mock(return_value=False)

        with mock.patch.dict(
            os.environ,
            {
                "WEBHOOK_SIGNING_SECRET": "test-webhook-secret",
                "COREX_SALES_SERVICE_ENV": "production",
            },
            clear=False,
        ):
            with mock.patch("httpx.Client", return_value=fake_client):
                ok = dispatch_run_webhook(run, event="run.completed", qualified_count=3)

        self.assertTrue(ok)
        self.assertEqual(captured["url"], "http://example.com/hook")
        body = json.loads(captured["content"])
        self.assertEqual(body["event"], "run.completed")
        self.assertEqual(body["qualified_count"], 3)
        self.assertIn("X-Corexbiz-Signature", captured["headers"])


class TestWebhookBody(unittest.TestCase):
    def test_build_run_webhook_body(self) -> None:
        body = build_run_webhook_body(
            {
                "id": "abc",
                "site_id": "site-1",
                "status": "completed",
                "list_name": "Test",
                "source_type": "manual_csv",
            },
            event="run.completed",
            qualified_count=2,
        )
        self.assertEqual(body["run_id"], "abc")
        self.assertEqual(body["qualified_count"], 2)


class TestWebhookUrlResolution(unittest.TestCase):
    def test_local_prefers_sales_site_url_over_stored_webhook(self) -> None:
        run = {
            "id": "run-1",
            "webhook_url": "https://dead-tunnel.trycloudflare.com/wp-json/corexbiz/v1/sales/run-webhook",
            "site_url": "https://dead-tunnel.trycloudflare.com",
        }
        with mock.patch.dict(
            os.environ,
            {
                "COREX_SALES_SERVICE_ENV": "local",
                "SALES_SITE_URL": "https://live-tunnel.trycloudflare.com",
            },
            clear=False,
        ):
            url = resolve_webhook_url(run)
        self.assertEqual(
            url,
            "https://live-tunnel.trycloudflare.com/wp-json/corexbiz/v1/sales/run-webhook",
        )

    def test_production_uses_stored_webhook_url(self) -> None:
        run = {
            "webhook_url": "https://shop.example.com/wp-json/corexbiz/v1/sales/run-webhook",
        }
        with mock.patch.dict(
            os.environ,
            {
                "COREX_SALES_SERVICE_ENV": "production",
                "SALES_SITE_URL": "https://other.example.com",
            },
            clear=False,
        ):
            url = resolve_webhook_url(run)
        self.assertEqual(
            url,
            "https://shop.example.com/wp-json/corexbiz/v1/sales/run-webhook",
        )


if __name__ == "__main__":
    unittest.main()
