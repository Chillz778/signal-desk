"""The locally served dashboard stays independent of third-party frontend hosting."""

import unittest
from os import environ
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


class DashboardAppTests(unittest.TestCase):
    def test_dashboard_and_assets_are_served_by_the_api(self) -> None:
        with TestClient(app) as client:
            page = client.get("/")
            script = client.get("/dashboard.js")
            stylesheet = client.get("/styles.css")

        self.assertEqual(page.status_code, 200)
        self.assertIn("Signal Desk", page.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn("runManualScan", script.text)
        self.assertIn('id="run-scan"', page.text)
        self.assertNotIn('id="strategy-rows"', page.text)
        self.assertNotIn('id="validation-form"', page.text)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn("--bg:", stylesheet.text)

    def test_health_route_remains_available(self) -> None:
        with TestClient(app) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_production_dashboard_requires_login(self) -> None:
        settings = {
            "APP_ENV": "production",
            "DASHBOARD_USERNAME": "researcher",
            "DASHBOARD_PASSWORD": "test-only-password",
        }
        with patch.dict(environ, settings):
            with TestClient(app) as client:
                page = client.get("/")
                health = client.get("/health")
                authorized = client.get("/", auth=("researcher", "test-only-password"))

        self.assertEqual(page.status_code, 401)
        self.assertIn("Basic", page.headers.get("www-authenticate", ""))
        self.assertEqual(health.status_code, 200)
        self.assertEqual(authorized.status_code, 200)

    def test_production_without_login_configuration_fails_closed(self) -> None:
        settings = {
            "APP_ENV": "production",
            "DASHBOARD_USERNAME": "",
            "DASHBOARD_PASSWORD": "",
        }
        with patch.dict(environ, settings):
            with TestClient(app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
