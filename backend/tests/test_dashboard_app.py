"""The locally served dashboard stays independent of third-party frontend hosting."""

import unittest

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
        self.assertIn("loadStrategies", script.text)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn("--bg:", stylesheet.text)

    def test_health_route_remains_available(self) -> None:
        with TestClient(app) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
