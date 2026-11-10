"""The dashboard can start a protected, single manual scan."""

import asyncio
import base64
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.domain.instruments import INSTRUMENTS
from app.main import app


class ManualScanApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_origin_button_starts_scan_and_reports_result(self) -> None:
        class FakeStore:
            async def get_worker_health(self):
                return None

        result = {
            "status": "healthy",
            "scanned_instruments": len(INSTRUMENTS),
            "signals_created": 0,
            "duplicate_candidates_suppressed": 0,
            "alerts_sent": 0,
            "alerts_failed": 0,
            "telegram_status": "ready",
            "provider_status": {},
        }
        auth = base64.b64encode(b"dashboard:test-password").decode()
        env = {
            "APP_ENV": "production",
            "DASHBOARD_USERNAME": "dashboard",
            "DASHBOARD_PASSWORD": "test-password",
            "TWELVE_DATA_API_KEY": "test-key",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SECRET_KEY": "sb_secret_test",
        }
        with patch.dict(os.environ, env), patch(
            "app.api.scanner.SupabaseStore.from_environment", return_value=FakeStore()
        ), patch("app.api.scanner.scan_once", new=AsyncMock(return_value=result)) as scan:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                headers = {"Authorization": f"Basic {auth}", "Origin": "http://testserver"}
                started = await client.post("/api/v1/scanner/run", headers=headers, json={})
                self.assertEqual(started.status_code, 202)
                scan_id = started.json()["scan_id"]
                for _ in range(10):
                    state = await client.get(f"/api/v1/scanner/run/{scan_id}", headers=headers)
                    if state.json().get("status") == "completed":
                        break
                    await asyncio.sleep(0.01)

        self.assertEqual(state.json()["status"], "completed")
        self.assertEqual(state.json()["result"]["scanned_instruments"], len(INSTRUMENTS))
        scan.assert_awaited_once()

    async def test_manual_scan_rejects_cross_site_request(self) -> None:
        auth = base64.b64encode(b"dashboard:test-password").decode()
        env = {
            "APP_ENV": "production",
            "DASHBOARD_USERNAME": "dashboard",
            "DASHBOARD_PASSWORD": "test-password",
        }
        with patch.dict(os.environ, env):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.post(
                    "/api/v1/scanner/run",
                    headers={"Authorization": f"Basic {auth}", "Origin": "http://attacker.test"},
                    json={},
                )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
